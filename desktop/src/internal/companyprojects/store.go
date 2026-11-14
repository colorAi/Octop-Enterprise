// Package companyprojects persists native project choices outside project files.
// The caller supplies an account-scoped filename and only registers paths chosen
// in the native folder dialog. This store is not exposed as a web path API.
package companyprojects

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"sync"
)

type Project struct {
	ID            string   `json:"id"`
	Name          string   `json:"name"`
	Path          string   `json:"path"`
	Authorized    bool     `json:"authorized"`
	Revision      int      `json:"revision"`
	Legacy        bool     `json:"legacy,omitempty"`
	LastExpert    string   `json:"last_expert,omitempty"`
	LastThread    string   `json:"last_thread,omitempty"`
	Hidden        bool     `json:"hidden,omitempty"`
	PreviousPaths []string `json:"previous_paths,omitempty"`
	SyncPending   bool     `json:"sync_pending,omitempty"`
}

type Catalog struct {
	Version  int                `json:"version"`
	Current  string             `json:"current_project_id"`
	Projects map[string]Project `json:"projects"`
	Threads  map[string]string  `json:"threads"`
}

type Store struct {
	mu       sync.Mutex
	filename string
	data     Catalog
}

func Open(filename string) (*Store, error) {
	filename, err := filepath.Abs(filename)
	if err != nil {
		return nil, err
	}
	s := &Store{filename: filename, data: Catalog{Version: 1, Projects: map[string]Project{}, Threads: map[string]string{}}}
	data, err := os.ReadFile(filename)
	if errors.Is(err, os.ErrNotExist) {
		return s, nil
	}
	if err != nil {
		return nil, err
	}
	if json.Unmarshal(data, &s.data) != nil || s.data.Version != 1 || s.data.Projects == nil || s.data.Threads == nil {
		return nil, errors.New("project_catalog_invalid")
	}
	for id, p := range s.data.Projects {
		if id == "" || p.ID != id || !filepath.IsAbs(p.Path) || p.Revision < 1 {
			return nil, errors.New("project_catalog_invalid")
		}
	}
	for _, id := range s.data.Threads {
		if _, ok := s.data.Projects[id]; !ok {
			return nil, errors.New("project_catalog_invalid")
		}
	}
	if s.data.Current != "" {
		if _, ok := s.data.Projects[s.data.Current]; !ok {
			return nil, errors.New("project_catalog_invalid")
		}
	}
	return s, nil
}

func clone(c Catalog) Catalog {
	n := Catalog{Version: c.Version, Current: c.Current, Projects: map[string]Project{}, Threads: map[string]string{}}
	for k, v := range c.Projects {
		v.PreviousPaths = append([]string(nil), v.PreviousPaths...)
		n.Projects[k] = v
	}
	for k, v := range c.Threads {
		n.Threads[k] = v
	}
	return n
}

func (s *Store) Snapshot() Catalog {
	s.mu.Lock()
	defer s.mu.Unlock()
	return clone(s.data)
}

// save commits memory only after the complete replacement has reached disk.
func (s *Store) save(next Catalog) error {
	data, err := json.Marshal(next)
	if err != nil {
		return err
	}
	if err = os.MkdirAll(filepath.Dir(s.filename), 0700); err != nil {
		return err
	}
	f, err := os.CreateTemp(filepath.Dir(s.filename), ".projects-*")
	if err != nil {
		return err
	}
	defer os.Remove(f.Name())
	defer f.Close()
	if _, err = f.Write(data); err != nil {
		return err
	}
	if err = f.Sync(); err != nil {
		return err
	}
	if err = f.Close(); err != nil {
		return err
	}
	if err = os.Rename(f.Name(), s.filename); err != nil {
		return err
	}
	s.data = next
	return nil
}

func contains(root, path string) bool {
	rel, err := filepath.Rel(root, path)
	return err == nil && rel != ".." && !strings.HasPrefix(rel, ".."+string(os.PathSeparator))
}

