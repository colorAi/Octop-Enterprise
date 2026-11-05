package companyprojects

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"unicode/utf8"
)

type FileEntry struct {
	Path  string `json:"path"`
	Name  string `json:"name"`
	IsDir bool   `json:"is_dir"`
	Size  int64  `json:"size"`
}
type FileList struct {
	Items     []FileEntry `json:"items"`
	Truncated bool        `json:"truncated"`
}
type FileContent struct {
	Path   string  `json:"path"`
	Size   int64   `json:"size"`
	SHA256 string  `json:"sha256"`
	Text   *string `json:"text,omitempty"`
}

var ignoredDirectories = map[string]bool{".git": true, "node_modules": true, ".venv": true, "venv": true, "__pycache__": true, ".octop": true, "dist": true, "build": true}

func relativeFile(root, path string) (string, error) {
	if path == "" {
		return ".", nil
	}
	if filepath.IsAbs(path) {
		var err error
		path, err = filepath.Rel(root, path)
		if err != nil {
			return "", err
		}
	}
	path = filepath.Clean(filepath.FromSlash(path))
	if path != "." && !filepath.IsLocal(path) {
		return "", errors.New("project_path_invalid")
	}
	return path, nil
}

// Reads are rooted and bounded. Symlinks are not traversed, and large vendor
// directories are pruned before recursive mention searches.
func ListFiles(project, path, query string) (FileList, error) {
	out := FileList{Items: []FileEntry{}}
	rel, err := relativeFile(project, path)
	if err != nil {
		return out, err
	}
	root, err := os.OpenRoot(project)
	if err != nil {
		return out, err
	}
	defer root.Close()
	query = strings.ToLower(strings.TrimSpace(query))
	queue := []string{rel}
	visited := 0
	for len(queue) > 0 {
		dir := queue[0]
		queue = queue[1:]
		info, err := root.Lstat(dir)
		if err != nil {
			return out, err
		}
		if !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
			return out, errors.New("project_path_invalid")
		}
		file, err := root.Open(dir)
		if err != nil {
			return out, err
		}
		entries, readErr := file.ReadDir(10001 - visited)
		file.Close()
		if readErr != nil && readErr != io.EOF {
			return out, readErr
		}
		sort.Slice(entries, func(i, j int) bool { return entries[i].Name() < entries[j].Name() })
		for _, entry := range entries {
			visited++
			if visited > 10000 || len(out.Items) >= 500 {
				out.Truncated = true
				return out, nil
			}
			if entry.Type()&os.ModeSymlink != 0 {
				continue
			}
			full := filepath.Join(dir, entry.Name())
			if entry.IsDir() && ignoredDirectories[entry.Name()] {
				continue
			}
			if query != "" {
				if entry.IsDir() {
					if strings.Count(full, string(os.PathSeparator)) < 16 {
						queue = append(queue, full)
					}
					continue
				}
				if !strings.Contains(strings.ToLower(filepath.ToSlash(full)), query) {
					continue
				}
			}
			info, err := entry.Info()
			if err != nil {
				continue
			}
			if !info.IsDir() && !info.Mode().IsRegular() {
				continue
			}
			out.Items = append(out.Items, FileEntry{Path: filepath.ToSlash(full), Name: entry.Name(), IsDir: info.IsDir(), Size: info.Size()})
		}
		if query == "" {
			break
		}
	}
	sort.Slice(out.Items, func(i, j int) bool {
		a, b := out.Items[i], out.Items[j]
		if a.IsDir != b.IsDir {
			return a.IsDir
		}
		return a.Path < b.Path
	})
	return out, nil
}
func ReadFile(project, path string) (FileContent, error) {
	var out FileContent
	rel, err := relativeFile(project, path)
	if err != nil {
		return out, err
	}
	root, err := os.OpenRoot(project)
	if err != nil {
		return out, err
	}
	defer root.Close()
	file, err := root.Open(rel)
	if err != nil {
		return out, err
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil || !info.Mode().IsRegular() {
		return out, errors.New("project_file_unavailable")
	}
	if info.Size() > 50<<20 {
		return out, errors.New("文件超过 50 MB，请在本地文件夹中查看")
	}
	data, err := io.ReadAll(io.LimitReader(file, (50<<20)+1))
	if err != nil || len(data) > 50<<20 {
		return out, errors.New("project_file_unavailable")
	}
	digest := sha256.Sum256(data)
	out = FileContent{Path: filepath.Join(project, rel), Size: int64(len(data)), SHA256: hex.EncodeToString(digest[:])}
	if len(data) <= 256<<10 && utf8.Valid(data) && !strings.ContainsRune(string(data), 0) {
		text := string(data)
		out.Text = &text
	}
	return out, nil
}
