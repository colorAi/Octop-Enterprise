package main

import (
	"archive/zip"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestRuntimeIPCRejectsStaleAndUnsupportedRequests(t *testing.T) {
	valid := "desktop:runtime:session:2:start:" + url.QueryEscape(`{"thread_id":"t","text":"你好: test"}`)
	id, method, params, ok := parseRuntimeEvent(valid, "session")
	if !ok || id != "2" || method != "start" || !json.Valid(params) {
		t.Fatal("valid request rejected")
	}
	for _, bad := range []string{strings.Replace(valid, "session", "old", 1), "desktop:runtime:session:2:initialize:%7B%7D", "desktop:runtime:session:2:execute:%7B%7D", "desktop:runtime:session:2:start:[]", "desktop:runtime:session:abc:status:%7B%7D"} {
		if _, _, _, ok := parseRuntimeEvent(bad, "session"); ok {
			t.Fatalf("accepted %s", bad)
		}
	}
	if _, _, _, ok := parseRuntimeEvent(valid, ""); ok {
		t.Fatal("uninitialized nonce accepted")
	}
	for _, action := range []string{"steer", "steer_remove"} {
		request := "desktop:runtime:session:3:" + action + ":" + url.QueryEscape(`{"thread_id":"t","expected_turn_id":"turn"}`)
		if _, got, _, ok := parseRuntimeEvent(request, "session"); !ok || got != action {
			t.Fatalf("steer request rejected: %s", action)
		}
		if _, _, _, ok := parseRuntimeEvent(request, "old-session"); ok {
			t.Fatal("steer request accepted after account change")
		}
	}
}
func TestCompanyRuntimeAccountDirectoriesAreIsolated(t *testing.T) {
	t.Setenv("OCTOP_HOME", t.TempDir())
	a := companyStateDir("https://one", 1)
	if a == companyStateDir("https://one", 2) || a == companyStateDir("https://two", 1) || strings.Contains(a, filepath.Join(".octop", "portable")) {
		t.Fatal("runtime state crossed account/company scope")
	}
}
func TestOldFolderPermissionDoesNotGrantTerminalExecution(t *testing.T) {
	t.Setenv("OCTOP_HOME", t.TempDir())
	name := workspaceSettingsPath("https://one", 1)
	_ = os.MkdirAll(filepath.Dir(name), 0700)
	_ = os.WriteFile(name, []byte(`{"path":"/old"}`), 0600)
	if readWorkspace("https://one", 1) != "/old" || workspaceRuntimeAuthorized("https://one", 1) {
		t.Fatal("legacy folder consent was expanded")
	}
	if err := saveWorkspace("https://one", 1, "/new"); err != nil {
		t.Fatal(err)
	}
	if !workspaceRuntimeAuthorized("https://one", 1) {
		t.Fatal("explicit new authorization not saved")
	}
}
func TestCompanyPayloadFailsClosedWithoutManifestOrWithArchiveLinks(t *testing.T) {
	if validateCompanyRuntime(t.TempDir()) == nil {
		t.Fatal("missing manifest accepted")
	}
	name := filepath.Join(t.TempDir(), "payload.zip")
	out, _ := os.Create(name)
	z := zip.NewWriter(out)
	header := &zip.FileHeader{Name: "root/runtime/python"}
	header.SetMode(os.ModeSymlink | 0755)
	f, _ := z.CreateHeader(header)
	_, _ = f.Write([]byte("/outside"))
	_ = z.Close()
	_ = out.Close()
	if verifyCompanyZip(name) == nil {
		t.Fatal("archive symlink accepted")
	}
}

func TestHistoryArtifactRequiresAuthorizedDirectoryAndMatchingBytes(t *testing.T) {
	root := t.TempDir()
	file := filepath.Join(root, "result.txt")
	_ = os.WriteFile(file, []byte("result"), 0600)
	hash := sha256.Sum256([]byte("result"))
	digest := hex.EncodeToString(hash[:])
	if _, err := checkedRuntimeArtifact(root, file, digest, false); err != nil {
		t.Fatal(err)
	}
	if _, err := checkedRuntimeArtifact(root, file, "", false); err == nil {
		t.Fatal("unverified history file accepted")
	}
	outside := filepath.Join(t.TempDir(), "private.txt")
	_ = os.WriteFile(outside, []byte("result"), 0600)
	if _, err := checkedRuntimeArtifact(root, outside, digest, false); err == nil {
		t.Fatal("file outside current account grant accepted")
	}
	_ = os.WriteFile(file, []byte("changed"), 0600)
	if _, err := checkedRuntimeArtifact(root, file, digest, false); err == nil {
		t.Fatal("changed history artifact accepted")
	}
}
