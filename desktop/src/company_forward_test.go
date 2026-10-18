package main

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"octop.desktop/internal/company"
)

func forwardTestApp(t *testing.T, handler http.HandlerFunc) *App {
	t.Helper()
	server := httptest.NewServer(handler)
	t.Cleanup(server.Close)
	return &App{companyURL: server.URL, workspaceNonce: "nonce", workspacePath: t.TempDir(), workspaceGrant: &company.Grant{}}
}

func forwardParams(t *testing.T, values map[string]string) json.RawMessage {
	t.Helper()
	values["token"] = "test-token"
	values["thread_id"] = "thr_target"
	data, err := json.Marshal(values)
	if err != nil {
		t.Fatal(err)
	}
	return data
}

func TestForwardArtifactRejectsOutsideChangedAndOversizedFiles(t *testing.T) {
	workspace := t.TempDir()
	path, digest := previewFixture(t, workspace, "report.pdf", []byte("original document"))
	outside, _ := previewFixture(t, t.TempDir(), "private.pdf", []byte("original document"))
	for name, input := range map[string][3]string{
		"outside": {workspace, outside, digest}, "changed": {workspace, path, strings.Repeat("0", 64)},
		"no_hash": {workspace, path, ""}, "no_grant": {"", path, digest}, "relative": {workspace, "report.pdf", digest},
	} {
		t.Run(name, func(t *testing.T) {
			if _, err := readForwardArtifact(input[0], input[1], input[2]); err == nil {
				t.Fatal("unverified file accepted")
			}
		})
	}
	if err := os.Truncate(path, maxForwardFileBytes+1); err != nil {
		t.Fatal(err)
	}
	if _, err := readForwardArtifact(workspace, path, digest); err == nil {
		t.Fatal("oversized file accepted")
	}
}

func TestForwardNativeOriginalUsesAuthenticatedMediaUpload(t *testing.T) {
	var uploaded []byte
	var metadata map[string]any
	var steps []string
	a := forwardTestApp(t, func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer test-token" {
			t.Error("missing authenticated session")
		}
		steps = append(steps, r.Method+" "+r.URL.Path)
		switch r.Method + " " + r.URL.Path {
		case "POST /api/im/media/uploads":
			if err := json.NewDecoder(r.Body).Decode(&metadata); err != nil {
				t.Error(err)
			}
			_, _ = io.WriteString(w, `{"data":{"upload_id":"upl_test","asset_id":"asm_test"}}`)
		case "PUT /api/im/media/uploads/upl_test/content":
			uploaded, _ = io.ReadAll(r.Body)
		case "POST /api/im/media/uploads/upl_test/complete":
		default:
			t.Error("unexpected endpoint", r.Method, r.URL.Path)
		}
	})
	source := append(previewPNG(t, 3, 3), []byte("original trailing bytes")...)
	path, digest := previewFixture(t, a.workspacePath, "result.png", source)
	_, err := a.performRuntimeAction("nonce", a.companyURL, "upload_artifact", forwardParams(t, map[string]string{"path": path, "sha256": digest}))
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(uploaded, source) || metadata["sha256"] != digest || metadata["purpose"] != "chat_image" || metadata["filename"] != "result.png" {
		t.Fatal("original bytes or attachment metadata changed")
	}
	if len(steps) != 3 {
		t.Fatal("upload did not complete", steps)
	}
}

func TestForwardImportKeepsBytesInsideWorkspaceWithSafeUniqueNames(t *testing.T) {
	source := []byte("company document")
	a := forwardTestApp(t, func(w http.ResponseWriter, r *http.Request) {
		if r.URL.RequestURI() != "/api/im/media/asm_test?kind=original" || r.Header.Get("Authorization") != "Bearer test-token" {
			t.Error("unexpected media request")
		}
		w.Header().Set("Content-Type", "application/pdf")
		w.Header().Set("Content-Disposition", `attachment; filename="../../report.pdf"`)
		_, _ = w.Write(source)
	})
	var previous string
	for range 2 {
		result, err := a.performRuntimeAction("nonce", a.companyURL, "import_attachment", forwardParams(t, map[string]string{"asset_id": "asm_test"}))
		if err != nil {
			t.Fatal(err)
		}
		file := result.(runtimeForwardFile)
		if filepath.Dir(file.Path) != filepath.Join(a.workspacePath, "thr_target", "inbound") || !strings.HasSuffix(file.Path, "report.pdf") || file.Path == previous {
			t.Fatal("unsafe or colliding imported filename")
		}
		data, _ := readForwardArtifact(a.workspacePath, file.Path, file.SHA256)
		if !bytes.Equal(data, source) || file.Size != len(source) {
			t.Fatal("imported content changed")
		}
		previous = file.Path
	}
}

func TestForwardImportRejectsPermissionLossAndRevokedWorkspace(t *testing.T) {
	for _, revoke := range []bool{false, true} {
		t.Run(map[bool]string{false: "media_forbidden", true: "grant_revoked"}[revoke], func(t *testing.T) {
			var a *App
			a = forwardTestApp(t, func(w http.ResponseWriter, r *http.Request) {
				if !revoke {
					w.WriteHeader(http.StatusForbidden)
					return
				}
				a.companyMu.Lock()
				a.workspaceGrant = nil
				a.runtimeEpoch++
				a.companyMu.Unlock()
				_, _ = io.WriteString(w, "document")
			})
			if _, err := a.performRuntimeAction("nonce", a.companyURL, "import_attachment", forwardParams(t, map[string]string{"asset_id": "asm_test"})); err == nil {
				t.Fatal("unauthorized import accepted")
			}
			if _, err := os.Stat(filepath.Join(a.workspacePath, "thr_target")); !os.IsNotExist(err) {
				t.Fatal("file written after permission loss")
			}
		})
	}
}

