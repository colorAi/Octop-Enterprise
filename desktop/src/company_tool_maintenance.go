package main

import (
	"encoding/hex"
	"encoding/json"
	"errors"
	"log"
	"os"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
	"time"

	"octop.desktop/internal/companytools"
)

func companyToolDigest(name string) bool {
	data, err := hex.DecodeString(name)
	return err == nil && len(data) == 32 && hex.EncodeToString(data) == name
}

func companyToolLock(tools, digest string) string {
	return filepath.Join(tools, ".locks", digest+".lock")
}

// Resolve the tools link once, so relocated caches share the same locks even
// when clients reach them through different OCTOP_HOME paths.
func lockCompanyTools(home string, timeout time.Duration) (*os.File, string, error) {
	dir := filepath.Join(home, "company-runtime", "tools")
	if err := os.MkdirAll(dir, 0700); err != nil {
		return nil, "", err
	}
	dir, err := filepath.EvalSymlinks(dir)
	if err != nil {
		return nil, "", err
	}
	lock, err := companytools.Lock(companyToolLock(dir, "cache"), true, timeout)
	return lock, dir, err
}

// Called with the in-memory preparation lock and the cross-process cache lock.
// Keep every root this shell has handed to a worker pinned until shell exit,
// including during account changes and failed-start diagnostics.
func (c *companyPortableCache) pin(root string) error {
	resolved, err := filepath.EvalSymlinks(root)
	if err != nil {
		return err
	}
	if c.leases[resolved] != nil {
		return nil
	}
	lease, err := companytools.Lock(companyToolLock(filepath.Dir(resolved), filepath.Base(resolved)), false, 0)
	if err != nil {
		return err
	}
	if c.leases == nil {
		c.leases = map[string]*os.File{}
	}
	c.leases[resolved] = lease
	return nil
}

func companyActiveTools(home, tools string, commands []string) map[string]bool {
	active := map[string]bool{}
	aliases := []string{tools, filepath.Join(home, "company-runtime", "tools")}
	for _, alias := range aliases {
		alias, err := filepath.Abs(alias)
		if err != nil {
			continue
		}
		prefix := alias + string(os.PathSeparator)
		if runtime.GOOS == "windows" {
			prefix = strings.ToLower(prefix)
		}
		for _, command := range commands {
			if runtime.GOOS == "windows" {
				command = strings.ToLower(command)
			}
			for _, suffix := range strings.Split(command, prefix)[1:] {
				if len(suffix) > 64 && suffix[64] == os.PathSeparator && companyToolDigest(suffix[:64]) {
					active[suffix[:64]] = true
				}
			}
		}
	}
	return active
}

func pruneCompanyTools(home, current string) {
	guard, tools, err := lockCompanyTools(home, 0)
	if errors.Is(err, companytools.ErrBusy) {
		return // Another installation/collection owns the cache; retry next launch.
	}
	if err == nil {
		defer guard.Close()
		var commands []string
		commands, err = companytools.Commands()
		if err == nil {
			err = pruneCompanyToolsLocked(home, tools, current, commands, time.Now())
		}
	}
	if err != nil {
		log.Printf("旧工具缓存清理已推迟：%v", err)
	}
}