func canonical(path string) (string, error) {
	path, err := filepath.Abs(path)
	if err != nil {
		return "", err
	}
	return filepath.EvalSymlinks(path)
}

// Resolve existing ancestors without creating a directory inside a rejected project.
func canonicalDestination(path string) (string, error) {
	resolved, err := canonical(path)
	if !errors.Is(err, os.ErrNotExist) {
		return resolved, err
	}
	parent := filepath.Dir(path)
	if parent == path {
		return "", err
	}
	resolved, err = canonicalDestination(parent)
	if err != nil {
		return "", err
	}
	return filepath.Join(resolved, filepath.Base(path)), nil
}

func sameDirectory(a, b string) bool {
	if a == b {
		return true
	}
	ai, err := os.Stat(a)
	if err != nil {
		return false
	}
	bi, err := os.Stat(b)
	return err == nil && os.SameFile(ai, bi)
}

func (s *Store) add(next *Catalog, path string, authorized, legacy bool) (Project, error) {
	for _, p := range next.Projects {
		if sameDirectory(p.Path, path) {
			if authorized && (!p.Authorized || p.Path != path) {
				p.Authorized = true
				p.Path = path
				p.Revision++
				next.Projects[p.ID] = p
			}
			return p, nil
		}
	}
	var bytes [16]byte
	if _, err := rand.Read(bytes[:]); err != nil {
		return Project{}, err
	}
	id := "prj_" + hex.EncodeToString(bytes[:])
	p := Project{ID: id, Name: filepath.Base(path), Path: path, Authorized: authorized, Revision: 1, Legacy: legacy}
	next.Projects[id] = p
	return p, nil
}

func (s *Store) authorizedRoot(path string) (string, error) {
	root, err := canonical(path)
	if err != nil {
		return "", err
	}
	info, err := os.Stat(root)
	if err != nil || !info.IsDir() {
		return "", errors.New("directory_unavailable")
	}
	// Resolve the catalog's existing parent so a symlink cannot hide app data.
	state, err := canonicalDestination(filepath.Dir(s.filename))
	if err != nil {
		return "", err
	}
	if contains(root, state) || contains(state, root) {
		return "", errors.New("runtime_data_must_be_outside_project")
	}
	return root, nil
}

// Register is called only after native directory selection and consent.
func (s *Store) Register(path string) (Project, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	root, err := s.authorizedRoot(path)
	if err != nil {
		return Project{}, err
	}
	next := clone(s.data)
	p, err := s.add(&next, root, true, false)
	if err != nil {
		return Project{}, err
	}
	p.Hidden = false
	next.Projects[p.ID] = p
	next.Current = p.ID
	if err = s.save(next); err != nil {
		return Project{}, err
	}
	return p, nil
}

func (s *Store) Select(id string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if _, ok := s.data.Projects[id]; id != "" && !ok {
		return errors.New("project_not_found")
	}
	next := clone(s.data)
	next.Current = id
	return s.save(next)
}

func (s *Store) grantAccountProject(next *Catalog, project Project, path string) error {
	root, err := s.authorizedRoot(path)
	if err != nil {
		return err
	}
	if old, ok := next.Projects[project.ID]; ok {
		if !sameDirectory(old.Path, root) {
			return errors.New("project_archive_project_exists")
		}
		if old.Path == root && old.Authorized && !old.Hidden {
			return nil
		}
		project = old
	}
	for id, other := range next.Projects {
		if id != project.ID && sameDirectory(other.Path, root) {
			return errors.New("project_archive_project_exists")
		}
	}
	if project.Path != "" && project.Path != root {
		project.PreviousPaths = append(project.PreviousPaths, project.Path)
	}
	project.Path = root
	project.Authorized = true
	project.Hidden = false
	project.Legacy = false
	project.Revision++
	project.SyncPending = true
	next.Projects[project.ID] = project
	return nil
}