func TestForwardTransferRejectsRedirectsAndInvalidAssetIDs(t *testing.T) {
	redirected := false
	outside := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { redirected = true }))
	defer outside.Close()
	a := forwardTestApp(t, func(w http.ResponseWriter, r *http.Request) { http.Redirect(w, r, outside.URL, http.StatusFound) })
	for _, asset := range []string{"asm_test", "../private", "https://outside.example/file"} {
		if _, err := a.performRuntimeAction("nonce", a.companyURL, "import_attachment", forwardParams(t, map[string]string{"asset_id": asset})); err == nil {
			t.Fatal("unsafe transfer accepted")
		}
	}
	if redirected {
		t.Fatal("authenticated media request followed a redirect")
	}
}

func TestForwardBridgeMethodsRequireCurrentNonce(t *testing.T) {
	for _, method := range []string{"upload_artifact", "import_attachment"} {
		if _, _, _, ok := parseRuntimeEvent("desktop:runtime:nonce:1:"+method+":%7B%7D", "nonce"); !ok {
			t.Fatal("transfer method rejected")
		}
		if _, _, _, ok := parseRuntimeEvent("desktop:runtime:old:1:"+method+":%7B%7D", "nonce"); ok {
			t.Fatal("stale transfer nonce allowed")
		}
	}
}

func TestForwardCopiesLocalArtifactsIntoTargetAgentProject(t *testing.T) {
	a := forwardTestApp(t, func(w http.ResponseWriter, r *http.Request) { t.Error("local copy must not upload the original") })
	data := []byte("original report")
	path, digest := previewFixture(t, a.workspacePath, "report.pdf", data)
	result, err := a.performRuntimeAction("nonce", a.companyURL, "import_attachment", forwardParams(t, map[string]string{"path": path, "sha256": digest}))
	if err != nil {
		t.Fatal(err)
	}
	file := result.(runtimeForwardFile)
	if filepath.Dir(file.Path) != filepath.Join(a.workspacePath, "thr_target", "inbound") || file.SHA256 != digest {
		t.Fatal("artifact not copied into the target project")
	}
	copy, err := os.ReadFile(file.Path)
	if err != nil || !bytes.Equal(copy, data) {
		t.Fatal("copied content changed", err)
	}
	original, err := os.ReadFile(path)
	if err != nil || !bytes.Equal(original, data) {
		t.Fatal("original was changed", err)
	}
}

func TestForwardImportsUnnamedImagesWithSupportedExtension(t *testing.T) {
	a := forwardTestApp(t, func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "image/jpeg")
		_, _ = w.Write([]byte("image bytes"))
	})
	result, err := a.performRuntimeAction("nonce", a.companyURL, "import_attachment", forwardParams(t, map[string]string{"asset_id": "asm_test"}))
	if err != nil || !strings.HasSuffix(result.(runtimeForwardFile).Path, "attachment.jpg") {
		t.Fatal("unnamed image lost its supported filename", err)
	}
}

func TestImportUploadedAgentImageIntoLocalProject(t *testing.T) {
	source := []byte("original uploaded PNG bytes")
	a := forwardTestApp(t, func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/agents/expert/workspace/download" || r.URL.Query().Get("path") != "inbound/原图 & morning.png" || r.Header.Get("Authorization") != "Bearer test-token" {
			t.Errorf("unexpected agent download request: %s", r.URL)
		}
		w.Header().Set("Content-Type", "image/png")
		_, _ = w.Write(source)
	})
	result, err := a.performRuntimeAction("nonce", a.companyURL, "import_attachment", forwardParams(t, map[string]string{"agent_id": "expert", "workspace_path": "inbound/原图 & morning.png", "filename": "original.png"}))
	if err != nil {
		t.Fatal(err)
	}
	file := result.(runtimeForwardFile)
	data, err := readForwardArtifact(a.workspacePath, file.Path, file.SHA256)
	if err != nil || !bytes.Equal(data, source) || filepath.Dir(file.Path) != filepath.Join(a.workspacePath, "thr_target", "inbound") {
		t.Fatal("agent image was not imported intact", err)
	}
}

func TestImportUploadedAgentImageRejectsInvalidScopeAndPermissionLoss(t *testing.T) {
	calls := 0
	a := forwardTestApp(t, func(w http.ResponseWriter, r *http.Request) {
		calls++
		w.WriteHeader(http.StatusForbidden)
	})
	for _, values := range []map[string]string{
		{"agent_id": "../other", "workspace_path": "inbound/image.png"},
		{"agent_id": "expert", "workspace_path": ""},
		{"agent_id": "expert", "workspace_path": "image\r\n.png"},
		{"agent_id": "expert", "workspace_path": "inbound/image.png"},
	} {
		if _, err := a.performRuntimeAction("nonce", a.companyURL, "import_attachment", forwardParams(t, values)); err == nil {
			t.Fatal("invalid or forbidden agent image accepted")
		}
	}
	if calls != 1 {
		t.Fatal("invalid inputs reached the company server")
	}
	if _, err := os.Stat(filepath.Join(a.workspacePath, "thr_target")); !os.IsNotExist(err) {
		t.Fatal("file saved after access was denied")
	}
}
