package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/base64"
	"encoding/binary"
	"encoding/hex"
	"hash/crc32"
	"image"
	"image/color"
	"image/png"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"octop.desktop/internal/company"
)

func previewFixture(t *testing.T, root, name string, data []byte) (string, string) {
	t.Helper()
	path := filepath.Join(root, name)
	if err := os.WriteFile(path, data, 0600); err != nil {
		t.Fatal(err)
	}
	h := sha256.Sum256(data)
	return path, hex.EncodeToString(h[:])
}
func previewPNG(t *testing.T, width, height int) []byte {
	t.Helper()
	im := image.NewNRGBA(image.Rect(0, 0, width, height))
	im.Set(0, 0, color.NRGBA{R: 255, A: 255})
	var b bytes.Buffer
	if err := png.Encode(&b, im); err != nil {
		t.Fatal(err)
	}
	return b.Bytes()
}
func TestRuntimePreviewReturnsBoundedRasterAndPreservesOriginal(t *testing.T) {
	root := t.TempDir()
	source := append(previewPNG(t, 2400, 1200), []byte("PRIVATE-TRAILING-METADATA")...)
	path, digest := previewFixture(t, root, "image.PNG", source)
	got, err := previewRuntimeImage(root, path, digest)
	if err != nil {
		t.Fatal(err)
	}
	if got.Width != 1200 || got.Height != 600 {
		t.Fatalf("wrong dimensions: %+v", got)
	}
	data, err := base64.StdEncoding.DecodeString(strings.TrimPrefix(got.DataURL, "data:image/png;base64,"))
	if err != nil {
		t.Fatal(err)
	}
	if bytes.Contains(data, []byte("PRIVATE")) {
		t.Fatal("trailing private bytes exposed")
	}
	decoded, err := png.Decode(bytes.NewReader(data))
	if err != nil || decoded.Bounds().Dx() != 1200 {
		t.Fatal("invalid preview raster", err)
	}
	actual, _ := os.ReadFile(path)
	if !bytes.Equal(actual, source) {
		t.Fatal("original changed")
	}
}
func TestRuntimePreviewRejectsUnverifiedOrOutsideFiles(t *testing.T) {
	root := t.TempDir()
	data := previewPNG(t, 1, 1)
	path, digest := previewFixture(t, root, "ok.png", data)
	outside, _ := previewFixture(t, t.TempDir(), "private.png", data)
	if err := os.Symlink(outside, filepath.Join(root, "escape.png")); err != nil {
		t.Fatal(err)
	}
	for name, c := range map[string][3]string{
		"missing_hash": {root, path, ""}, "wrong_hash": {root, path, strings.Repeat("0", 64)}, "outside": {root, outside, digest}, "symlink": {root, filepath.Join(root, "escape.png"), digest}, "missing": {root, filepath.Join(root, "missing.png"), digest}, "relative": {root, "ok.png", digest}, "ungranted": {"", path, digest},
	} {
		t.Run(name, func(t *testing.T) {
			if _, err := previewRuntimeImage(c[0], c[1], c[2]); err == nil {
				t.Fatal("unsafe read allowed")
			}
		})
	}
}
func TestRuntimePreviewRejectsActiveAndOversizeContent(t *testing.T) {
	root := t.TempDir()
	for _, name := range []string{"image.svg", "image.html", "fake.png"} {
		path, digest := previewFixture(t, root, name, []byte(`<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>`))
		if _, err := previewRuntimeImage(root, path, digest); err == nil {
			t.Fatal("active content accepted", name)
		}
	}
	path, digest := previewFixture(t, root, "huge.png", []byte("x"))
	if err := os.Truncate(path, maxRuntimePreviewBytes+1); err != nil {
		t.Fatal(err)
	}
	if _, err := previewRuntimeImage(root, path, digest); err == nil || !strings.Contains(err.Error(), "16 MB") {
		t.Fatal("oversize read accepted", err)
	}
	// Change the PNG IHDR dimensions and CRC; DecodeConfig can inspect this without allocating pixels.
	large := previewPNG(t, 1, 1)
	binary.BigEndian.PutUint32(large[16:20], uint32(maxRuntimePreviewPixels))
	binary.BigEndian.PutUint32(large[20:24], 2)
	binary.BigEndian.PutUint32(large[29:33], crc32.ChecksumIEEE(large[12:29]))
	path, digest = previewFixture(t, root, "pixels.png", large)
	if _, err := previewRuntimeImage(root, path, digest); err == nil || !strings.Contains(err.Error(), "像素") {
		t.Fatal("pixel limit not enforced", err)
	}
}
func TestRuntimePreviewAccountAndDirectoryEpoch(t *testing.T) {
	a := &App{workspaceNonce: "nonce", companyURL: "https://one", workspacePath: "/project", runtimeEpoch: 3, workspaceGrant: &company.Grant{}}
	if !a.runtimePreviewScopeCurrent("nonce", "https://one", "/project", 3) {
		t.Fatal("current scope rejected")
	}
	for _, c := range []struct {
		nonce, base, path string
		epoch             uint64
	}{{"old", "https://one", "/project", 3}, {"nonce", "https://two", "/project", 3}, {"nonce", "https://one", "/other", 3}, {"nonce", "https://one", "/project", 2}} {
		if a.runtimePreviewScopeCurrent(c.nonce, c.base, c.path, c.epoch) {
			t.Fatal("stale scope allowed")
		}
	}
	a.workspaceGrant = nil
	if a.runtimePreviewScopeCurrent("nonce", "https://one", "/project", 3) {
		t.Fatal("revoked grant allowed")
	}
	if _, _, _, ok := parseRuntimeEvent("desktop:runtime:nonce:1:preview_image:%7B%7D", "nonce"); !ok {
		t.Fatal("preview request rejected")
	}
	if _, _, _, ok := parseRuntimeEvent("desktop:runtime:old:1:preview_image:%7B%7D", "nonce"); ok {
		t.Fatal("stale nonce allowed")
	}
}
func TestRuntimeImageFailureMessagesDoNotSuggestResubmission(t *testing.T) {
	for code, want := range map[string]string{"company_image_download_failed": "勿重复提交", "company_image_provider_timeout": "不会自动重复生成", "company_image_provider_unavailable": "暂时无法连接"} {
		if !strings.Contains(runtimeErrorText(code), want) {
			t.Fatal(code)
		}
	}
}