// ImportCatalog grants the extracted directories. Conversation bindings are
// fetched separately from the account's current server state.
func (s *Store) ImportCatalog(directory string, manifest ArchiveManifest) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	next := clone(s.data)
	for _, project := range manifest.Projects {
		project.LastThread = ""
		if err := s.grantAccountProject(&next, project, filepath.Join(directory, "projects", project.ID)); err != nil {
			return err
		}
	}
	next.Current = manifest.Projects[0].ID
	return s.save(next)
}

func (s *Store) Attach(id, name, directory string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	next := clone(s.data)
	if err := s.grantAccountProject(&next, Project{ID: id, Name: name}, directory); err != nil {
		return err
	}
	next.Current = id
	return s.save(next)
}

func (s *Store) SyncConversations(expected Project, name string, threads []string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	project, err := checkedProject(s.data.Projects[expected.ID])
	if err != nil {
		return err
	}
	if project.Path != expected.Path || project.Revision != expected.Revision {
		return errors.New("project_directory_changed")
	}
	next := clone(s.data)
	for _, thread := range threads {
		if old := next.Threads[thread]; old != "" && old != project.ID {
			return errors.New("thread_project_conflict")
		}
		next.Threads[thread] = project.ID
	}
	if project.Name == expected.Name {
		project.Name = name
	}
	project.SyncPending = false
	next.Projects[project.ID] = project
	return s.save(next)
}

// ResolveImportedPath checks current paths using native rules and remaps old
// project roots using portable paths. Unmatched paths reach the caller's scope check.
func (s *Store) ResolveImportedPath(original string) (string, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	native := filepath.ToSlash(original)
	portable := strings.ReplaceAll(original, "\\", "/")
	var selected Project
	var relative string
	best := 0
	bestCurrent, ambiguous := false, false
	for _, p := range s.data.Projects {
		for index, prefix := range append([]string{p.Path}, p.PreviousPaths...) {
			current := index == 0
			candidate := portable
			if current {
				candidate, prefix = native, filepath.ToSlash(prefix)
			} else {
				prefix = strings.ReplaceAll(prefix, "\\", "/")
			}
			prefix = strings.TrimRight(prefix, "/")
			if len(candidate) < len(prefix) {
				continue
			}
			head, rest := candidate[:len(prefix)], candidate[len(prefix):]
			windows := (len(prefix) > 1 && prefix[1] == ':') || strings.HasPrefix(prefix, "//")
			if head != prefix && !(windows && strings.EqualFold(head, prefix)) {
				continue
			}
			if rest != "" && !strings.HasPrefix(rest, "/") {
				continue
			}
			if len(prefix) == best && current == bestCurrent && p.ID != selected.ID {
				ambiguous = true
			}
			if len(prefix) > best || (len(prefix) == best && current && !bestCurrent) {
				selected = p
				relative = strings.TrimPrefix(rest, "/")
				best = len(prefix)
				bestCurrent, ambiguous = current, false
			}
		}
	}
	if best == 0 {
		return original, nil
	}
	if ambiguous {
		return "", errors.New("project_path_ambiguous")
	}
	if _, err := checkedProject(selected); err != nil {
		return "", err
	}
	if relative == "" {
		return selected.Path, nil
	}
	rel := filepath.FromSlash(relative)
	if bestCurrent {
		if rel == "." || !filepath.IsLocal(rel) || filepath.Clean(rel) != rel || strings.ContainsRune(rel, 0) {
			return "", errors.New("project_path_invalid")
		}
	} else if !portableArchivePath(relative) {
		return "", errors.New("project_path_invalid")
	}
	return filepath.Join(selected.Path, rel), nil
}

// Bind preserves a conversation's project even when the active project changes.
func (s *Store) Bind(thread, project string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if thread == "" {
		return errors.New("thread_required")
	}
	if _, ok := s.data.Projects[project]; !ok {
		return errors.New("project_not_found")
	}
	if old, ok := s.data.Threads[thread]; ok {
		if old != project {
			return errors.New("thread_project_conflict")
		}
		return nil
	}
	next := clone(s.data)
	next.Threads[thread] = project
	return s.save(next)
}

