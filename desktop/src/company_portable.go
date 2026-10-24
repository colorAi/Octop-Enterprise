package main

import (
	"archive/zip"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
)

type companyManifest struct {
	Protocol int               `json:"protocol_version"`
	Platform string            `json:"platform"`
	Arch     string            `json:"arch"`
	Files    map[string]string `json:"files"`
}

func validateCompanyRuntime(root string) error {
	return validateCompanyRuntimeWorkers(root, 4)
}

func companyRuntimeRequired(root string) []string {
	required := []string{"launch.py", "packages/octop/company_runtime.py", "tools/ffmpeg", "tools/ffprobe", "packages/openpyxl/__init__.py", "packages/docx/__init__.py", "packages/pptx/__init__.py", "packages/pypdf/__init__.py", "packages/PIL/__init__.py", "packages/pandas/__init__.py"}
	if runtime.GOOS == "windows" {
		required[2] += ".exe"
		required[3] += ".exe"
	}
	required = append(required, "packages/octop_harness/__init__.py")
	py, _ := filepath.Rel(root, pythonExe(root))
	required = append(required, filepath.ToSlash(py))
	return required
}

func validateCompanyRuntimeWorkers(root string, workers int) error {
	data, err := os.ReadFile(filepath.Join(root, "company-runtime-manifest.json"))
	if err != nil {
		return errors.New("安装包缺少企业运行时清单，请安装完整企业客户端")
	}
	var m companyManifest
	if json.Unmarshal(data, &m) != nil || m.Protocol != 1 || m.Platform != runtime.GOOS || m.Arch != runtime.GOARCH || len(m.Files) == 0 {
		return errors.New("企业运行时与当前系统或协议不匹配")
	}
	for _, name := range companyRuntimeRequired(root) {
		if _, ok := m.Files[name]; !ok {
			return fmt.Errorf("企业运行时缺少必要工具：%s", name)
		}
	}
	for name := range m.Files {
		if filepath.IsAbs(name) || strings.Contains(name, "\\") || filepath.ToSlash(filepath.Clean(name)) != name || strings.HasPrefix(name, "../") {
			return errors.New("企业运行时清单包含无效路径")
		}
	}
	// Walk once in directory order. Root.Open prevents traversal without
	// repeating EvalSymlinks on every ancestor of all 36,000 package files.
	directory, err := os.OpenRoot(root)
	if err != nil {
		return err
	}
	defer directory.Close()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	type check struct{ path, digest string }
	checks := make(chan check, 32)
	var group sync.WaitGroup
	var firstFailure sync.Once
	var failure error
	for range workers {
		group.Go(func() {
			for item := range checks {
				if ctx.Err() != nil {
					return
				}
				file, openErr := directory.Open(filepath.FromSlash(item.path))
				if openErr == nil {
					h := sha256.New()
					_, openErr = io.Copy(h, file)
					file.Close()
					if openErr == nil && hex.EncodeToString(h.Sum(nil)) != item.digest {
						openErr = fmt.Errorf("企业运行时校验失败：%s；请重新安装", item.path)
					}
				}
				if openErr != nil {
					firstFailure.Do(func() { failure = openErr; cancel() })
					return
				}
			}
		})
	}
	verified := 0
	err = filepath.WalkDir(root, func(path string, entry fs.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if entry.Type()&os.ModeSymlink != 0 {
			return errors.New("企业运行时不允许符号链接")
		}
		if entry.IsDir() {
			return nil
		}
		relative, err := filepath.Rel(root, path)
		if err != nil {
			return err
		}
		relative = filepath.ToSlash(relative)
		if relative == "company-runtime-manifest.json" {
			return nil
		}
		want, ok := m.Files[relative]
		if !ok {
			return fmt.Errorf("企业运行时包含未校验文件：%s", relative)
		}
		if !entry.Type().IsRegular() {
			return fmt.Errorf("企业运行时文件不可读：%s", relative)
		}
		select {
		case checks <- check{relative, want}:
			verified++
			return nil
		case <-ctx.Done():
			return ctx.Err()
		}
	})
	if err != nil {
		cancel()
	}
	close(checks)
	group.Wait()
	if failure != nil {
		return failure
	}
	if err != nil {
		return err
	}
	if verified != len(m.Files) {
		return errors.New("企业运行时文件缺失，请重新安装")
	}
	if !launchReady(root) {
		return errors.New("企业客户端缺少可执行的 Python 运行时")
	}
	return nil
}

