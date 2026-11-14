package companyprojects

import (
	"archive/zip"
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func archiveFixture(t *testing.T) (string, ArchiveManifest) {
	t.Helper()
	manifest := ArchiveManifest{Server: "https://company.test", Owner: 7, ID: "migration"}
	for _, id := range []string{"a", "b"} {
		root := filepath.Join(t.TempDir(), "same-name")
		if err := os.MkdirAll(filepath.Join(root, "empty"), 0700); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(root, "notes.txt"), []byte(id), 0600); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(root, ".hidden"), []byte("hidden"), 0600); err != nil {
			t.Fatal(err)
		}
		manifest.Projects = append(manifest.Projects, Project{ID: id, Name: "Same name", Path: root, Authorized: true, Revision: 1, LastThread: id + "-thread"})
	}
	filename := filepath.Join(t.TempDir(), "migration.zip")
	if err := ExportArchive(context.Background(), filename, manifest); err != nil {
		t.Fatal(err)
	}
	m, err := ReadArchive(filename)
	if err != nil {
		t.Fatal(err)
	}
	if m.Version != 2 || m.Projects[0].LastThread != "" {
		t.Fatal("export included the source conversation selection", m)
	}
	return filename, m
}

func TestArchiveImportsDistinctRootsBeforeFetchingOriginalThreadBindings(t *testing.T) {
	filename, m := archiveFixture(t)
	parent := t.TempDir()
	directory, err := ExtractArchive(context.Background(), filename, parent, m)
	if err != nil {
		t.Fatal(err)
	}
	store, err := Open(filepath.Join(t.TempDir(), "projects.json"))
	if err != nil {
		t.Fatal(err)
	}
	if err := store.ImportCatalog(directory, m); err != nil {
		t.Fatal(err)
	}
	if len(store.Snapshot().Threads) != 0 {
		t.Fatal("archive granted a conversation binding before server sync")
	}
	for _, old := range m.Projects {
		p, err := store.ForID(old.ID)
		if err != nil {
			t.Fatal(err)
		}
		if p.ID != old.ID || p.Path == old.Path || !p.Authorized || !p.SyncPending {
			t.Fatal("lost project identity", p)
		}
		if err := store.SyncConversations(p, "Server name", []string{old.ID + "-thread"}); err != nil {
			t.Fatal(err)
		}
		if current, err := store.ForThread(old.ID + "-thread"); err != nil || current.SyncPending || current.Name != "Server name" {
			t.Fatal("server conversation was not bound", current, err)
		}
		file, err := ReadFile(p.Path, "notes.txt")
		if err != nil || file.Text == nil || *file.Text != old.ID {
			t.Fatal(file, err)
		}
		resolved, err := store.ResolveImportedPath(filepath.Join(old.Path, "notes.txt"))
		if err != nil || resolved != filepath.Join(p.Path, "notes.txt") {
			t.Fatal(resolved, err)
		}
		if _, err := os.Stat(filepath.Join(p.Path, "empty")); err != nil {
			t.Fatal(err)
		}
	}
	if again, err := ExtractArchive(context.Background(), filename, parent, m); err != nil || again != directory {
		t.Fatal("import retry failed", again, err)
	}
	if err := store.ImportCatalog(directory, m); err != nil {
		t.Fatal(err)
	}
	if p, err := store.ForThread("a-thread"); err != nil || p.SyncPending || p.Name != "Server name" {
		t.Fatal("import retry overwrote the synchronized project", p, err)
	}
	if err := os.WriteFile(filepath.Join(directory, "projects", "a", "notes.txt"), []byte("new work"), 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := ExtractArchive(context.Background(), filename, parent, m); err == nil {
		t.Fatal("import overwrote newer work")
	}
	if data, _ := os.ReadFile(filepath.Join(directory, "projects", "a", "notes.txt")); string(data) != "new work" {
		t.Fatal("newer work lost")
	}
}

func TestArchiveFailureRemovesOutputAndAllowsRetry(t *testing.T) {
	_, manifest := archiveFixture(t)
	filename := filepath.Join(t.TempDir(), "retry.zip")
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if err := ExportArchive(ctx, filename, manifest); !errors.Is(err, context.Canceled) {
		t.Fatal("expected cancellation", err)
	}
	if _, err := os.Stat(filename); !errors.Is(err, os.ErrNotExist) {
		t.Fatal("failed export left an incomplete archive", err)
	}
	if err := ExportArchive(context.Background(), filename, manifest); err != nil {
		t.Fatal("retry failed", err)
	}
	if _, err := ReadArchive(filename); err != nil {
		t.Fatal(err)
	}
}

func TestLegacyArchiveImportsFilesWithoutItsOldConversationSnapshot(t *testing.T) {
	filename := filepath.Join(t.TempDir(), "legacy.zip")
	file, err := os.Create(filename)
	if err != nil {
		t.Fatal(err)
	}
	defer file.Close()
	archive := zip.NewWriter(file)
	w, err := archive.Create("manifest.json")
	if err != nil {
		t.Fatal(err)
	}
	legacy := map[string]any{
		"version": 1,
		"server":  "https://company.test",
		"owner":   7,
		"projects": []Project{{
			ID: "a", Name: "Legacy project", Path: t.TempDir(), Revision: 1, LastThread: "old-chat",
		}},
		"entries": []ArchiveEntry{{Path: "projects/a", Directory: true, Mode: 0700}},
		"snapshot": map[string]any{
			"transfer_id": "legacy-archive",
			"threads":     map[string]string{"old-chat": "a"},
		},
	}
	if err := json.NewEncoder(w).Encode(legacy); err != nil {
		t.Fatal(err)
	}
	if err := archive.Close(); err != nil {
		t.Fatal(err)
	}
	if err := file.Close(); err != nil {
		t.Fatal(err)
	}
	m, err := ReadArchive(filename)
	if err != nil || m.ID != "legacy-archive" {
		t.Fatal(m.ID, err)
	}
	directory, err := ExtractArchive(context.Background(), filename, t.TempDir(), m)
	if err != nil {
		t.Fatal(err)
	}
	store, err := Open(filepath.Join(t.TempDir(), "projects.json"))
	if err != nil {
		t.Fatal(err)
	}
	if err := store.ImportCatalog(directory, m); err != nil {
		t.Fatal(err)
	}
	catalog := store.Snapshot()
	if p := catalog.Projects["a"]; !p.Authorized || !p.SyncPending || p.LastThread != "" || len(catalog.Threads) != 0 {
		t.Fatal("legacy archive was treated as a source of conversation data", catalog)
	}
}

func TestImportedWindowsPathsStayInsideAuthorizedProject(t *testing.T) {
	filename, m := archiveFixture(t)
	directory, err := ExtractArchive(context.Background(), filename, t.TempDir(), m)
	if err != nil {
		t.Fatal(err)
	}
	m.Projects[0].PreviousPaths = []string{`C:\Work\Old`}
	store, err := Open(filepath.Join(t.TempDir(), "projects.json"))
	if err != nil {
		t.Fatal(err)
	}
	if err := store.ImportCatalog(directory, m); err != nil {
		t.Fatal(err)
	}
	resolved, err := store.ResolveImportedPath(`c:\work\old\notes.txt`)
	project, _ := store.ForID("a")
	if err != nil || resolved != filepath.Join(project.Path, "notes.txt") {
		t.Fatal(resolved, err)
	}
	if _, err := store.ResolveImportedPath(`C:\Work\Old\..\secret`); err == nil {
		t.Fatal("traversal accepted")
	}
	if err := store.Update("a", "", false, true); err != nil {
		t.Fatal(err)
	}
	if _, err := store.ResolveImportedPath(`C:\Work\Old\notes.txt`); err == nil {
		t.Fatal("revocation bypassed")
	}
}

func TestArchiveRejectsTraversalAndChecksumTampering(t *testing.T) {
	_, m := archiveFixture(t)
	for _, kind := range []string{"traversal", "checksum", "case-collision", "missing-root", "size-overflow"} {
		t.Run(kind, func(t *testing.T) {
			copy := m
			copy.Entries = append([]ArchiveEntry{}, m.Entries...)
			if kind == "traversal" {
				copy.Entries[1].Path = "projects/a/../../escape"
			}
			if kind == "case-collision" {
				copy.Entries = append(copy.Entries, ArchiveEntry{Path: "projects/A", Directory: true})
			}
			if kind == "missing-root" {
				copy.Entries = copy.Entries[1:]
			}
			if kind == "size-overflow" {
				copy.Entries[0].Size = 1<<63 - 1
			}
			file := filepath.Join(t.TempDir(), "bad.zip")
			f, err := os.Create(file)
			if err != nil {
				t.Fatal(err)
			}
			z := zip.NewWriter(f)
			for _, entry := range copy.Entries {
				if entry.Directory {
					continue
				}
				w, err := z.Create(entry.Path)
				if err != nil {
					t.Fatal(err)
				}
				_, _ = w.Write([]byte(strings.Repeat("x", int(entry.Size))))
			}
			w, _ := z.Create("manifest.json")
			_ = json.NewEncoder(w).Encode(copy)
			_ = z.Close()
			_ = f.Close()
			parsed, err := ReadArchive(file)
			if kind != "checksum" {
				if err == nil {
					t.Fatal("invalid archive accepted")
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			parent := t.TempDir()
			if _, err := ExtractArchive(context.Background(), file, parent, parsed); err == nil {
				t.Fatal("bad checksum accepted")
			}
			entries, _ := os.ReadDir(parent)
			if len(entries) != 0 {
				t.Fatal("failed extraction left files", entries)
			}
		})
	}
}

func TestImportedPathRejectsAmbiguousOldRoots(t *testing.T) {
	filename, m := archiveFixture(t)
	directory, err := ExtractArchive(context.Background(), filename, t.TempDir(), m)
	if err != nil {
		t.Fatal(err)
	}
	for i := range m.Projects {
		m.Projects[i].PreviousPaths = []string{`C:\shared`}
	}
	store, err := Open(filepath.Join(t.TempDir(), "projects.json"))
	if err != nil {
		t.Fatal(err)
	}
	if err := store.ImportCatalog(directory, m); err != nil {
		t.Fatal(err)
	}
	if _, err := store.ResolveImportedPath(`C:\shared\notes.txt`); err == nil {
		t.Fatal("ambiguous old path selected a random project")
	}
}

func TestArchiveMaterializesInternalLinksAndRejectsExternalLinks(t *testing.T) {
	_, m := archiveFixture(t)
	p := m.Projects[0]
	m.Projects = m.Projects[:1]
	if err := os.Symlink("notes.txt", filepath.Join(p.Path, "linked.txt")); err != nil {
		t.Skip("symlinks unavailable", err)
	}
	if err := os.Symlink(filepath.Join(p.Path, "linked.txt"), filepath.Join(p.Path, "absolute.txt")); err != nil {
		t.Fatal(err)
	}
	nested := filepath.Join(p.Path, "empty", "nested.txt")
	if err := os.WriteFile(nested, []byte("nested"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(filepath.Dir(nested), filepath.Join(p.Path, "absolute-dir")); err != nil {
		t.Fatal(err)
	}
	file := filepath.Join(t.TempDir(), "links.zip")
	if err := ExportArchive(context.Background(), file, m); err != nil {
		t.Fatal(err)
	}
	parsed, err := ReadArchive(file)
	if err != nil {
		t.Fatal(err)
	}
	directory, err := ExtractArchive(context.Background(), file, t.TempDir(), parsed)
	if err != nil {
		t.Fatal(err)
	}
	for name, want := range map[string]string{"linked.txt": "a", "absolute.txt": "a", "absolute-dir/nested.txt": "nested"} {
		file := filepath.Join(directory, "projects", "a", filepath.FromSlash(name))
		info, err := os.Lstat(file)
		if err != nil || !info.Mode().IsRegular() {
			t.Fatal("link was not materialized", name, info, err)
		}
		if data, err := os.ReadFile(file); err != nil || string(data) != want {
			t.Fatal(name, string(data), err)
		}
	}
	if err := os.Symlink(t.TempDir(), filepath.Join(p.Path, "external")); err != nil {
		t.Fatal(err)
	}
	if err := ExportArchive(context.Background(), filepath.Join(t.TempDir(), "external.zip"), m); err == nil {
		t.Fatal("external project files included without selection")
	}
}
