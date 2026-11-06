package main

import (
	"archive/zip"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"octop.desktop/internal/companytools"
)

func installedCompanyTools(t *testing.T, home string, count int) []string {
	t.Helper()
	source := validRuntimeFixture(t)
	archive := filepath.Join(t.TempDir(), "tools.zip")
	var roots []string
	for i := 0; i < count; i++ {
		// Distinct valid application revisions with the same dependency files.
		manifest := filepath.Join(source, "company-runtime-manifest.json")
		data, _ := os.ReadFile(manifest)
		var value map[string]any
		_ = json.Unmarshal(data, &value)
		value["source_commit"] = fmt.Sprint(i)
		data, _ = json.Marshal(value)
		if err := os.WriteFile(manifest, data, 0600); err != nil {
			t.Fatal(err)
		}
		companyToolArchive(t, source, archive)
		root, err := prepareCompanyPortable(archive, home)
		if err != nil {
			t.Fatal(err)
		}
		resolved, _ := filepath.EvalSymlinks(root)
		stamp := time.Now().Add(time.Duration(i-count) * time.Hour)
		if err = os.Chtimes(companyToolLock(filepath.Dir(resolved), filepath.Base(root)), stamp, stamp); err != nil {
			t.Fatal(err)
		}
		roots = append(roots, root)
	}
	return roots
}

func runCompanyCollection(t *testing.T, home, current string, commands ...string) {
	t.Helper()
	guard, tools, err := lockCompanyTools(home, 0)
	if err != nil {
		t.Fatal(err)
	}
	defer guard.Close()
	if err = pruneCompanyToolsLocked(home, tools, current, commands, time.Now()); err != nil {
		t.Fatal(err)
	}
}

func assertCompanyPath(t *testing.T, path string, exists bool) {
	t.Helper()
	_, err := os.Lstat(path)
	if exists && err != nil || !exists && !os.IsNotExist(err) {
		t.Fatalf("path %s exists=%v: %v", path, exists, err)
	}
}

func TestCompanyToolsRetainCurrentAndOneRollback(t *testing.T) {
	home := t.TempDir()
	roots := installedCompanyTools(t, home, 5)
	runCompanyCollection(t, home, roots[4])
	for i, root := range roots {
		assertCompanyPath(t, root, i >= 3)
	}
	// Repeated maintenance is idempotent and preserves both verified installs.
	runCompanyCollection(t, home, roots[4])
	for _, root := range roots[3:] {
		if err := validateCompanyRuntime(root); err != nil {
			t.Fatal(err)
		}
	}
}

func TestCompanyToolsPreserveLeasesAndLegacyWorkers(t *testing.T) {
	home := t.TempDir()
	roots := installedCompanyTools(t, home, 5)
	lease, err := companytools.Lock(companyToolLock(filepath.Dir(roots[0]), filepath.Base(roots[0])), false, 0)
	if err != nil {
		t.Fatal(err)
	}
	defer lease.Close()
	runCompanyCollection(t, home, roots[4], pythonExe(roots[1])+" -u launch.py company-runtime")
	for i, root := range roots {
		assertCompanyPath(t, root, i != 2)
	}
	lease.Close()
	runCompanyCollection(t, home, roots[4])
	for i, root := range roots {
		assertCompanyPath(t, root, i >= 3)
	}
}

func TestCompanyToolsCollectStagingWithoutTouchingForeignData(t *testing.T) {
	home := t.TempDir()
	roots := installedCompanyTools(t, home, 3)
	tools := filepath.Dir(roots[0])
	stale := filepath.Join(tools, strings.Repeat("a", 64)+".new")
	recent := filepath.Join(tools, strings.Repeat("b", 64)+".new")
	interrupted := filepath.Join(tools, strings.Repeat("c", 64)+".gc")
	foreign := filepath.Join(tools, "user-project")
	unknown := filepath.Join(tools, strings.Repeat("d", 64))
	account := filepath.Join(home, "company-runtime", "accounts", "employee")
	for _, dir := range []string{stale, recent, interrupted, foreign, unknown, account} {
		if err := os.MkdirAll(dir, 0700); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(dir, "keep.txt"), []byte("data"), 0600); err != nil {
			t.Fatal(err)
		}
	}
	old := time.Now().Add(-25 * time.Hour)
	if err := os.Chtimes(stale, old, old); err != nil {
		t.Fatal(err)
	}
	runCompanyCollection(t, home, roots[2])
	assertCompanyPath(t, stale, false)
	assertCompanyPath(t, interrupted, false)
	for _, dir := range []string{recent, foreign, unknown, account} {
		assertCompanyPath(t, filepath.Join(dir, "keep.txt"), true)
	}
}

