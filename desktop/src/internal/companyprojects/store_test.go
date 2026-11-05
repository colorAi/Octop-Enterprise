package companyprojects

import (
	"os"
	"path/filepath"
	"reflect"
	"sync"
	"testing"
)

func openTestStore(t *testing.T) *Store {
	t.Helper()
	s, err := Open(filepath.Join(t.TempDir(), "account", "projects.json"))
	if err != nil {
		t.Fatal(err)
	}
	return s
}

func registerTest(t *testing.T, s *Store, path string) Project {
	t.Helper()
	p, err := s.Register(path)
	if err != nil {
		t.Fatal(err)
	}
	return p
}

func TestSelectionDoesNotRebindConversations(t *testing.T) {
	s := openTestStore(t)
	a := registerTest(t, s, t.TempDir())
	if err := s.Bind("chat-a", a.ID); err != nil {
		t.Fatal(err)
	}
	b := registerTest(t, s, t.TempDir())
	if err := s.Bind("chat-b", b.ID); err != nil {
		t.Fatal(err)
	}
	if err := s.Bind("chat-a", b.ID); err == nil {
		t.Fatal("conversation silently rebound")
	}
	if err := s.Bind("chat-a", a.ID); err != nil {
		t.Fatal("idempotent bind failed", err)
	}
	if got, err := s.ForThread("chat-a"); err != nil || got.Path != a.Path {
		t.Fatal(got, err)
	}
	if _, err := s.ForThread("unbound"); err == nil {
		t.Fatal("unbound chat used current project")
	}
	reopened, err := Open(s.filename)
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(s.Snapshot(), reopened.Snapshot()) {
		t.Fatal("catalog did not persist")
	}
	if reopened.Snapshot().Current != b.ID {
		t.Fatal("current project lost")
	}
	snapshot := s.Snapshot()
	delete(snapshot.Projects, a.ID)
	if _, err := s.ForThread("chat-a"); err != nil {
		t.Fatal("caller mutated live catalog", err)
	}
	if err := os.RemoveAll(a.Path); err != nil {
		t.Fatal(err)
	}
	if _, err := s.ForThread("chat-a"); err == nil {
		t.Fatal("missing directory used another project")
	}
}

func TestNativeSelectionDeduplicatesDirectoryAliases(t *testing.T) {
	s := openTestStore(t)
	root := t.TempDir()
	a := registerTest(t, s, root)
	b := registerTest(t, s, filepath.Join(root, "."))
	if a.ID != b.ID {
		t.Fatal("duplicate physical directory")
	}
	alias := filepath.Join(t.TempDir(), "alias")
	if err := os.Symlink(root, alias); err != nil {
		t.Skip("symlinks unavailable")
	}
	c := registerTest(t, s, alias)
	if a.ID != c.ID || len(s.Snapshot().Projects) != 1 {
		t.Fatal("alias duplicated project")
	}
}

func TestSeparateAccountsDoNotShareSelection(t *testing.T) {
	a, b := openTestStore(t), openTestStore(t)
	root := t.TempDir()
	pa := registerTest(t, a, root)
	if b.Snapshot().Current != "" || len(b.Snapshot().Projects) != 0 {
		t.Fatal("account inherited selection")
	}
	pb := registerTest(t, b, root)
	if pa.ID == pb.ID {
		t.Fatal("account reused project identity")
	}
}

func TestLegacyImportPreservesExactDirectoryWithoutAuthorization(t *testing.T) {
	s := openTestStore(t)
	parent := t.TempDir()
	root := filepath.Join(parent, "thread-original")
	if err := os.Mkdir(root, 0700); err != nil {
		t.Fatal(err)
	}
	file := filepath.Join(root, "原始文件.txt")
	if err := os.WriteFile(file, []byte("original"), 0600); err != nil {
		t.Fatal(err)
	}
	p, err := s.ImportLegacy("old", root)
	if err != nil {
		t.Fatal(err)
	}
	again, err := s.ImportLegacy("old", root)
	if err != nil || !reflect.DeepEqual(p, again) {
		t.Fatal("migration not idempotent", err)
	}
	resolved, _ := canonical(root)
	if p.Path != resolved || p.Authorized || !p.Legacy || s.Snapshot().Current != "" {
		t.Fatal("legacy path or consent changed", p)
	}
	if _, err := s.ForThread("old"); err == nil {
		t.Fatal("import silently authorized a project")
	}
	selected := registerTest(t, s, root)
	if selected.ID != p.ID || !selected.Authorized || selected.Revision != 2 {
		t.Fatal("explicit authorization failed", selected)
	}
	if data, err := os.ReadFile(file); err != nil || string(data) != "original" {
		t.Fatal("migration changed user file", err)
	}
	missing := filepath.Join(parent, "removed")
	if _, err := s.ImportLegacy("missing", missing); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(missing); !os.IsNotExist(err) {
		t.Fatal("migration created missing folder")
	}
	if _, err := s.ImportLegacy("old", parent); err == nil {
		t.Fatal("migration rebound to global parent")
	}
}