func TestRuntimePreviewCacheRechecksChangedSourceAndAuthorization(t *testing.T) {
	root := t.TempDir()
	data := previewPNG(t, 12, 12)
	path, digest := previewFixture(t, root, "cached.png", data)
	first, err := previewRuntimeImage(root, path, digest)
	if err != nil {
		t.Fatal(err)
	}
	again, err := previewRuntimeImage(root, path, digest)
	if err != nil || first != again {
		t.Fatal("cached derivative changed", err)
	}
	if _, err = previewRuntimeImage("", path, digest); err == nil {
		t.Fatal("cache bypassed directory authorization")
	}
	if err = os.WriteFile(path, []byte("changed"), 0600); err != nil {
		t.Fatal(err)
	}
	if _, err = previewRuntimeImage(root, path, digest); err == nil {
		t.Fatal("cache hid a source checksum mismatch")
	}
}

func TestRuntimePreviewCacheEvictsLeastRecentlyUsedBytes(t *testing.T) {
	c := runtimePreviewCache{limit: 6}
	c.put("one", runtimeImagePreview{DataURL: "111"})
	c.put("two", runtimeImagePreview{DataURL: "222"})
	if _, ok := c.get("one"); !ok {
		t.Fatal("missing first derivative")
	}
	c.put("three", runtimeImagePreview{DataURL: "333"})
	if _, ok := c.get("two"); ok {
		t.Fatal("old derivative not evicted")
	}
	c.put("one", runtimeImagePreview{DataURL: "1"})
	if c.bytes != 4 {
		t.Fatalf("incorrect memory bound: %d", c.bytes)
	}
}