func TestCompanyToolsRelocatedCacheAndScopedBytecodeCleanup(t *testing.T) {
	home, external := t.TempDir(), t.TempDir()
	tools := filepath.Join(home, "company-runtime", "tools")
	if err := os.MkdirAll(filepath.Dir(tools), 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(external, tools); err != nil {
		t.Skip(err)
	}
	roots := installedCompanyTools(t, home, 4)
	var obsolete, retained []string
	for i, root := range roots {
		paths := []string{companyBytecodeDir(home, root)}
		resolved, _ := filepath.EvalSymlinks(root)
		for _, source := range []string{root, resolved} {
			rel := strings.TrimLeft(strings.TrimPrefix(source, filepath.VolumeName(source)), string(os.PathSeparator))
			paths = append(paths, filepath.Join(home, "pyc", rel))
		}
		for _, path := range paths {
			if err := os.MkdirAll(path, 0700); err != nil {
				t.Fatal(err)
			}
			if err := os.WriteFile(filepath.Join(path, "module.pyc"), []byte("cache"), 0600); err != nil {
				t.Fatal(err)
			}
			if i < 2 {
				obsolete = append(obsolete, path)
			} else {
				retained = append(retained, path)
			}
		}
	}
	foreign := filepath.Join(home, "pyc", "another-python")
	if err := os.MkdirAll(foreign, 0700); err != nil {
		t.Fatal(err)
	}
	// A digest-shaped directory symlink must never expand the deletion scope.
	outside := t.TempDir()
	if err := os.Symlink(outside, filepath.Join(external, strings.Repeat("e", 64))); err != nil {
		t.Fatal(err)
	}
	runCompanyCollection(t, home, roots[3])
	for _, path := range obsolete {
		assertCompanyPath(t, path, false)
	}
	for _, path := range append(retained, foreign, outside, tools) {
		assertCompanyPath(t, path, true)
	}
	if !companyActiveTools(home, external, []string{pythonExe(roots[0])})[filepath.Base(roots[0])] {
		t.Fatal("legacy process using the symlink path was not protected")
	}
}

func TestCompanyToolExtractionFailureRemovesStaging(t *testing.T) {
	home := t.TempDir()
	archive := filepath.Join(t.TempDir(), "bad.zip")
	file, err := os.Create(archive)
	if err != nil {
		t.Fatal(err)
	}
	writer := zip.NewWriter(file)
	first, _ := writer.Create("Octop-test/partial.bin")
	first.Write([]byte("partially extracted dependency"))
	bad, _ := writer.Create("Octop-test/../../outside")
	bad.Write([]byte("invalid"))
	writer.Close()
	file.Close()
	if _, err = prepareCompanyPortable(archive, home); err == nil {
		t.Fatal("invalid archive accepted")
	}
	staging, err := filepath.Glob(filepath.Join(home, "company-runtime", "tools", "*.new"))
	if err != nil || len(staging) != 0 {
		t.Fatal("failed installation retained staging", staging, err)
	}
}

func TestCompanyToolsConcurrentPreparationSharesOneInstall(t *testing.T) {
	home, source := t.TempDir(), validRuntimeFixture(t)
	archive := filepath.Join(t.TempDir(), "tools.zip")
	companyToolArchive(t, source, archive)
	var group sync.WaitGroup
	results := make(chan string, 4)
	for range 4 {
		group.Go(func() {
			root, err := prepareCompanyPortable(archive, home)
			if err != nil {
				t.Error(err)
			}
			results <- root
		})
	}
	group.Wait()
	close(results)
	var first string
	for root := range results {
		if first == "" {
			first = root
		}
		if root == "" || root != first {
			t.Fatalf("different or missing runtime: %q, %q", first, root)
		}
	}
	if err := validateCompanyRuntime(first); err != nil {
		t.Fatal(err)
	}
}

func TestCompanyToolsRepairDoesNotReplaceLeasedFiles(t *testing.T) {
	home, source := t.TempDir(), validRuntimeFixture(t)
	archive := filepath.Join(t.TempDir(), "tools.zip")
	companyToolArchive(t, source, archive)
	root, err := prepareCompanyPortable(archive, home)
	if err != nil {
		t.Fatal(err)
	}
	resolved, _ := filepath.EvalSymlinks(root)
	lease, err := companytools.Lock(companyToolLock(filepath.Dir(resolved), filepath.Base(root)), false, 0)
	if err != nil {
		t.Fatal(err)
	}
	defer lease.Close()
	path := filepath.Join(root, "launch.py")
	if err := os.WriteFile(path, []byte("damaged"), 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := prepareCompanyPortable(archive, home); err == nil {
		t.Fatal("repair replaced a leased runtime")
	}
	if data, _ := os.ReadFile(path); string(data) != "damaged" {
		t.Fatal("leased file was overwritten")
	}
	lease.Close()
	if _, err := prepareCompanyPortable(archive, home); err != nil {
		t.Fatal(err)
	}
	if err := validateCompanyRuntime(root); err != nil {
		t.Fatal(err)
	}
}