// The caller holds the cache lock. A failed process inventory must never be
// passed here as an empty inventory: older clients do not hold runtime leases.
func pruneCompanyToolsLocked(home, tools, current string, commands []string, now time.Time) error {
	root, err := os.OpenRoot(tools)
	if err != nil {
		return err
	}
	defer root.Close()
	entries, err := os.ReadDir(tools)
	if err != nil {
		return err
	}
	keep := companyActiveTools(home, tools, commands)
	keep[filepath.Base(current)] = true
	type candidate struct {
		name string
		used time.Time
	}
	var versions []candidate
	var stale []string
	for _, entry := range entries {
		if !entry.IsDir() || entry.Type()&os.ModeSymlink != 0 {
			continue
		}
		name := entry.Name()
		info, err := entry.Info()
		if err != nil {
			return err
		}
		if strings.HasSuffix(name, ".new") && companyToolDigest(strings.TrimSuffix(name, ".new")) {
			// Leave recent staging alone, including extraction by older clients.
			if now.Sub(info.ModTime()) > 24*time.Hour {
				stale = append(stale, name)
			}
			continue
		}
		if strings.HasSuffix(name, ".gc") && companyToolDigest(strings.TrimSuffix(name, ".gc")) {
			stale = append(stale, name)
			continue
		}
		if !companyToolDigest(name) {
			continue
		}
		data, err := root.ReadFile(filepath.Join(name, "company-runtime-manifest.json"))
		var manifest companyManifest
		if err != nil || json.Unmarshal(data, &manifest) != nil || manifest.Protocol != 1 || len(manifest.Files) == 0 {
			continue // Only collect recognizable managed installations.
		}
		used := info.ModTime()
		if stamp, err := os.Stat(companyToolLock(tools, name)); err == nil {
			used = stamp.ModTime()
		}
		versions = append(versions, candidate{name, used})
	}
	sort.Slice(versions, func(i, j int) bool {
		if versions[i].used.Equal(versions[j].used) {
			return versions[i].name < versions[j].name
		}
		return versions[i].used.After(versions[j].used)
	})
	// Current + one recently used rollback, plus any live/leased versions.
	for _, version := range versions {
		if version.name != filepath.Base(current) {
			keep[version.name] = true
			break
		}
	}
	for _, version := range versions {
		stale = append(stale, version.name)
	}
	var failures []error
	for _, name := range stale {
		digest := strings.TrimSuffix(strings.TrimSuffix(name, ".new"), ".gc")
		if keep[digest] {
			continue
		}
		lease, err := companytools.Lock(companyToolLock(tools, digest), true, 0)
		if errors.Is(err, companytools.ErrBusy) {
			keep[digest] = true
			continue
		}
		if err != nil {
			failures = append(failures, err)
			continue
		}
		if name == digest {
			// A crash halfway through deletion must not strand an unrecognizable
			// hash directory whose manifest has already been removed.
			err = root.RemoveAll(name + ".gc")
			if err == nil {
				err = root.Rename(name, name+".gc")
			}
			name += ".gc"
		}
		if err == nil {
			err = root.RemoveAll(name)
		}
		lease.Close()
		if err != nil {
			failures = append(failures, err)
		}
	}
	failures = append(failures, pruneCompanyBytecode(home, tools, root, keep))
	return errors.Join(failures...)
}

func companyBytecodeDir(home, root string) string {
	return filepath.Join(home, "company-runtime", "pyc", filepath.Base(root))
}

func pruneCompanyBytecode(home, tools string, toolsRoot *os.Root, keep map[string]bool) error {
	// New clients have an independent prefix per payload. Older Python prefixes
	// mirror absolute source paths (without drive/root), both before and after a
	// tools symlink was relocated. Only these exact tool-cache subtrees are eligible.
	locations := map[string][]string{
		filepath.Join(home, "company-runtime", "pyc"): {"."},
	}
	for _, alias := range []string{filepath.Join(home, "company-runtime", "tools"), tools} {
		absolute, err := filepath.Abs(alias)
		if err != nil {
			return err
		}
		rel := strings.TrimLeft(strings.TrimPrefix(absolute, filepath.VolumeName(absolute)), string(os.PathSeparator))
		legacy := filepath.Join(home, "pyc")
		locations[legacy] = append(locations[legacy], rel)
	}
	for cache, prefixes := range locations {
		if err := pruneCompanyBytecodeAt(cache, prefixes, tools, toolsRoot, keep); err != nil {
			return err
		}
	}
	return nil
}

func pruneCompanyBytecodeAt(cache string, prefixes []string, tools string, toolsRoot *os.Root, keep map[string]bool) error {
	root, err := os.OpenRoot(cache)
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if err != nil {
		return err
	}
	defer root.Close()
	for _, prefix := range prefixes {
		dir, err := root.Open(prefix)
		if errors.Is(err, os.ErrNotExist) {
			continue
		}
		if err != nil {
			return err
		}
		entries, err := dir.ReadDir(-1)
		dir.Close()
		if err != nil {
			return err
		}
		for _, entry := range entries {
			name := entry.Name()
			if !entry.IsDir() || !companyToolDigest(name) || keep[name] {
				continue
			}
			if _, err := toolsRoot.Lstat(name); !errors.Is(err, os.ErrNotExist) {
				continue
			}
			lease, err := companytools.Lock(companyToolLock(tools, name), true, 0)
			if errors.Is(err, companytools.ErrBusy) {
				continue
			}
			if err != nil {
				return err
			}
			err = root.RemoveAll(filepath.Join(prefix, name))
			lease.Close()
			if err != nil {
				return err
			}
		}
	}
	return nil
}
