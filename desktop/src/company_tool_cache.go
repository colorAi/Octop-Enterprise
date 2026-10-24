package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"runtime"
	"sort"
	"time"
)

type companyToolStamp struct {
	Path     string      `json:"path"`
	Size     int64       `json:"size"`
	Modified int64       `json:"modified"`
	Mode     os.FileMode `json:"mode"`
}

type companyToolCheck struct {
	Version    int              `json:"version"`
	Platform   string           `json:"platform"`
	Arch       string           `json:"arch"`
	Archive    companyToolStamp `json:"archive"`
	Digest     string           `json:"digest"`
	Startup    string           `json:"startup"`
	VerifiedAt int64            `json:"verified_at"`
}

func companyArchiveStamp(path string) (companyToolStamp, error) {
	absolute, err := filepath.Abs(path)
	if err != nil {
		return companyToolStamp{}, err
	}
	info, err := os.Stat(absolute)
	if err != nil {
		return companyToolStamp{}, err
	}
	if !info.Mode().IsRegular() {
		return companyToolStamp{}, errors.New("企业工具包不是普通文件")
	}
	return companyToolStamp{absolute, info.Size(), info.ModTime().UnixNano(), info.Mode()}, nil
}

func companyCheckPath(home, archive string) string {
	key := sha256.Sum256([]byte(archive))
	return filepath.Join(home, "company-runtime", "tool-checks", hex.EncodeToString(key[:])+".json")
}

// Full content verification belongs to installation/update. On subsequent
// launches read only the manifest and stat the startup files and their parents;
// do not walk/hash tens of thousands of unchanged dependency files again.
func companyStartupStamp(root string) (string, error) {
	paths := map[string]bool{".": true, "company-runtime-manifest.json": false}
	for _, name := range companyRuntimeRequired(root) {
		paths[name] = false
		for parent := filepath.Dir(name); parent != "."; parent = filepath.Dir(parent) {
			paths[filepath.ToSlash(parent)] = true
		}
	}
	names := make([]string, 0, len(paths))
	for name := range paths {
		names = append(names, name)
	}
	sort.Strings(names)
	h := sha256.New()
	encoder := json.NewEncoder(h)
	for _, name := range names {
		info, err := os.Lstat(filepath.Join(root, filepath.FromSlash(name)))
		if err != nil {
			return "", err
		}
		if (paths[name] && !info.IsDir()) || (!paths[name] && !info.Mode().IsRegular()) {
			return "", fmt.Errorf("企业运行时启动文件异常：%s", name)
		}
		_ = encoder.Encode(companyToolStamp{name, info.Size(), info.ModTime().UnixNano(), info.Mode()})
	}
	manifest, err := os.ReadFile(filepath.Join(root, "company-runtime-manifest.json"))
	if err != nil {
		return "", err
	}
	_, _ = h.Write(manifest)
	if !launchReady(root) {
		return "", errors.New("企业客户端缺少可执行的 Python 运行时")
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}

func cachedCompanyRuntime(home string, archive companyToolStamp) string {
	data, err := os.ReadFile(companyCheckPath(home, archive.Path))
	var check companyToolCheck
	if err != nil || json.Unmarshal(data, &check) != nil || check.Version != 1 || check.Platform != runtime.GOOS || check.Arch != runtime.GOARCH || check.Archive != archive {
		return ""
	}
	digest, err := hex.DecodeString(check.Digest)
	if err != nil || len(digest) != sha256.Size {
		return ""
	}
	// The receipt cannot select an arbitrary executable path.
	root := filepath.Join(home, "company-runtime", "tools", hex.EncodeToString(digest))
	stamp, err := companyStartupStamp(root)
	if err != nil || stamp != check.Startup {
		return ""
	}
	return root
}

func recordCompanyRuntime(home string, archive companyToolStamp, digest, root string) {
	stamp, err := companyStartupStamp(root)
	if err != nil {
		return
	}
	current, err := companyArchiveStamp(archive.Path)
	if err != nil || current != archive {
		return
	}
	check := companyToolCheck{1, runtime.GOOS, runtime.GOARCH, archive, digest, stamp, time.Now().Unix()}
	if err := saveCompanyToolCheck(companyCheckPath(home, archive.Path), check); err != nil {
		// Verification succeeded. A read-only/full cache directory should not
		// prevent use of the tools, but will require verification next launch.
		log.Printf("企业工具检查结果无法保存：%v", err)
	}
}

func saveCompanyToolCheck(path string, check companyToolCheck) error {
	if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
		return err
	}
	file, err := os.CreateTemp(filepath.Dir(path), ".check-*.tmp")
	if err != nil {
		return err
	}
	defer os.Remove(file.Name())
	if err := json.NewEncoder(file).Encode(check); err != nil {
		file.Close()
		return err
	}
	if err := file.Close(); err != nil {
		return err
	}
	return os.Rename(file.Name(), path)
}

func (c *companyPortableCache) invalidate(root string) {
	c.Lock()
	defer c.Unlock()
	if c.root == root {
		absolute, err := filepath.Abs(c.key.archive)
		if err == nil {
			_ = os.Remove(companyCheckPath(c.key.home, absolute))
		}
		c.key, c.root = companyPortableKey{}, ""
	}
}
