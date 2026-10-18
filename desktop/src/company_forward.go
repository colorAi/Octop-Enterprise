package main

import (
	"bytes"
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"mime"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"
)

const maxForwardFileBytes = 50 << 20

var forwardMediaID = regexp.MustCompile(`^[A-Za-z0-9_-]{1,160}$`)

type runtimeForwardFile struct {
	Path   string `json:"path"`
	Size   int    `json:"size"`
	SHA256 string `json:"sha256"`
}

// Read the recorded original through a traversal-resistant directory handle.
func readForwardArtifact(workspace, path, digest string) ([]byte, error) {
	expected, err := hex.DecodeString(digest)
	if err != nil || len(expected) != sha256.Size || !filepath.IsAbs(workspace) || !filepath.IsAbs(path) {
		return nil, errors.New("成果缺少有效校验值，或不在已授权的本机工作目录中")
	}
	canonicalRoot, err := filepath.EvalSymlinks(workspace)
	if err != nil {
		return nil, errors.New("本机工作目录不可用")
	}
	canonicalFile, err := filepath.EvalSymlinks(path)
	if err != nil {
		return nil, errors.New("成果文件已移动或尚未同步到本机")
	}
	relative, err := filepath.Rel(canonicalRoot, canonicalFile)
	if err != nil || relative == ".." || strings.HasPrefix(relative, ".."+string(os.PathSeparator)) {
		return nil, errors.New("成果不在已授权的本机工作目录中")
	}
	root, err := os.OpenRoot(canonicalRoot)
	if err != nil {
		return nil, errors.New("本机工作目录不可用")
	}
	defer root.Close()
	file, err := root.Open(relative)
	if err != nil {
		return nil, errors.New("成果文件不可读")
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil || !info.Mode().IsRegular() || info.Size() <= 0 || info.Size() > maxForwardFileBytes {
		return nil, errors.New("只能转发大于 0 且不超过 50 MB 的成果文件")
	}
	data, err := io.ReadAll(io.LimitReader(file, maxForwardFileBytes+1))
	if err != nil || len(data) == 0 || len(data) > maxForwardFileBytes {
		return nil, errors.New("成果文件读取失败或超过 50 MB")
	}
	actual := sha256.Sum256(data)
	if !bytes.Equal(actual[:], expected) {
		return nil, errors.New("成果文件已改变，SHA-256 与消息中的记录不一致，请核对文件")
	}
	return data, nil
}

// Only company media endpoints are reachable; file bytes never cross the JS bridge.
func (a *App) transferRuntimeArtifact(nonce, base, method string, raw json.RawMessage) (any, error) {
	var params struct {
		Path          string `json:"path"`
		SHA256        string `json:"sha256"`
		Token         string `json:"token"`
		AssetID       string `json:"asset_id"`
		Filename      string `json:"filename"`
		ThreadID      string `json:"thread_id"`
		AgentID       string `json:"agent_id"`
		WorkspacePath string `json:"workspace_path"`
	}
	if json.Unmarshal(raw, &params) != nil || params.Token == "" || len(params.Token) > 16384 || strings.ContainsAny(params.Token, "\r\n") {
		return nil, errors.New("文件转发请求无效，请重新登录后重试")
	}
	a.companyMu.Lock()
	workspace, epoch := a.workspacePath, a.runtimeEpoch
	current := a.runtimePreviewScopeCurrent(nonce, base, workspace, epoch)
	a.companyMu.Unlock()
	if !current || workspace == "" {
		return nil, errors.New("请先授权当前账号的本地工作目录")
	}
	check := func() error {
		a.companyMu.Lock()
		defer a.companyMu.Unlock()
		if !a.runtimePreviewScopeCurrent(nonce, base, workspace, epoch) {
			return errors.New("账号或工作目录已切换，文件转发已取消")
		}
		return nil
	}
	ctx, cancel := context.WithTimeout(context.Background(), 50*time.Second)
	defer cancel()
	client := &http.Client{CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	request := func(verb, path, contentType string, body io.Reader) (*http.Response, error) {
		if err := check(); err != nil {
			return nil, err
		}
		req, err := http.NewRequestWithContext(ctx, verb, base+path, body)
		if err != nil {
			return nil, errors.New("文件转发请求无效")
		}
		req.Header.Set("Authorization", "Bearer "+params.Token)
		if contentType != "" {
			req.Header.Set("Content-Type", contentType)
		}
		response, err := client.Do(req)
		if err != nil {
			return nil, errors.New("文件传输失败，请检查公司连接后重试")
		}
		if response.StatusCode < 200 || response.StatusCode >= 300 {
			response.Body.Close()
			return nil, fmt.Errorf("文件传输未获授权或暂不可用（HTTP %d）", response.StatusCode)
		}
		return response, nil
	}
	if method == "upload_artifact" {
		data, err := readForwardArtifact(workspace, params.Path, params.SHA256)
		if err != nil {
			return nil, err
		}
		mediaType := strings.Split(mime.TypeByExtension(filepath.Ext(params.Path)), ";")[0]
		if mediaType == "" {
			mediaType = "application/octet-stream"
		}
		purpose := "chat_file"
		if len(data) <= 10<<20 && (mediaType == "image/png" || mediaType == "image/jpeg" || mediaType == "image/webp" || mediaType == "image/gif") {
			purpose = "chat_image"
		}
		payload, _ := json.Marshal(map[string]any{"purpose": purpose, "mime": mediaType, "filename": filepath.Base(params.Path), "size_bytes": len(data), "sha256": params.SHA256})
		response, err := request("POST", "/api/im/media/uploads", "application/json", bytes.NewReader(payload))
		if err != nil {
			return nil, err
		}
		var envelope struct {
			Data struct {
				UploadID string `json:"upload_id"`
				AssetID  string `json:"asset_id"`
			} `json:"data"`
		}
		err = json.NewDecoder(io.LimitReader(response.Body, 64<<10)).Decode(&envelope)
		response.Body.Close()
		if err != nil || !forwardMediaID.MatchString(envelope.Data.UploadID) || !forwardMediaID.MatchString(envelope.Data.AssetID) {
			return nil, errors.New("公司返回了无效的附件上传信息")
		}
		path := "/api/im/media/uploads/" + envelope.Data.UploadID
		completed := false
		defer func() {
			if !completed {
				if response, err := request("DELETE", path, "", nil); err == nil {
					response.Body.Close()
				}
			}
		}()
		for _, step := range []struct {
			verb, suffix, contentType string
			body                      io.Reader
		}{{"PUT", "/content", "application/octet-stream", bytes.NewReader(data)}, {"POST", "/complete", "application/json", nil}} {
			response, err = request(step.verb, path+step.suffix, step.contentType, step.body)
			if err != nil {
				return nil, err
			}
			response.Body.Close()
		}
		if err := check(); err != nil {
			return nil, err
		}
		completed = true
		return envelope.Data, nil
	}
	if !forwardMediaID.MatchString(params.ThreadID) {
		return nil, errors.New("目标 Agent 会话编号无效")
	}
	root, err := os.OpenRoot(workspace)
	if err != nil {
		return nil, errors.New("本机工作目录不可用")
	}
	defer root.Close()
	if params.Path != "" {
		data, err := readForwardArtifact(workspace, params.Path, params.SHA256)
		if err != nil {
			return nil, err
		}
		a.companyMu.Lock()
		defer a.companyMu.Unlock()
		if !a.runtimePreviewScopeCurrent(nonce, base, workspace, epoch) {
			return nil, errors.New("账号或工作目录已切换，附件复制已取消")
		}
		return saveForwardAttachment(root, workspace, params.ThreadID, filepath.Base(params.Path), data)
	}
	downloadPath := ""
	if params.AgentID != "" {
		if !forwardMediaID.MatchString(params.AgentID) || params.WorkspacePath == "" || len(params.WorkspacePath) > 4096 || strings.ContainsAny(params.WorkspacePath, "\r\n") {
			return nil, errors.New("Agent 附件路径无效")
		}
		// The company workspace endpoint enforces ownership and workspace bounds.
		// No caller-controlled origin, redirect or arbitrary URL is accepted here.
		downloadPath = "/api/agents/" + params.AgentID + "/workspace/download?path=" + url.QueryEscape(params.WorkspacePath)
	} else {
		if !forwardMediaID.MatchString(params.AssetID) {
			return nil, errors.New("附件编号无效")
		}
		downloadPath = "/api/im/media/" + params.AssetID + "?kind=original"
	}
	response, err := request("GET", downloadPath, "", nil)
	if err != nil {
		return nil, err
	}
	defer response.Body.Close()
	if response.ContentLength > maxForwardFileBytes {
		return nil, errors.New("附件超过 50 MB，无法转给本地 Agent")
	}
	data, err := io.ReadAll(io.LimitReader(response.Body, maxForwardFileBytes+1))
	if err != nil || len(data) == 0 || len(data) > maxForwardFileBytes {
		return nil, errors.New("附件下载失败或超过 50 MB")
	}
	if _, disposition, err := mime.ParseMediaType(response.Header.Get("Content-Disposition")); err == nil && disposition["filename"] != "" {
		params.Filename = disposition["filename"]
	}
	if params.Filename == "" {
		params.Filename = "attachment"
		mediaType := strings.Split(response.Header.Get("Content-Type"), ";")[0]
		extension := map[string]string{"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif", "image/webp": ".webp"}[mediaType]
		if extension != "" {
			params.Filename += extension
		} else if extensions, _ := mime.ExtensionsByType(mediaType); len(extensions) > 0 {
			params.Filename += extensions[0]
		}
	}
	a.companyMu.Lock()
	defer a.companyMu.Unlock()
	if !a.runtimePreviewScopeCurrent(nonce, base, workspace, epoch) {
		return nil, errors.New("账号或工作目录已切换，附件保存已取消")
	}
	return saveForwardAttachment(root, workspace, params.ThreadID, params.Filename, data)
}

func saveForwardAttachment(root *os.Root, workspace, thread, name string, data []byte) (runtimeForwardFile, error) {
	name = filepath.Base(strings.ReplaceAll(name, "\\", "/"))
	name = strings.Map(func(r rune) rune {
		if r < 32 || strings.ContainsRune(`<>:"/\|?*`, r) {
			return '_'
		}
		return r
	}, name)
	name = strings.Trim(name, ". ")
	if name == "" || len(name) > 180 {
		name = "attachment"
	}
	var random [16]byte
	if _, err := rand.Read(random[:]); err != nil {
		return runtimeForwardFile{}, errors.New("无法创建本地附件")
	}
	if !forwardMediaID.MatchString(thread) {
		return runtimeForwardFile{}, errors.New("目标 Agent 会话编号无效")
	}
	if info, err := root.Lstat(thread); err == nil && info.Mode()&os.ModeSymlink != 0 {
		return runtimeForwardFile{}, errors.New("目标 Agent 任务目录不能是符号链接")
	}
	directory := filepath.Join(thread, "inbound")
	if err := root.MkdirAll(directory, 0700); err != nil {
		return runtimeForwardFile{}, errors.New("无法创建本地附件目录")
	}
	relative := filepath.Join(directory, hex.EncodeToString(random[:])+"-"+name)
	file, err := root.OpenFile(relative, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if err != nil {
		return runtimeForwardFile{}, errors.New("无法保存本地附件")
	}
	_, err = file.Write(data)
	closeErr := file.Close()
	if err != nil || closeErr != nil {
		_ = root.Remove(relative)
		return runtimeForwardFile{}, errors.New("本地附件保存失败，请检查磁盘空间")
	}
	digest := sha256.Sum256(data)
	return runtimeForwardFile{Path: filepath.Join(workspace, relative), Size: len(data), SHA256: hex.EncodeToString(digest[:])}, nil
}
