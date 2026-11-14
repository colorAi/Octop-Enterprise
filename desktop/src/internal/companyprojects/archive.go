package companyprojects

import (
	"archive/zip"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"os"
	"path"
	"path/filepath"
	"regexp"
	"slices"
	"strings"
)

const archiveFormat = 2
const maxArchiveFiles = 200000
const maxArchiveBytes int64 = 100 << 30
const maxManifestBytes = 32 << 20

var archiveID = regexp.MustCompile(`^[A-Za-z0-9_-]{1,128}$`)

type ArchiveEntry struct {
	Path      string `json:"path"`
	Size      int64  `json:"size"`
	SHA256    string `json:"sha256,omitempty"`
	Directory bool   `json:"directory,omitempty"`
	Mode      uint32 `json:"mode"`
}
type ArchiveManifest struct {
	Version  int            `json:"version"`
	ID       string         `json:"archive_id"`
	Server   string         `json:"server"`
	Owner    int64          `json:"owner"`
	Projects []Project      `json:"projects"`
	Entries  []ArchiveEntry `json:"entries"`
}

func portableArchivePath(name string) bool {
	if name == "" || name != path.Clean(name) || strings.HasPrefix(name, "/") || strings.ContainsAny(name, "\\:\x00") {
		return false
	}
	for _, part := range strings.Split(name, "/") {
		if part == ".." || part == "." || strings.TrimRight(part, ". ") != part || strings.ContainsAny(part, `<>"|?*`) {
			return false
		}
		stem := strings.ToUpper(strings.SplitN(part, ".", 2)[0])
		if stem == "CON" || stem == "PRN" || stem == "AUX" || stem == "NUL" || (len(stem) == 4 && (strings.HasPrefix(stem, "COM") || strings.HasPrefix(stem, "LPT")) && stem[3] >= '1' && stem[3] <= '9') {
			return false
		}
	}
	return true
}

type contextReader struct {
	ctx context.Context
	io.Reader
}

func (r contextReader) Read(p []byte) (int, error) {
	if err := r.ctx.Err(); err != nil {
		return 0, err
	}
	return r.Reader.Read(p)
}

func archiveSource(root *os.Root, rel string) (string, os.FileInfo, error) {
	info, err := root.Stat(rel)
	if err == nil {
		return rel, info, nil
	}
	// An absolute link can target a file inside the project. Resolve it to a
	// relative name, then use the root handle again for stat and content reads.
	target, resolveErr := filepath.EvalSymlinks(filepath.Join(root.Name(), rel))
	if resolveErr != nil {
		return "", nil, err
	}
	source, relErr := filepath.Rel(root.Name(), target)
	if relErr != nil || !filepath.IsLocal(source) {
		return "", nil, err
	}
	info, err = root.Stat(source)
	return source, info, err
}

