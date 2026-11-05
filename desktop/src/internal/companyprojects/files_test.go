package companyprojects

import (
	"os"
	"path/filepath"
	"testing"
)

func TestProjectFileBrowserAndSearchStayBoundedAndRooted(t *testing.T) {
	root := t.TempDir()
	outside := t.TempDir()
	for _, dir := range []string{"docs", "node_modules/vendor"} {
		if err := os.MkdirAll(filepath.Join(root, dir), 0700); err != nil {
			t.Fatal(err)
		}
	}
	for path, text := range map[string]string{"docs/中文 report.txt": "hello", "node_modules/vendor/report.txt": "skip"} {
		if err := os.WriteFile(filepath.Join(root, path), []byte(text), 0600); err != nil {
			t.Fatal(err)
		}
	}
	_ = os.WriteFile(filepath.Join(outside, "secret.txt"), []byte("secret"), 0600)
	if err := os.Symlink(outside, filepath.Join(root, "link")); err != nil {
		t.Fatal(err)
	}
	hits, err := ListFiles(root, "", "report")
	if err != nil || len(hits.Items) != 1 || hits.Items[0].Path != "docs/中文 report.txt" {
		t.Fatal(hits, err)
	}
	file, err := ReadFile(root, hits.Items[0].Path)
	if err != nil || file.Text == nil || *file.Text != "hello" {
		t.Fatal(file, err)
	}
	for _, path := range []string{"../secret.txt", "link/secret.txt", filepath.Join(outside, "secret.txt")} {
		if _, err := ReadFile(root, path); err == nil {
			t.Fatal("read outside project", path)
		}
	}
	if _, err := ListFiles(root, "link", ""); err == nil {
		t.Fatal("followed directory symlink")
	}
	if _, err := ListFiles(root, "../", ""); err == nil {
		t.Fatal("listed outside project")
	}
}
func TestProjectRenameHideAndRevokePreserveFilesAndBindings(t *testing.T) {
	store := openTestStore(t)
	p := registerTest(t, store, t.TempDir())
	if err := store.Bind("chat", p.ID); err != nil {
		t.Fatal(err)
	}
	if err := store.RememberThread("chat"); err != nil {
		t.Fatal(err)
	}
	if err := store.RememberExpert(p.ID, "writer"); err != nil {
		t.Fatal(err)
	}
	if err := store.Update(p.ID, "Renamed", true, true); err != nil {
		t.Fatal(err)
	}
	snapshot := store.Snapshot()
	changed := snapshot.Projects[p.ID]
	if snapshot.Current != "" || changed.Name != "Renamed" || !changed.Hidden || changed.Authorized || changed.LastThread != "chat" || changed.LastExpert != "writer" {
		t.Fatal(changed)
	}
	if _, err := os.Stat(p.Path); err != nil {
		t.Fatal(err)
	}
	if _, err := store.ForThread("chat"); err == nil {
		t.Fatal("revoked grant accepted")
	}
	restored := registerTest(t, store, p.Path)
	if restored.ID != p.ID || restored.Hidden || !restored.Authorized {
		t.Fatal(restored)
	}
	if _, err := store.ForThread("chat"); err != nil {
		t.Fatal(err)
	}
}
