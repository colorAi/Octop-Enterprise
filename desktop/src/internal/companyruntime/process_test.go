package companyruntime

import (
	"context"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestStdioProtocolAndExit(t *testing.T) {
	python, err := exec.LookPath("python3")
	if err != nil {
		t.Skip("test fixture requires Python")
	}
	root := t.TempDir()
	launch := filepath.Join(root, "launch.py")
	script := `import json,sys
for line in sys.stdin:
 r=json.loads(line)
 if r['method']=='start':
  print(json.dumps({'event':'steer','thread_id':'thread','frame':{'type':'turn.started','turn_id':'turn'}}),flush=True)
  print(json.dumps({'event':'chunk','thread_id':'thread','data':{'type':'token','content':'local'}}),flush=True)
  print(json.dumps({'event':'done','thread_id':'thread','status':'completed'}),flush=True)
  print(json.dumps({'id':r['id'],'result':{'task_id':'task'}}),flush=True)
 elif r['method']=='fail':
  print(json.dumps({'id':r['id'],'error':'explicit failure'}),flush=True)
 elif r['method']=='never':
  pass
 else:
  print(json.dumps({'id':r['id'],'result':{'credential_seen':r['params']['credential'],'args':sys.argv}}),flush=True)
`
	if err = os.WriteFile(launch, []byte(script), 0600); err != nil {
		t.Fatal(err)
	}
	events := make(chan Message, 10)
	p, err := Start(python, launch, root, os.Environ(), func(m Message) { events <- m })
	if err != nil {
		t.Fatal(err)
	}
	defer p.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	result, err := p.Request(ctx, "initialize", map[string]string{"credential": "private-secret"})
	if err != nil {
		t.Fatal(err)
	}
	var got struct {
		Credential string   `json:"credential_seen"`
		Args       []string `json:"args"`
	}
	_ = json.Unmarshal(result, &got)
	if got.Credential != "private-secret" || strings.Contains(strings.Join(got.Args, " "), "private-secret") {
		t.Fatal("credential transport is not private stdin")
	}
	if _, err = p.Request(ctx, "start", map[string]string{"thread_id": "thread"}); err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{"steer", "chunk", "done"} {
		select {
		case m := <-events:
			if m.Event != want {
				t.Fatalf("event order: %+v", m)
			}
			if want == "steer" && !strings.Contains(string(m.Frame), `"turn_id": "turn"`) {
				t.Fatal("native steering frame was lost")
			}
		case <-ctx.Done():
			t.Fatal("missing event")
		}
	}
	if _, err = p.Request(ctx, "fail", nil); err == nil || err.Error() != "explicit failure" {
		t.Fatal("worker error was hidden")
	}
	timeout, stop := context.WithTimeout(ctx, 20*time.Millisecond)
	defer stop()
	if _, err = p.Request(timeout, "never", nil); err == nil {
		t.Fatal("request did not timeout")
	}
	p.Close()
	select {
	case <-p.done:
	case <-ctx.Done():
		t.Fatal("process survived Close")
	}
	if p.Alive() {
		t.Fatal("closed process marked alive")
	}
}

func TestLargeImageSnapshotKeepsRuntimeResponsive(t *testing.T) {
	python, err := exec.LookPath("python3")
	if err != nil {
		t.Skip("test fixture requires Python")
	}
	root := t.TempDir()
	launch := filepath.Join(root, "launch.py")
	script := `import json,sys
for line in sys.stdin:
 r=json.loads(line)
 if r['method']=='start':
  print(json.dumps({'event':'chunk','thread_id':'thread','data':{'type':'state_snapshot','data':{'image':'data:image/png;base64,'+'A'*(9*1024*1024)}}}),flush=True)
 print(json.dumps({'id':r['id'],'result':{'method':r['method']}}),flush=True)
 if r['method']=='cancel':
  print(json.dumps({'event':'done','thread_id':'thread','status':'cancelled'}),flush=True)
`
	if err := os.WriteFile(launch, []byte(script), 0600); err != nil {
		t.Fatal(err)
	}
	events := make(chan Message, 4)
	p, err := Start(python, launch, root, os.Environ(), func(m Message) { events <- m })
	if err != nil {
		t.Fatal(err)
	}
	defer p.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	for _, method := range []string{"start", "status", "steer", "cancel"} {
		if _, err := p.Request(ctx, method, nil); err != nil {
			t.Fatalf("%s after large image snapshot: %v", method, err)
		}
	}
	select {
	case m := <-events:
		if m.Event != "chunk" || len(m.Data) < 9*1024*1024 {
			t.Fatal("large snapshot was lost or truncated")
		}
	case <-ctx.Done():
		t.Fatal("missing large snapshot")
	}
	select {
	case m := <-events:
		if m.Event != "done" || m.Status != "cancelled" {
			t.Fatalf("missing cancellation confirmation: %+v", m)
		}
	case <-ctx.Done():
		t.Fatal("cancel did not finish")
	}
}

func TestBrokenRuntimeOutputTerminatesWorker(t *testing.T) {
	python, err := exec.LookPath("python3")
	if err != nil {
		t.Skip("test fixture requires Python")
	}
	for _, output := range []string{"!private-prompt-and-token", strings.Repeat("x", 9*1024*1024)} {
		t.Run(fmtID(uint64(len(output))), func(t *testing.T) {
			root := t.TempDir()
			launch := filepath.Join(root, "launch.py")
			encoded, _ := json.Marshal(output)
			script := "import sys,time\nsys.stdout.write(" + string(encoded) + "+'\\n')\nsys.stdout.flush()\ntime.sleep(60)\n"
			if err := os.WriteFile(launch, []byte(script), 0600); err != nil {
				t.Fatal(err)
			}
			events := make(chan Message, 1)
			p, err := Start(python, launch, root, os.Environ(), func(m Message) { events <- m })
			if err != nil {
				t.Fatal(err)
			}
			defer p.Close()
			select {
			case m := <-events:
				if m.Event != "runtime_exit" || m.Status != "unknown" || !strings.Contains(m.Error, "ipc_decode_error") {
					t.Fatalf("missing output failure diagnostic: %+v", m)
				}
				if strings.Contains(m.Error, "private-prompt-and-token") || p.Alive() {
					t.Fatal("protocol failure leaked content or left worker alive")
				}
			case <-time.After(4 * time.Second):
				t.Fatal("unreadable output left worker blocked instead of reporting exit")
			}
		})
	}
}

func TestEnvironmentIsolatesPythonAndOctopHome(t *testing.T) {
	t.Setenv("PYTHONPATH", "foreign")
	t.Setenv("PYTHONHOME", "foreign")
	t.Setenv("PYTHONPYCACHEPREFIX", "foreign")
	t.Setenv("OCTOP_HOME", "foreign")
	t.Setenv("PYTHONUTF8", "0")
	t.Setenv("PYTHONIOENCODING", "gbk")
	got := strings.Join(Environment("private-state", "bundle", "bundle-tools", "private-cache"), "\n")
	if strings.Contains(got, "foreign") || !strings.Contains(got, "OCTOP_HOME=private-state") || !strings.Contains(got, "PATH=bundle-tools") {
		t.Fatal("ambient Python or standalone Octop directory leaked")
	}
	if !strings.Contains(got, "PYTHONUTF8=1") || !strings.Contains(got, "PYTHONIOENCODING=utf-8") || strings.Contains(got, "gbk") || strings.Contains(got, "PYTHONUTF8=0") {
		t.Fatal("ambient Windows encoding leaked into the worker")
	}
	if !strings.Contains(got, "PYTHONPYCACHEPREFIX=private-cache") || strings.Contains(got, "PYTHONDONTWRITEBYTECODE=1") {
		t.Fatal("the private bytecode cache is disabled")
	}
	fallback := strings.Join(Environment("private-state", "bundle", "bundle-tools", ""), "\n")
	if !strings.Contains(fallback, "PYTHONDONTWRITEBYTECODE=1") || strings.Contains(fallback, "PYTHONPYCACHEPREFIX=") {
		t.Fatal("cache failure must not write into the verified bundle")
	}
}

func TestUnicodeIPCAndFilesWithLegacyWindowsEncoding(t *testing.T) {
	python, err := exec.LookPath("python3")
	if err != nil {
		t.Skip("test fixture requires Python")
	}
	t.Setenv("PYTHONUTF8", "0")
	t.Setenv("PYTHONIOENCODING", "gbk")
	root := t.TempDir()
	launch := filepath.Join(root, "launch.py")
	script := `import json,sys
for line in sys.stdin:
 r=json.loads(line)
 text=r['params']['text']
 with open('工作记录.txt','w') as f:f.write(text)
 print(json.dumps({'id':r['id'],'result':{'text':text,'utf8_mode':sys.flags.utf8_mode}},ensure_ascii=False),flush=True)
`
	if err := os.WriteFile(launch, []byte(script), 0600); err != nil {
		t.Fatal(err)
	}
	p, err := Start(python, launch, root, Environment(root, root, os.Getenv("PATH"), ""), nil)
	if err != nil {
		t.Fatal(err)
	}
	defer p.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	text := "中文任务 📝 完成 ✅"
	result, err := p.Request(ctx, "echo", map[string]string{"text": text})
	if err != nil {
		t.Fatal(err)
	}
	var got struct {
		Text string `json:"text"`
		UTF8 int    `json:"utf8_mode"`
	}
	if json.Unmarshal(result, &got) != nil || got.Text != text || got.UTF8 != 1 {
		t.Fatal("Unicode IPC did not survive legacy encoding", string(result))
	}
	file, err := os.ReadFile(filepath.Join(root, "工作记录.txt"))
	if err != nil || string(file) != text {
		t.Fatal("default file encoding lost Unicode", err)
	}
}

func TestWorkerReusesBytecodeOutsideTheVerifiedBundle(t *testing.T) {
	python, err := exec.LookPath("python3")
	if err != nil {
		t.Skip("test fixture requires Python")
	}
	root := t.TempDir()
	bundle, cache := filepath.Join(root, "bundle"), filepath.Join(root, "pyc")
	packages := filepath.Join(bundle, "packages")
	if err := os.MkdirAll(packages, 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(packages, "cached_probe.py"), []byte("VALUE=42\n"), 0600); err != nil {
		t.Fatal(err)
	}
	launch := filepath.Join(root, "launch.py")
	script := `import json,os,sys
sys.path.insert(0,os.environ['OCTOP_COMPANY_PACKAGES'])
import cached_probe
for line in sys.stdin:
 r=json.loads(line)
 print(json.dumps({'id':r['id'],'result':{'value':cached_probe.VALUE,'cache':sys.pycache_prefix}}),flush=True)
`
	if err := os.WriteFile(launch, []byte(script), 0600); err != nil {
		t.Fatal(err)
	}
	call := func() {
		p, err := Start(python, launch, root, Environment(filepath.Join(root, "state"), bundle, os.Getenv("PATH"), cache), nil)
		if err != nil {
			t.Fatal(err)
		}
		defer p.Close()
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		result, err := p.Request(ctx, "status", nil)
		var got struct {
			Value int    `json:"value"`
			Cache string `json:"cache"`
		}
		if err != nil || json.Unmarshal(result, &got) != nil || got.Value != 42 || got.Cache != cache {
			t.Fatalf("cache worker: %s %v", result, err)
		}
	}
	call()
	var compiled string
	if err := filepath.WalkDir(cache, func(path string, entry os.DirEntry, err error) error {
		if err == nil && strings.HasPrefix(entry.Name(), "cached_probe.") && strings.HasSuffix(entry.Name(), ".pyc") {
			compiled = path
		}
		return err
	}); err != nil {
		t.Fatal(err)
	}
	if compiled == "" {
		t.Fatal("worker did not create external bytecode")
	}
	stamp := time.Unix(1234, 0)
	if err := os.Chtimes(compiled, stamp, stamp); err != nil {
		t.Fatal(err)
	}
	call()
	info, err := os.Stat(compiled)
	if err != nil || !info.ModTime().Equal(stamp) {
		t.Fatal("unchanged module was recompiled", err)
	}
	if _, err := os.Stat(filepath.Join(packages, "__pycache__")); !os.IsNotExist(err) {
		t.Fatal("verified bundle was modified")
	}
}

func TestRuntimeDiagnosticsDoesNotExposeSecrets(t *testing.T) {
	d := &runtimeDiagnostics{}
	text := strings.Repeat("secret path/token\n", 1000) + "ModuleNotFoundError: secret-token-and-path\n"
	if n, err := d.Write([]byte(text)); err != nil || n != len(text) {
		t.Fatal(n, err)
	}
	if len(d.tail) > 4096 || d.category() != "ModuleNotFoundError" {
		t.Fatal("invalid safe diagnostic")
	}
	d.Write([]byte(strings.Repeat("x", 4096)))
	if d.category() != "runtime_exit" {
		t.Fatal("stale diagnostic")
	}
}
