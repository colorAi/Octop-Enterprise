package main

import (
	"archive/zip"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"
)

func companyToolArchive(t *testing.T, root, path string) {
	t.Helper()
	file, err := os.Create(path)
	if err != nil {
		t.Fatal(err)
	}
	writer := zip.NewWriter(file)
	err = filepath.WalkDir(root, func(path string, entry fs.DirEntry, err error) error {
		if err != nil || entry.IsDir() {
			return err
		}
		info, err := entry.Info()
		if err != nil {
			return err
		}
		header, err := zip.FileInfoHeader(info)
		if err != nil {
			return err
		}
		relative, _ := filepath.Rel(root, path)
		header.Name = "Octop-test/" + filepath.ToSlash(relative)
		out, err := writer.CreateHeader(header)
		if err != nil {
			return err
		}
		data, err := os.ReadFile(path)
		if err == nil {
			_, err = out.Write(data)
		}
		return err
	})
	if closeErr := writer.Close(); err == nil {
		err = closeErr
	}
	if closeErr := file.Close(); err == nil {
		err = closeErr
	}
	if err != nil {
		t.Fatal(err)
	}
}

func TestCompanyToolCheckPersistsAcrossRestartsAndArchiveUpdates(t *testing.T) {
	home, source := t.TempDir(), validRuntimeFixture(t)
	archive := filepath.Join(t.TempDir(), "tools.zip")
	companyToolArchive(t, source, archive)
	checks := 0
	prepare := func(zip, home string) (string, error) {
		return prepareCompanyPortableChecked(zip, home, func(root string) error {
			checks++
			return validateCompanyRuntime(root)
		})
	}
	var first companyPortableCache
	root, err := first.prepare(archive, home, prepare)
	if err != nil {
		t.Fatal(err)
	}
	initialChecks := checks
	var restarted companyPortableCache
	if got, err := restarted.prepare(archive, home, prepare); err != nil || got != root || checks != initialChecks {
		t.Fatalf("restart repeated the full tool check: root=%s err=%v checks=%d", got, err, checks)
	}
	// Existing installations acquire a receipt once, without re-extracting.
	stamp, _ := companyArchiveStamp(archive)
	if err := os.Remove(companyCheckPath(home, stamp.Path)); err != nil {
		t.Fatal(err)
	}
	before, _ := os.Stat(filepath.Join(root, "launch.py"))
	if _, err := prepare(archive, home); err != nil {
		t.Fatal(err)
	}
	after, _ := os.Stat(filepath.Join(root, "launch.py"))
	if checks != initialChecks+1 || before.ModTime() != after.ModTime() {
		t.Fatal("receipt migration reinstalled an already valid tool directory")
	}
	// An updated payload must select a new content-addressed root, preserving
	// previous tools and account/project state for rollback.
	manifest := filepath.Join(source, "company-runtime-manifest.json")
	file, _ := os.OpenFile(manifest, os.O_APPEND|os.O_WRONLY, 0600)
	_, _ = io.WriteString(file, "\n")
	file.Close()
	companyToolArchive(t, source, archive)
	updated, err := restarted.prepare(archive, home, prepare)
	if err != nil || updated == root || checks <= initialChecks+1 {
		t.Fatalf("changed payload reused old tools: %s %v", updated, err)
	}
	if err := validateCompanyRuntime(root); err != nil {
		t.Fatalf("update destroyed previous tools: %v", err)
	}
}

func TestCompanyToolCheckRejectsChangedStartupFilesAndBadReceipts(t *testing.T) {
	for _, kind := range []string{"missing-python", "changed-tool", "changed-entry", "changed-manifest", "unexpected-file", "symlink", "bad-receipt", "wrong-platform", "unsafe-digest"} {
		t.Run(kind, func(t *testing.T) {
			home, source := t.TempDir(), validRuntimeFixture(t)
			archive := filepath.Join(t.TempDir(), "tools.zip")
			companyToolArchive(t, source, archive)
			root, err := prepareCompanyPortable(archive, home)
			if err != nil {
				t.Fatal(err)
			}
			stamp, _ := companyArchiveStamp(archive)
			receipt := companyCheckPath(home, stamp.Path)
			switch kind {
			case "missing-python":
				err = os.Remove(pythonExe(root))
			case "changed-tool":
				suffix := ""
				if runtime.GOOS == "windows" {
					suffix = ".exe"
				}
				err = os.WriteFile(filepath.Join(root, "tools", "ffprobe"+suffix), []byte("damaged"), 0755)
			case "changed-entry":
				entry := filepath.Join(root, "launch.py")
				info, _ := os.Stat(entry)
				later := info.ModTime().Add(time.Hour)
				err = os.Chtimes(entry, later, later)
			case "changed-manifest":
				err = os.WriteFile(filepath.Join(root, "company-runtime-manifest.json"), []byte("{}"), 0600)
			case "unexpected-file":
				err = os.WriteFile(filepath.Join(root, "injected.py"), []byte("pass"), 0600)
			case "symlink":
				entry := filepath.Join(root, "launch.py")
				if err = os.Remove(entry); err == nil {
					err = os.Symlink(filepath.Join(source, "launch.py"), entry)
				}
				if err != nil {
					t.Skip(err)
				}
			case "bad-receipt":
				err = os.WriteFile(receipt, []byte("{"), 0600)
			case "wrong-platform", "unsafe-digest":
				data, _ := os.ReadFile(receipt)
				var check companyToolCheck
				_ = json.Unmarshal(data, &check)
				if kind == "wrong-platform" {
					check.Platform = "another-os"
				} else {
					check.Digest = "../../outside"
				}
				err = saveCompanyToolCheck(receipt, check)
			}
			if err != nil {
				t.Fatal(err)
			}
			checks := 0
			_, err = prepareCompanyPortableChecked(archive, home, func(root string) error {
				checks++
				return validateCompanyRuntime(root)
			})
			if err != nil || checks == 0 {
				t.Fatalf("changed tools did not trigger successful revalidation/repair: %v, checks=%d", err, checks)
			}
			if err := validateCompanyRuntime(root); err != nil {
				t.Fatal(err)
			}
			if cachedCompanyRuntime(home, stamp) != root {
				t.Fatal("successful repair did not refresh the receipt")
			}
		})
	}
}