func TestCatalogRejectsCorruptionAndProtectsPriorStateOnWriteFailure(t *testing.T) {
	s := openTestStore(t)
	p := registerTest(t, s, t.TempDir())
	before := s.Snapshot()
	// A failed replacement must not publish unpersisted in-memory state.
	if err := os.Remove(s.filename); err != nil {
		t.Fatal(err)
	}
	if err := os.Mkdir(s.filename, 0700); err != nil {
		t.Fatal(err)
	}
	if err := s.Bind("new-chat", p.ID); err == nil {
		t.Fatal("expected save failure")
	}
	if !reflect.DeepEqual(before, s.Snapshot()) {
		t.Fatal("failed save changed memory")
	}
	if err := os.Remove(s.filename); err != nil {
		t.Fatal(err)
	}
	for _, data := range []string{"{", `{"version":2}`, `{"version":1,"projects":{},"threads":{"x":"missing"}}`} {
		if err := os.WriteFile(s.filename, []byte(data), 0600); err != nil {
			t.Fatal(err)
		}
		if _, err := Open(s.filename); err == nil {
			t.Fatal("corrupt catalog reset silently")
		}
		got, _ := os.ReadFile(s.filename)
		if string(got) != data {
			t.Fatal("opening corrupted file rewrote it")
		}
	}
}

func TestConcurrentBindingsPersistAndApplicationDataCannotBeProject(t *testing.T) {
	s := openTestStore(t)
	p := registerTest(t, s, t.TempDir())
	var wg sync.WaitGroup
	for _, id := range []string{"one", "two", "three"} {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if err := s.Bind(id, p.ID); err != nil {
				t.Error(err)
			}
		}()
	}
	wg.Wait()
	reopened, err := Open(s.filename)
	if err != nil || len(reopened.Snapshot().Threads) != 3 {
		t.Fatal("concurrent binding lost", err)
	}
	if _, err := s.Register(filepath.Dir(s.filename)); err == nil {
		t.Fatal("authorized app data as project")
	}
	inside := filepath.Join(filepath.Dir(s.filename), "private")
	if err := os.Mkdir(inside, 0700); err != nil {
		t.Fatal(err)
	}
	if _, err := s.Register(inside); err == nil {
		t.Fatal("authorized a private state subdirectory")
	}
	root := t.TempDir()
	newState := filepath.Join(root, "client-state", "projects.json")
	empty, err := Open(newState)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := empty.Register(root); err == nil {
		t.Fatal("authorized future application state")
	}
	if _, err := os.Stat(filepath.Dir(newState)); !os.IsNotExist(err) {
		t.Fatal("rejected project was modified")
	}
}

func TestRelocationKeepsProjectIdentityAndLegacyReceipts(t *testing.T) {
	s := openTestStore(t)
	original := t.TempDir()
	p := registerTest(t, s, original)
	if err := s.Bind("legacy", p.ID); err != nil {
		t.Fatal(err)
	}
	next := t.TempDir()
	moved, err := s.Relocate(p.ID, next)
	if err != nil {
		t.Fatal(err)
	}
	if moved.ID != p.ID || moved.Revision <= p.Revision || moved.Path == p.Path {
		t.Fatal(moved)
	}
	if _, err = s.ImportLegacy("legacy", original); err != nil {
		t.Fatal("legacy receipt lost after explicit relocation", err)
	}
	reloaded, err := Open(s.filename)
	if err != nil {
		t.Fatal(err)
	}
	got, err := reloaded.ForThread("legacy")
	if err != nil || got.Path != moved.Path {
		t.Fatal(got, err)
	}
	another := registerTest(t, s, t.TempDir())
	if _, err = s.Relocate(p.ID, another.Path); err == nil {
		t.Fatal("relocation merged distinct projects")
	}
	snapshot := s.Snapshot()
	snapshot.Projects[p.ID].PreviousPaths[0] = "forged"
	if s.Snapshot().Projects[p.ID].PreviousPaths[0] == "forged" {
		t.Fatal("caller mutated prior path record")
	}
}
