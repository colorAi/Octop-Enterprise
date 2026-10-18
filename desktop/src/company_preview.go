package main

import (
	"bytes"
	"container/list"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"errors"
	"image"
	_ "image/gif"
	_ "image/jpeg"
	"image/png"
	"io"
	"os"
	"path/filepath"
	"strings"
	"sync"

	"golang.org/x/image/draw"
	_ "golang.org/x/image/webp"
)

const maxRuntimePreviewBytes = 16 * 1024 * 1024
const maxRuntimePreviewPixels = 24 * 1024 * 1024
const runtimePreviewSide = 1200

// Limit concurrent decoders; even valid compressed pictures can be expensive.
var runtimePreviewSlots = make(chan struct{}, 2)

// Cache derivatives only. Every hit still verifies the current source bytes
// inside the authorized directory before it can cross the native bridge.
var runtimePreviews = runtimePreviewCache{limit: 32 * 1024 * 1024}

type runtimePreviewEntry struct {
	key     string
	preview runtimeImagePreview
}

type runtimePreviewCache struct {
	mu      sync.Mutex
	entries map[string]*list.Element
	order   list.List
	bytes   int
	limit   int
}

func (c *runtimePreviewCache) get(key string) (runtimeImagePreview, bool) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if entry := c.entries[key]; entry != nil {
		c.order.MoveToFront(entry)
		return entry.Value.(runtimePreviewEntry).preview, true
	}
	return runtimeImagePreview{}, false
}

func (c *runtimePreviewCache) put(key string, preview runtimeImagePreview) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if len(preview.DataURL) > c.limit {
		return
	}
	if c.entries == nil {
		c.entries = make(map[string]*list.Element)
	}
	if entry := c.entries[key]; entry != nil {
		c.bytes -= len(entry.Value.(runtimePreviewEntry).preview.DataURL)
		c.order.Remove(entry)
	}
	c.entries[key] = c.order.PushFront(runtimePreviewEntry{key, preview})
	c.bytes += len(preview.DataURL)
	for c.bytes > c.limit {
		entry := c.order.Back()
		value := entry.Value.(runtimePreviewEntry)
		c.bytes -= len(value.preview.DataURL)
		delete(c.entries, value.key)
		c.order.Remove(entry)
	}
}

type runtimeImagePreview struct {
	DataURL string `json:"data_url"`
	Width   int    `json:"width"`
	Height  int    `json:"height"`
}

// Only a bounded, hash-verified raster derivative crosses the native bridge.
// Re-encoding strips metadata and trailing content; the original is never sent,
// changed, uploaded, or interpreted as SVG/HTML.
func previewRuntimeImage(workspace, path, digest string) (runtimeImagePreview, error) {
	var result runtimeImagePreview
	expected, err := hex.DecodeString(digest)
	if err != nil || len(expected) != sha256.Size {
		return result, errors.New("图片成果缺少有效 SHA-256 校验值")
	}
	if workspace == "" || !filepath.IsAbs(workspace) || !filepath.IsAbs(path) {
		return result, errors.New("图片不在已授权的本机工作目录中")
	}
	switch strings.ToLower(filepath.Ext(path)) {
	case ".png", ".jpg", ".jpeg", ".gif", ".webp":
	default:
		return result, errors.New("仅支持 PNG、JPEG、GIF 和 WebP 图片预览，不支持 SVG 或 HTML")
	}
	canonicalRoot, err := filepath.EvalSymlinks(workspace)
	if err != nil {
		return result, errors.New("本机工作目录不可用")
	}
	canonicalFile, err := filepath.EvalSymlinks(path)
	if err != nil {
		return result, errors.New("图片尚未同步到本机，或文件已移动")
	}
	relative, err := filepath.Rel(canonicalRoot, canonicalFile)
	if err != nil || relative == ".." || strings.HasPrefix(relative, ".."+string(os.PathSeparator)) {
		return result, errors.New("图片不在已授权的本机工作目录中")
	}
	// Queue history thumbnails instead of failing the third simultaneous image.
	runtimePreviewSlots <- struct{}{}
	defer func() { <-runtimePreviewSlots }()
	root, err := os.OpenRoot(canonicalRoot)
	if err != nil {
		return result, errors.New("本机工作目录不可用")
	}
	defer root.Close()
	// Root.Open is traversal-resistant, including symlinks changed during a read.
	file, err := root.Open(relative)
	if err != nil {
		return result, errors.New("图片不在已授权目录内，或尚未同步到本机")
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil || !info.Mode().IsRegular() {
		return result, errors.New("本机图片文件不可读")
	}
	if info.Size() > maxRuntimePreviewBytes {
		return result, errors.New("图片超过 16 MB，请使用打开文件位置查看原图")
	}
	data, err := io.ReadAll(io.LimitReader(file, maxRuntimePreviewBytes+1))
	if err != nil || len(data) > maxRuntimePreviewBytes {
		return result, errors.New("图片读取失败或超过预览大小限制")
	}
	actual := sha256.Sum256(data)
	if !bytes.Equal(actual[:], expected) {
		return result, errors.New("本机图片与成果记录的 SHA-256 不一致，请核对文件")
	}
	cacheKey := canonicalRoot + "\x00" + canonicalFile + "\x00" + hex.EncodeToString(actual[:])
	if cached, ok := runtimePreviews.get(cacheKey); ok {
		return cached, nil
	}
	config, format, err := image.DecodeConfig(bytes.NewReader(data))
	if err != nil || !runtimeRasterFormat(format) {
		return result, errors.New("仅支持 PNG、JPEG、GIF 和 WebP 图片预览，不支持 SVG 或 HTML")
	}
	if config.Width <= 0 || config.Height <= 0 || config.Width > maxRuntimePreviewPixels/config.Height {
		return result, errors.New("图片像素过大，请使用打开文件位置查看原图")
	}
	decoded, format, err := image.Decode(bytes.NewReader(data))
	if err != nil || !runtimeRasterFormat(format) {
		return result, errors.New("图片内容损坏，无法预览")
	}
	width, height := config.Width, config.Height
	if width > runtimePreviewSide || height > runtimePreviewSide {
		if width >= height {
			height = max(1, height*runtimePreviewSide/width)
			width = runtimePreviewSide
		} else {
			width = max(1, width*runtimePreviewSide/height)
			height = runtimePreviewSide
		}
	}
	thumbnail := image.NewNRGBA(image.Rect(0, 0, width, height))
	draw.ApproxBiLinear.Scale(thumbnail, thumbnail.Bounds(), decoded, decoded.Bounds(), draw.Src, nil)
	var output bytes.Buffer
	if err = png.Encode(&output, thumbnail); err != nil {
		return result, errors.New("无法生成本机图片预览")
	}
	result = runtimeImagePreview{DataURL: "data:image/png;base64," + base64.StdEncoding.EncodeToString(output.Bytes()), Width: width, Height: height}
	runtimePreviews.put(cacheKey, result)
	return result, nil
}
func runtimeRasterFormat(format string) bool {
	return format == "png" || format == "jpeg" || format == "gif" || format == "webp"
}

// Caller holds companyMu. Both account nonce and directory generation must match.
func (a *App) runtimePreviewScopeCurrent(nonce, base, workspace string, epoch uint64) bool {
	return a.workspaceNonce == nonce && a.companyURL == base && a.workspacePath == workspace && a.runtimeEpoch == epoch && a.workspaceGrant != nil
}