// ForThread never substitutes Current when a mapping or directory is missing.
func (s *Store) ForThread(thread string) (Project, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	return checkedProject(s.data.Projects[s.data.Threads[thread]])
}

func (s *Store) ForID(id string) (Project, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	return checkedProject(s.data.Projects[id])
}

func checkedProject(p Project) (Project, error) {
	ok := p.ID != ""
	if !ok {
		return Project{}, errors.New("thread_project_missing")
	}
	if !p.Authorized {
		return Project{}, errors.New("project_authorization_required")
	}
	resolved, err := canonical(p.Path)
	if err != nil || resolved != p.Path {
		return Project{}, errors.New("project_directory_changed")
	}
	info, err := os.Stat(resolved)
	if err != nil || !info.IsDir() {
		return Project{}, errors.New("directory_unavailable")
	}
	return p, nil
}

// ImportLegacy records the exact path from an existing local task receipt. It
// never creates a directory or grants permission; reauthorization is explicit.
func (s *Store) ImportLegacy(thread, path string) (Project, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if thread == "" || !filepath.IsAbs(path) {
		return Project{}, errors.New("legacy_project_invalid")
	}
	root, err := canonicalDestination(path)
	if err != nil {
		return Project{}, err
	}
	if id, ok := s.data.Threads[thread]; ok {
		p := s.data.Projects[id]
		matches := sameDirectory(p.Path, root)
		for _, previous := range p.PreviousPaths {
			if sameDirectory(previous, root) {
				matches = true
			}
		}
		if !matches {
			return Project{}, errors.New("thread_project_conflict")
		}
		return p, nil
	}
	next := clone(s.data)
	p, err := s.add(&next, root, false, true)
	if err != nil {
		return Project{}, err
	}
	next.Threads[thread] = p.ID
	if err = s.save(next); err != nil {
		return Project{}, err
	}
	return p, nil
}

func (s *Store) RememberExpert(id, expert string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	p, ok := s.data.Projects[id]
	if !ok {
		return errors.New("project_not_found")
	}
	if p.LastExpert == expert {
		return nil
	}
	next := clone(s.data)
	p.LastExpert = expert
	next.Projects[id] = p
	return s.save(next)
}

func (s *Store) RememberThread(thread string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	p, ok := s.data.Projects[s.data.Threads[thread]]
	if !ok {
		return errors.New("thread_project_missing")
	}
	if p.LastThread == thread {
		return nil
	}
	next := clone(s.data)
	p.LastThread = thread
	next.Projects[p.ID] = p
	return s.save(next)
}
func (s *Store) Update(id, name string, hide, revoke bool) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	p, ok := s.data.Projects[id]
	if !ok {
		return errors.New("project_not_found")
	}
	next := clone(s.data)
	if name != "" {
		p.Name = name
	}
	if hide {
		p.Hidden = true
		if next.Current == id {
			next.Current = ""
		}
	}
	if revoke {
		p.Authorized = false
		p.Revision++
	}
	next.Projects[id] = p
	return s.save(next)
}

// Relocate follows explicit native selection; the caller first excludes all
// active/unknown work on the old and new roots. Files themselves are not moved.
func (s *Store) Relocate(id, path string) (Project, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	p, ok := s.data.Projects[id]
	if !ok {
		return Project{}, errors.New("project_not_found")
	}
	root, err := s.authorizedRoot(path)
	if err != nil {
		return Project{}, err
	}
	for other, project := range s.data.Projects {
		if other != id && sameDirectory(project.Path, root) {
			return Project{}, errors.New("这个文件夹已属于另一个项目，请直接切换到该项目")
		}
	}
	next := clone(s.data)
	p = next.Projects[id]
	if p.Path != root {
		p.PreviousPaths = append(p.PreviousPaths, p.Path)
	}
	p.Path = root
	p.Authorized = true
	p.Revision++
	p.Hidden = false
	next.Projects[id] = p
	next.Current = id
	if err = s.save(next); err != nil {
		return Project{}, err
	}
	return p, nil
}
