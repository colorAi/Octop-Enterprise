package company

import (
	"archive/zip"
	"bytes"
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"sync"
	"testing"
	"time"
)

func fileFixture(t *testing.T, writable, share bool) (*Files, Grant, string) {
	t.Helper()
	root := t.TempDir()
	files := NewFiles(t.TempDir())
	grant, err := files.Add(root, writable, share)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(files.Reset)
	return files, grant, root
}

func TestGrantConsentCompatibility(t *testing.T) {
	for _, field := range []string{"allow_company", "allow_h3c"} {
		for _, consent := range []bool{false, true} {
			payload, _ := json.Marshal(map[string]any{"id": "grant", field: consent})
			var grant Grant
			if err := json.Unmarshal(payload, &grant); err != nil || grant.AllowCompany != consent {
				t.Fatalf("%s=%v: %+v %v", field, consent, grant, err)
			}
			wire := wireGrants([]Grant{grant})
			if wire[0]["allow_h3c"] != consent {
				t.Fatalf("legacy server consent changed: %+v", wire)
			}
			if _, ok := wire[0]["allow_company"]; ok {
				t.Fatal("protocol-v2 servers reject unknown fields")
			}
		}
	}
	var grant Grant
	if err := json.Unmarshal([]byte(`{"allow_company":false,"allow_h3c":true}`), &grant); err == nil {
		t.Fatal("conflicting consent accepted")
	}
	if err := json.Unmarshal([]byte(`{}`), &grant); err != nil || grant.AllowCompany {
		t.Fatal("missing consent must deny access")
	}
	encoded, _ := json.Marshal(Grant{AllowCompany: true})
	var native map[string]any
	_ = json.Unmarshal(encoded, &native)
	if native["allow_company"] != true {
		t.Fatal("native UI did not receive canonical consent")
	}
}
func fileCall(g Grant, tool, path, content string) frame {
	args := map[string]json.RawMessage{}
	b, _ := json.Marshal(path)
	args["path"] = b
	if tool == "local_write_file" {
		b, _ = json.Marshal(content)
		args["content"] = b
	}
	return frame{CallID: randomID(), TaskID: strings.Repeat("a", 64), DeviceID: strings.Repeat("b", 32), OwnerUserID: 7, AgentID: "private-expert", ThreadID: "thread", GrantID: g.ID, GrantVersion: g.Version, Tool: tool, ExpiresAt: time.Now().Add(time.Minute).Unix(), Arguments: args}
}
func TestFilesReadWriteReplayAndConflict(t *testing.T) {
	x, g, dir := fileFixture(t, true, true)
	call := fileCall(g, "local_write_file", "report.md", "你好\nreport")
	first := x.Execute(context.Background(), call)
	if first.Status != "completed" || first.SHA256 == "" {
		t.Fatalf("%+v", first)
	}
	replay := x.Execute(context.Background(), call)
	if !reflect.DeepEqual(replay, first) {
		t.Fatalf("replay differs: %+v", replay)
	}
	changed := call
	changed.Arguments = map[string]json.RawMessage{"path": json.RawMessage(`"another.md"`), "content": json.RawMessage(`"changed"`)}
	if r := x.Execute(context.Background(), changed); r.Error != "operation_id_conflict" {
		t.Fatalf("%+v", r)
	}
	conflict := fileCall(g, "local_write_file", "report.md", "overwrite")
	if r := x.Execute(context.Background(), conflict); r.Error != "output_exists_or_not_writable" {
		t.Fatalf("%+v", r)
	}
	data, _ := os.ReadFile(filepath.Join(dir, "report.md"))
	if string(data) != "你好\nreport" {
		t.Fatal("source overwritten")
	}
	read := x.Execute(context.Background(), fileCall(g, "local_read_file", "report.md", ""))
	if read.Text != string(data) {
		t.Fatalf("%+v", read)
	}
	status := fileCall(g, "local_operation_status", "", "")
	status.Arguments = map[string]json.RawMessage{"operation_id": json.RawMessage(`"` + call.CallID + `"`)}
	if r := x.Execute(context.Background(), status); r.Status != "completed" || r.OperationID != call.CallID {
		t.Fatalf("%+v", r)
	}
	status.ThreadID = "another-thread"
	if r := x.Execute(context.Background(), status); r.Error != "operation_not_owned" {
		t.Fatalf("%+v", r)
	}
}
func TestFilesPermissionAndPathBoundaries(t *testing.T) {
	x, g, dir := fileFixture(t, true, true)
	_ = os.WriteFile(filepath.Join(dir, "input.txt"), []byte("input"), 0600)
	for _, path := range []string{"../secret.txt", "/etc/passwd", "C:/private.txt", "a\\b.txt", "//server/share", "file.txt:stream", "folder/../input.txt", ".secret", "a//b", "NUL.txt", "COM1", "end. ", "a\x00b"} {
		r := x.Execute(context.Background(), fileCall(g, "local_read_file", path, ""))
		if r.Status != "failed" {
			t.Fatalf("allowed %q: %+v", path, r)
		}
	}
	bad := fileCall(g, "local_read_file", "input.txt", "")
	bad.GrantVersion++
	if r := x.Execute(context.Background(), bad); r.Status != "failed" {
		t.Fatal(r)
	}
	x.Revoke(g.ID)
	if r := x.Execute(context.Background(), fileCall(g, "local_read_file", "input.txt", "")); r.Status != "failed" {
		t.Fatal(r)
	}
	for _, settings := range [][2]bool{{false, true}, {true, false}} {
		x, g, _ := fileFixture(t, settings[0], settings[1])
		if r := x.Execute(context.Background(), fileCall(g, "local_write_file", "out.txt", "x")); r.Status != "failed" {
			t.Fatal(r)
		}
	}
}
func TestFilesLinksAndRootReplacement(t *testing.T) {
	x, g, dir := fileFixture(t, true, true)
	outside := t.TempDir()
	_ = os.WriteFile(filepath.Join(outside, "secret.txt"), []byte("outside secret"), 0600)
	if err := os.Symlink(outside, filepath.Join(dir, "escape")); err == nil {
		if r := x.Execute(context.Background(), fileCall(g, "local_read_file", "escape/secret.txt", "")); r.Status != "failed" {
			t.Fatal(r)
		}
		if r := x.Execute(context.Background(), fileCall(g, "local_write_file", "escape/out.txt", "x")); r.Status != "failed" {
			t.Fatal(r)
		}
	}
	if err := os.Link(filepath.Join(outside, "secret.txt"), filepath.Join(dir, "hard.txt")); err == nil {
		if r := x.Execute(context.Background(), fileCall(g, "local_read_file", "hard.txt", "")); r.Status != "failed" {
			t.Fatal(r)
		}
	}
	moved := dir + "-moved"
	if err := os.Rename(dir, moved); err == nil {
		defer os.RemoveAll(moved)
		_ = os.Mkdir(dir, 0700)
		_ = os.WriteFile(filepath.Join(dir, "replacement.txt"), []byte("replacement"), 0600)
		if r := x.Execute(context.Background(), fileCall(g, "local_read_file", "replacement.txt", "")); r.Status != "failed" {
			t.Fatal("followed replaced root", r)
		}
	}
}
func TestFilesConcurrentConflictCancellationAndUncertainJournal(t *testing.T) {
	x, g, dir := fileFixture(t, true, true)
	var wg sync.WaitGroup
	results := make(chan FileResult, 2)
	for range 2 {
		wg.Add(1)
		go func() {
			defer wg.Done()
			results <- x.Execute(context.Background(), fileCall(g, "local_write_file", "same.txt", "report"))
		}()
	}
	wg.Wait()
	close(results)
	count := 0
	for r := range results {
		if r.Status == "completed" {
			count++
		}
	}
	if count != 1 {
		t.Fatal(count)
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if r := x.Execute(ctx, fileCall(g, "local_write_file", "cancel.txt", "x")); r.Status != "failed" {
		t.Fatal(r)
	}
	if _, err := os.Stat(filepath.Join(dir, "cancel.txt")); !os.IsNotExist(err) {
		t.Fatal("cancel created output")
	}
	call := fileCall(g, "local_write_file", "uncertain.txt", "x")
	receipt, _ := x.journalPath(call, call.CallID)
	op := operation{Fingerprint: fingerprint(call), DeviceID: call.DeviceID, Owner: call.OwnerUserID, AgentID: call.AgentID, ThreadID: call.ThreadID, GrantID: g.ID, GrantVersion: g.Version, Result: FileResult{Kind: "local_file", Status: "uncertain", Path: "uncertain.txt", OperationID: call.CallID}}
	if err := saveReceipt(receipt, op, true); err != nil {
		t.Fatal(err)
	}
	if r := x.Execute(context.Background(), call); r.Status != "uncertain" {
		t.Fatal(r)
	}
	if _, err := os.Stat(filepath.Join(dir, "uncertain.txt")); !os.IsNotExist(err) {
		t.Fatal("uncertain call replayed")
	}
}
func TestDocumentsBoundedRoundTrips(t *testing.T) {
	for _, ext := range []string{"txt", "md", "docx", "xlsx"} {
		input := "标题,amount\n用户,100"
		data, err := writeDocument("report."+ext, input)
		if err != nil {
			t.Fatal(ext, err)
		}
		text, err := readDocument("report."+ext, data)
		if err != nil || !strings.Contains(text, "用户") {
			t.Fatal(ext, text, err)
		}
	}
	if _, err := writeDocument("payload.exe", "x"); err == nil {
		t.Fatal("executable allowed")
	}
	if _, err := readDocument("input.txt", []byte{0xff}); err == nil {
		t.Fatal("binary accepted")
	}
	csv, err := writeDocument("report.csv", "=HYPERLINK(\"\"),normal")
	if err == nil && !bytes.Contains(csv, []byte("'=")) {
		t.Fatal("formula not escaped")
	}
	csv, err = writeDocument("report.csv", "=1+1,normal\n@cmd,safe")
	if err != nil || !bytes.Contains(csv, []byte("'=1+1")) || !bytes.Contains(csv, []byte("'@cmd")) {
		t.Fatal(string(csv), err)
	}
	var b bytes.Buffer
	z := zip.NewWriter(&b)
	f, _ := z.Create("word/document.xml")
	_, _ = f.Write(bytes.Repeat([]byte("a"), 17*1024*1024))
	_ = z.Close()
	if _, err := readDocument("bomb.docx", b.Bytes()); err == nil {
		t.Fatal("unbounded expansion")
	}
	x, g, dir := fileFixture(t, true, true)
	_ = os.WriteFile(filepath.Join(dir, "large.txt"), []byte(strings.Repeat("中", maxContent)), 0600)
	r := x.Execute(context.Background(), fileCall(g, "local_read_file", "large.txt", ""))
	if !r.Truncated || len(r.Text) > maxContent {
		t.Fatalf("bad truncation: %d %+v", len(r.Text), r.Status)
	}
}

func TestFilesDirectoryLinkSwapCannotReadOutsideGrant(t *testing.T) {
	x, g, root := fileFixture(t, true, true)
	outside := t.TempDir()
	_ = os.WriteFile(filepath.Join(outside, "data.txt"), []byte("outside secret"), 0600)
	active, parked := filepath.Join(root, "folder"), filepath.Join(root, "parked")
	_ = os.Mkdir(active, 0700)
	_ = os.WriteFile(filepath.Join(active, "data.txt"), []byte("inside"), 0600)
	probe := filepath.Join(root, "link-probe")
	if err := os.Symlink(outside, probe); err != nil {
		t.Skip("symlink creation requires platform permission")
	}
	_ = os.Remove(probe)
	done := make(chan struct{})
	stopped := make(chan struct{})
	go func() {
		defer close(stopped)
		for {
			select {
			case <-done:
				return
			default:
			}
			if os.Rename(active, parked) != nil {
				continue
			}
			if os.Symlink(outside, active) == nil {
				_ = os.Remove(active)
			}
			_ = os.Rename(parked, active)
		}
	}()
	for range 300 {
		result := x.Execute(context.Background(), fileCall(g, "local_read_file", "folder/data.txt", ""))
		if result.Status == "completed" && result.Text != "inside" {
			close(done)
			<-stopped
			t.Fatalf("escaped root: %+v", result)
		}
	}
	close(done)
	<-stopped
}