// Company payloads reject symlinks before extraction, preventing archive entries
// from redirecting later writes outside the isolated runtime directory.
func verifyCompanyZip(path string) error {
	r, err := zip.OpenReader(path)
	if err != nil {
		return err
	}
	defer r.Close()
	for _, f := range r.File {
		if f.Mode()&os.ModeSymlink != 0 {
			return errors.New("企业工具包不得包含符号链接，请使用打包脚本生成的完整工具包")
		}
	}
	return nil
}

type companyPortableKey struct {
	archive, home  string
	size, modified int64
}

type companyPortableCache struct {
	sync.Mutex
	key  companyPortableKey
	root string
}

var readyCompanyPortable companyPortableCache

func (c *companyPortableCache) prepare(zipPath, home string, prepare func(string, string) (string, error)) (string, error) {
	c.Lock()
	defer c.Unlock()
	info, err := os.Stat(zipPath)
	if err != nil {
		return "", err
	}
	key := companyPortableKey{zipPath, home, info.Size(), info.ModTime().UnixNano()}
	if c.key == key && c.root != "" && launchReady(c.root) {
		return c.root, nil
	}
	root, err := prepare(zipPath, home)
	if err != nil {
		return "", err
	}
	c.key, c.root = key, root
	return root, nil
}

func ensureCompanyPortable() (string, error) {
	zipPath, err := bundledPortableZip()
	if err != nil {
		return "", errors.New("此客户端未包含本地 Python、文档和 FFmpeg 工具包，请安装完整企业客户端；通讯录和员工聊天仍可使用")
	}
	return readyCompanyPortable.prepare(zipPath, octopHome(), prepareCompanyPortable)
}

func prepareCompanyPortable(zipPath, home string) (string, error) {
	return prepareCompanyPortableChecked(zipPath, home, validateCompanyRuntime)
}

func prepareCompanyPortableChecked(zipPath, home string, validate func(string) error) (string, error) {
	archive, err := companyArchiveStamp(zipPath)
	if err != nil {
		return "", err
	}
	if root := cachedCompanyRuntime(home, archive); root != "" {
		return root, nil
	}
	// A failed or interrupted check must never leave an old success reusable.
	_ = os.Remove(companyCheckPath(home, archive.Path))
	file, err := os.Open(zipPath)
	if err != nil {
		return "", err
	}
	h := sha256.New()
	_, err = io.Copy(h, file)
	file.Close()
	if err != nil {
		return "", err
	}
	// Content-addressed installs keep existing runtimes and account state intact
	// during upgrade/rollback; the company runner never initializes ~/.octop/portable.
	digest := hex.EncodeToString(h.Sum(nil))
	root := filepath.Join(home, "company-runtime", "tools", digest)
	if err = validate(root); err == nil {
		recordCompanyRuntime(home, archive, digest, root)
		return root, nil
	}
	if err = verifyCompanyZip(zipPath); err != nil {
		return "", err
	}
	next := root + ".new"
	if err = unzipGreen(zipPath, next); err != nil {
		return "", err
	}
	if err = validate(next); err != nil {
		_ = os.RemoveAll(next)
		return "", err
	}
	if err = os.MkdirAll(filepath.Dir(root), 0700); err != nil {
		return "", err
	}
	_ = os.RemoveAll(root)
	if err = os.Rename(next, root); err != nil {
		return "", err
	}
	recordCompanyRuntime(home, archive, digest, root)
	return root, nil
}