func TestCompanyToolCheckDoesNotCacheFailedInstallAndInvalidatesStartupFailure(t *testing.T) {
	home, source := t.TempDir(), validRuntimeFixture(t)
	archive := filepath.Join(t.TempDir(), "tools.zip")
	if err := os.WriteFile(filepath.Join(source, "launch.py"), []byte("damaged"), 0600); err != nil {
		t.Fatal(err)
	}
	companyToolArchive(t, source, archive)
	if _, err := prepareCompanyPortable(archive, home); err == nil {
		t.Fatal("corrupt installation accepted")
	}
	stamp, _ := companyArchiveStamp(archive)
	if _, err := os.Stat(companyCheckPath(home, stamp.Path)); !os.IsNotExist(err) {
		t.Fatal("failed validation left a success receipt")
	}
	companyToolArchive(t, validRuntimeFixture(t), archive)
	var cache companyPortableCache
	root, err := cache.prepare(archive, home, prepareCompanyPortable)
	if err != nil {
		t.Fatal(err)
	}
	cache.invalidate(root + "-unrelated")
	stamp, _ = companyArchiveStamp(archive)
	if cachedCompanyRuntime(home, stamp) != root {
		t.Fatal("unrelated runtime invalidated the receipt")
	}
	cache.invalidate(root)
	if cachedCompanyRuntime(home, stamp) != "" || cache.root != "" {
		t.Fatal("failed startup retained the memory or disk cache")
	}
}

func TestCompanyToolManualCheckFindsDamageOutsideStartupFiles(t *testing.T) {
	a := startupTestApp(t)
	source := validRuntimeFixture(t)
	name := "packages/test_dependency/nested/module.py"
	path := filepath.Join(source, filepath.FromSlash(name))
	if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
		t.Fatal(err)
	}
	data := []byte("original dependency")
	if err := os.WriteFile(path, data, 0600); err != nil {
		t.Fatal(err)
	}
	manifestPath := filepath.Join(source, "company-runtime-manifest.json")
	manifestData, _ := os.ReadFile(manifestPath)
	var manifest companyManifest
	_ = json.Unmarshal(manifestData, &manifest)
	digest := sha256.Sum256(data)
	manifest.Files[name] = hex.EncodeToString(digest[:])
	manifestData, _ = json.Marshal(manifest)
	if err := os.WriteFile(manifestPath, manifestData, 0600); err != nil {
		t.Fatal(err)
	}
	archive := filepath.Join(t.TempDir(), "tools.zip")
	companyToolArchive(t, source, archive)
	t.Setenv("OCTOP_DESKTOP_PORTABLE_ZIP", archive)
	root, err := ensureCompanyPortable()
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { readyCompanyPortable.invalidate(root) })
	if err := os.WriteFile(filepath.Join(root, filepath.FromSlash(name)), []byte("corrupt dependency!"), 0600); err != nil {
		t.Fatal(err)
	}
	stamp, _ := companyArchiveStamp(archive)
	if cachedCompanyRuntime(octopHome(), stamp) != root {
		t.Fatal("fixture unexpectedly changed a startup-file stamp")
	}
	_, err = a.performRuntimeAction("nonce", a.companyURL, "status", json.RawMessage(`{"verify_tools":true}`))
	if err == nil || !strings.Contains(err.Error(), "校验失败") {
		t.Fatalf("manual check skipped full content verification: %v", err)
	}
	if a.companyRuntime != nil || cachedCompanyRuntime(octopHome(), stamp) != "" {
		t.Fatal("manual check started a worker or retained the damaged receipt")
	}
}

func BenchmarkCompanyToolCheckRestart(b *testing.B) {
	archive := os.Getenv("OCTOP_BENCH_TOOL_ARCHIVE")
	if archive == "" {
		b.Skip("set OCTOP_BENCH_TOOL_ARCHIVE to a packaged tool archive")
	}
	home := b.TempDir()
	root, err := prepareCompanyPortable(archive, home)
	if err != nil {
		b.Fatal(err)
	}
	b.Run("full-validation", func(b *testing.B) {
		for b.Loop() {
			if err := validateCompanyRuntime(root); err != nil {
				b.Fatal(err)
			}
		}
	})
	b.Run("cached-restart", func(b *testing.B) {
		for b.Loop() {
			if _, err := prepareCompanyPortable(archive, home); err != nil {
				b.Fatal(err)
			}
		}
	})
}