// ExportArchive writes a new archive and materializes links within each project.
func ExportArchive(ctx context.Context, filename string, manifest ArchiveManifest) error {
	f, err := os.OpenFile(filename, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	if err != nil {
		return err
	}
	ok := false
	defer func() {
		// Windows cannot remove the incomplete archive while its file is open.
		_ = f.Close()
		if !ok {
			_ = os.Remove(filename)
		}
	}()
	z := zip.NewWriter(f)
	defer z.Close()
	manifest.Version = archiveFormat
	manifest.Entries = nil
	manifest.Projects = slices.Clone(manifest.Projects)
	seen := map[string]bool{}
	var total int64
	for i, project := range manifest.Projects {
		manifest.Projects[i].LastThread = ""
		if !archiveID.MatchString(project.ID) {
			return errors.New("project_archive_invalid")
		}
		rootPath, err := canonical(project.Path)
		if err != nil {
			return err
		}
		root, err := os.OpenRoot(rootPath)
		if err != nil {
			return err
		}
		var walk func(string, string, []os.FileInfo) error
		walk = func(rel, source string, parents []os.FileInfo) error {
			if err := ctx.Err(); err != nil {
				return err
			}
			source, info, err := archiveSource(root, source)
			if err != nil {
				return fmt.Errorf("project_archive_path: %s: %w", filepath.Join(project.Path, rel), err)
			}
			name := path.Join("projects", project.ID, filepath.ToSlash(rel))
			key := strings.ToLower(name)
			if !portableArchivePath(name) || seen[key] || len(seen) >= maxArchiveFiles || len(parents) > 64 {
				return fmt.Errorf("project_archive_path: %s", name)
			}
			seen[key] = true
			entry := ArchiveEntry{Path: name, Mode: uint32(info.Mode().Perm()), Directory: info.IsDir()}
			if info.IsDir() {
				for _, p := range parents {
					if os.SameFile(info, p) {
						return fmt.Errorf("project_archive_link_cycle: %s", name)
					}
				}
				manifest.Entries = append(manifest.Entries, entry)
				dir, err := root.Open(source)
				if err != nil {
					return err
				}
				children, err := dir.ReadDir(-1)
				_ = dir.Close()
				if err != nil {
					return err
				}
				for _, child := range children {
					if err := walk(filepath.Join(rel, child.Name()), filepath.Join(source, child.Name()), append(parents, info)); err != nil {
						return err
					}
				}
				return nil
			}
			if !info.Mode().IsRegular() {
				return fmt.Errorf("project_archive_special_file: %s", name)
			}
			file, err := root.Open(source)
			if err != nil {
				return err
			}
			defer file.Close()
			before, err := file.Stat()
			if err != nil {
				return err
			}
			header := &zip.FileHeader{Name: name, Method: zip.Deflate}
			header.SetMode(before.Mode().Perm())
			header.Modified = before.ModTime()
			out, err := z.CreateHeader(header)
			if err != nil {
				return err
			}
			hash := sha256.New()
			n, err := io.Copy(io.MultiWriter(out, hash), contextReader{ctx, io.LimitReader(file, maxArchiveBytes-total+1)})
			total += n
			if err != nil {
				return err
			}
			if total > maxArchiveBytes {
				return errors.New("project_archive_too_large")
			}
			after, err := file.Stat()
			if err != nil {
				return err
			}
			if n != before.Size() || before.Size() != after.Size() || !before.ModTime().Equal(after.ModTime()) {
				return fmt.Errorf("project_archive_file_changed: %s", name)
			}
			entry.Size, entry.SHA256 = n, hex.EncodeToString(hash.Sum(nil))
			manifest.Entries = append(manifest.Entries, entry)
			return nil
		}
		err = walk(".", ".", nil)
		_ = root.Close()
		if err != nil {
			return err
		}
	}
	data, err := json.Marshal(manifest)
	if err != nil {
		return err
	}
	if len(data) > maxManifestBytes {
		return errors.New("project_archive_too_large")
	}
	out, err := z.Create("manifest.json")
	if err != nil {
		return err
	}
	if _, err = out.Write(data); err != nil {
		return err
	}
	if err = z.Close(); err != nil {
		return err
	}
	if err = f.Sync(); err != nil {
		return err
	}
	if err = f.Close(); err != nil {
		return err
	}
	ok = true
	return nil
}

func ReadArchive(filename string) (ArchiveManifest, error) {
	var m ArchiveManifest
	z, err := zip.OpenReader(filename)
	if err != nil {
		return m, err
	}
	defer z.Close()
	var found bool
	for _, f := range z.File {
		if f.Name != "manifest.json" {
			continue
		}
		if found || f.UncompressedSize64 > maxManifestBytes {
			return m, errors.New("project_archive_invalid")
		}
		found = true
		r, err := f.Open()
		if err != nil {
			return m, err
		}
		data, err := io.ReadAll(io.LimitReader(r, maxManifestBytes+1))
		_ = r.Close()
		if err != nil {
			return m, err
		}
		var saved struct {
			ArchiveManifest
			Snapshot struct {
				ID string `json:"transfer_id"`
			} `json:"snapshot"`
		}
		if len(data) > maxManifestBytes || json.Unmarshal(data, &saved) != nil {
			return m, errors.New("project_archive_invalid")
		}
		m = saved.ArchiveManifest
		if m.Version == 1 {
			m.ID = saved.Snapshot.ID
		}
	}
	if !found || (m.Version != 1 && m.Version != archiveFormat) || !archiveID.MatchString(m.ID) || len(m.Projects) == 0 || len(m.Projects) > 100 || len(m.Entries) > maxArchiveFiles {
		return m, errors.New("project_archive_invalid")
	}
	projects := map[string]bool{}
	for _, p := range m.Projects {
		if !archiveID.MatchString(p.ID) || projects[p.ID] || p.Path == "" || p.Revision < 1 || p.Revision == math.MaxInt {
			return m, errors.New("project_archive_invalid")
		}
		projects[p.ID] = true
	}
	entries := map[string]ArchiveEntry{}
	folded := map[string]bool{}
	var size int64
	for _, e := range m.Entries {
		parts := strings.Split(e.Path, "/")
		key := strings.ToLower(e.Path)
		if !portableArchivePath(e.Path) || len(parts) < 2 || parts[0] != "projects" || !projects[parts[1]] || folded[key] || e.Size < 0 || (!e.Directory && len(e.SHA256) != 64) {
			return m, errors.New("project_archive_invalid")
		}
		folded[key] = true
		entries[e.Path] = e
		if e.Size > maxArchiveBytes-size {
			return m, errors.New("project_archive_too_large")
		}
		size += e.Size
	}
	for id := range projects {
		if root, ok := entries["projects/"+id]; !ok || !root.Directory {
			return m, errors.New("project_archive_invalid")
		}
	}
	seen := map[string]bool{}
	for _, f := range z.File {
		if f.Name == "manifest.json" {
			continue
		}
		e, ok := entries[f.Name]
		if !ok || e.Directory || seen[f.Name] || !f.Mode().IsRegular() || f.UncompressedSize64 != uint64(e.Size) {
			return m, errors.New("project_archive_invalid")
		}
		seen[f.Name] = true
	}
	for _, e := range m.Entries {
		if !e.Directory && !seen[e.Path] {
			return m, errors.New("project_archive_invalid")
		}
	}
	return m, nil
}

// ExtractArchive never merges with an existing project. A verified extraction
// can be reused after an interrupted import, without overwriting newer files.
func ExtractArchive(ctx context.Context, filename, parent string, m ArchiveManifest) (string, error) {
	destination := filepath.Join(parent, "octop-migration-"+m.ID)
	if info, err := os.Lstat(destination); err == nil {
		if !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
			return "", errors.New("project_archive_destination_exists")
		}
		if err := verifyExtracted(ctx, destination, m); err != nil {
			return "", err
		}
		return destination, nil
	} else if !os.IsNotExist(err) {
		return "", err
	}
	staging, err := os.MkdirTemp(parent, ".octop-import-")
	if err != nil {
		return "", err
	}
	defer os.RemoveAll(staging)
	root, err := os.OpenRoot(staging)
	if err != nil {
		return "", err
	}
	defer root.Close()
	z, err := zip.OpenReader(filename)
	if err != nil {
		return "", err
	}
	defer z.Close()
	files := map[string]*zip.File{}
	for _, f := range z.File {
		files[f.Name] = f
	}
	for _, e := range m.Entries {
		if err := ctx.Err(); err != nil {
			return "", err
		}
		name := filepath.FromSlash(e.Path)
		if e.Directory {
			if err := root.MkdirAll(name, 0700); err != nil {
				return "", err
			}
			continue
		}
		if err := root.MkdirAll(filepath.Dir(name), 0700); err != nil {
			return "", err
		}
		f := files[e.Path]
		if f == nil {
			return "", errors.New("project_archive_invalid")
		}
		r, err := f.Open()
		if err != nil {
			return "", err
		}
		out, err := root.OpenFile(name, os.O_CREATE|os.O_EXCL|os.O_WRONLY, os.FileMode(e.Mode)&0777|0600)
		if err != nil {
			_ = r.Close()
			return "", err
		}
		h := sha256.New()
		n, copyErr := io.Copy(io.MultiWriter(out, h), contextReader{ctx, io.LimitReader(r, e.Size+1)})
		_ = r.Close()
		closeErr := out.Close()
		if copyErr != nil {
			return "", copyErr
		}
		if closeErr != nil {
			return "", closeErr
		}
		if n != e.Size || hex.EncodeToString(h.Sum(nil)) != e.SHA256 {
			return "", errors.New("project_archive_checksum")
		}
	}
	if err := os.Rename(staging, destination); err != nil {
		return "", err
	}
	return destination, nil
}

func verifyExtracted(ctx context.Context, directory string, m ArchiveManifest) error {
	root, err := os.OpenRoot(directory)
	if err != nil {
		return err
	}
	defer root.Close()
	for _, e := range m.Entries {
		info, err := root.Lstat(filepath.FromSlash(e.Path))
		if err != nil {
			return errors.New("project_archive_destination_exists")
		}
		if e.Directory {
			if !info.IsDir() {
				return errors.New("project_archive_destination_exists")
			}
			continue
		}
		if !info.Mode().IsRegular() || info.Size() != e.Size {
			return errors.New("project_archive_destination_exists")
		}
		f, err := root.Open(filepath.FromSlash(e.Path))
		if err != nil {
			return err
		}
		h := sha256.New()
		_, err = io.Copy(h, contextReader{ctx, f})
		_ = f.Close()
		if err != nil {
			return err
		}
		if hex.EncodeToString(h.Sum(nil)) != e.SHA256 {
			return errors.New("project_archive_destination_exists")
		}
	}
	return nil
}
