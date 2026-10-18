//go:build !windows

package companyruntime

import (
	"context"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"
)

func TestCloseTerminatesToolInSeparateSession(t *testing.T) {
	python, err := exec.LookPath("python3")
	if err != nil {
		t.Skip("Python subprocess fixture")
	}
	root := t.TempDir()
	launch := filepath.Join(root, "launch.py")
	script := `import subprocess,sys,json
p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],start_new_session=True)
for line in sys.stdin:
 r=json.loads(line)
 print(json.dumps({'id':r['id'],'result':{'pid':p.pid}}),flush=True)
`
	_ = os.WriteFile(launch, []byte(script), 0600)
	p, err := Start(python, launch, root, os.Environ(), nil)
	if err != nil {
		t.Fatal(err)
	}
	defer p.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	reply, err := p.Request(ctx, "pid", nil)
	if err != nil {
		t.Fatal(err)
	}
	var result struct {
		PID int `json:"pid"`
	}
	_ = json.Unmarshal(reply, &result)
	p.Close()
	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		state, err := exec.Command("/bin/ps", "-o", "stat=", "-p", strconv.Itoa(result.PID)).Output()
		if err != nil || strings.TrimSpace(string(state)) == "" || strings.HasPrefix(strings.TrimSpace(string(state)), "Z") {
			return
		}
		time.Sleep(20 * time.Millisecond)
	}
	_ = exec.Command("kill", "-9", strconv.Itoa(result.PID)).Run()
	t.Fatal("separate-session tool survived account shutdown")
}
