// Package companyruntime owns an outbound-only, stdio JSONL worker. Secrets are
// sent only over its private stdin and never placed in command arguments or logs.
package companyruntime

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
)

type Message struct {
	ID         string          `json:"id,omitempty"`
	Event      string          `json:"event,omitempty"`
	ThreadID   string          `json:"thread_id,omitempty"`
	TaskID     string          `json:"task_id,omitempty"`
	Result     json.RawMessage `json:"result,omitempty"`
	Error      string          `json:"error,omitempty"`
	Data       json.RawMessage `json:"data,omitempty"`
	Frame      json.RawMessage `json:"frame,omitempty"`
	Status     string          `json:"status,omitempty"`
	SyncState  string          `json:"sync_state,omitempty"`
	PID        int             `json:"pid,omitempty"`
	Stream     string          `json:"stream,omitempty"`
	Content    string          `json:"content,omitempty"`
	ToolCallID string          `json:"tool_call_id,omitempty"`
	Artifacts  json.RawMessage `json:"artifacts,omitempty"`
}
type Process struct {
	cmd       *exec.Cmd
	stdin     io.WriteCloser
	writeMu   sync.Mutex
	mu        sync.Mutex
	pending   map[string]chan Message
	done      chan struct{}
	seq       atomic.Uint64
	closeOnce sync.Once
}

func Start(python, launch, dir string, env []string, event func(Message)) (*Process, error) {
	// Embedded Windows Python can ignore environment flags; -X also fixes
	// default file encoding before any package is imported.
	cmd := exec.Command(python, "-X", "utf8", "-u", launch, "company-runtime")
	cmd.Dir, cmd.Env = dir, env
	configure(cmd)
	stdin, err := cmd.StdinPipe()
	if err != nil {
		return nil, err
	}
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		stdin.Close()
		return nil, err
	}
	// Avoid leaking prompts, paths or credentials from third-party libraries.
	diagnostics := &runtimeDiagnostics{}
	cmd.Stderr = diagnostics
	if err = cmd.Start(); err != nil {
		stdin.Close()
		return nil, err
	}
	p := &Process{cmd: cmd, stdin: stdin, pending: map[string]chan Message{}, done: make(chan struct{})}
	go func() {
		// Image tool results accumulate in state snapshots and can exceed a
		// Scanner token limit. Keep consuming the trusted worker's JSON stream.
		decoder := json.NewDecoder(stdout)
		outputFailure := ""
		for {
			var m Message
			if err := decoder.Decode(&m); err != nil {
				if !errors.Is(err, io.EOF) {
					// Decoder errors can include private payload bytes; expose only
					// the category. Stop the writer before waiting for its exit.
					outputFailure = "ipc_decode_error"
				}
				p.Close()
				break
			}
			if m.ID != "" {
				p.mu.Lock()
				ch := p.pending[m.ID]
				p.mu.Unlock()
				if ch != nil {
					select {
					case ch <- m:
					default:
					}
				}
			} else if m.Event != "" && event != nil {
				event(m)
			}
		}
		_ = cmd.Wait()
		category := diagnostics.category()
		if outputFailure != "" {
			category = outputFailure
		}
		diagnostic := fmt.Sprintf("%s (exit=%d)", category, cmd.ProcessState.ExitCode())
		close(p.done)
		if event != nil {
			event(Message{Event: "runtime_exit", Status: "unknown", Error: "本地运行时已退出；请核对任务结果后重新连接，不会自动重做已执行操作。诊断：" + diagnostic})
		}
	}()
	return p, nil
}
func (p *Process) Request(ctx context.Context, method string, params any) (json.RawMessage, error) {
	id := fmtID(p.seq.Add(1))
	ch := make(chan Message, 1)
	p.mu.Lock()
	p.pending[id] = ch
	p.mu.Unlock()
	defer func() { p.mu.Lock(); delete(p.pending, id); p.mu.Unlock() }()
	p.writeMu.Lock()
	err := json.NewEncoder(p.stdin).Encode(map[string]any{"id": id, "method": method, "params": params})
	p.writeMu.Unlock()
	if err != nil {
		return nil, errors.New("本地运行时连接已中断，请核对任务状态后重试")
	}
	select {
	case m := <-ch:
		if m.Error != "" {
			return nil, errors.New(m.Error)
		}
		return m.Result, nil
	case <-p.done:
		return nil, errors.New("本地运行时已退出，请重新连接")
	case <-ctx.Done():
		return nil, errors.New("本地运行时响应超时；请核对任务状态，避免重复执行")
	}
}
func fmtID(v uint64) string {
	const digits = "0123456789"
	var b [20]byte
	i := len(b)
	for {
		i--
		b[i] = digits[v%10]
		v /= 10
		if v == 0 {
			return string(b[i:])
		}
	}
}
func (p *Process) Alive() bool {
	select {
	case <-p.done:
		return false
	default:
		return true
	}
}
func (p *Process) Close() { p.closeOnce.Do(func() { _ = p.stdin.Close(); kill(p.cmd) }) }

// Environment discards ambient Python configuration and uses only packaged tools.
func Environment(stateDir, root, path, cacheDir string) []string {
	env := []string{}
	for _, e := range os.Environ() {
		key := e
		for i, c := range e {
			if c == '=' {
				key = e[:i]
				break
			}
		}
		switch strings.ToUpper(key) {
		case "PYTHONPATH", "PYTHONHOME", "OCTOP_HOME", "PATH", "PYTHONNOUSERSITE", "PYTHONDONTWRITEBYTECODE", "PYTHONPYCACHEPREFIX", "PYTHONUTF8", "PYTHONIOENCODING", "OCTOP_COMPANY_PACKAGES", "OCTOP_COMPANY_TOOL_BIN":
			continue
		}
		env = append(env, e)
	}
	if cacheDir == "" {
		env = append(env, "PYTHONDONTWRITEBYTECODE=1")
	} else {
		env = append(env, "PYTHONPYCACHEPREFIX="+cacheDir)
	}
	return append(env, "OCTOP_HOME="+stateDir, "PYTHONNOUSERSITE=1", "PYTHONUTF8=1", "PYTHONIOENCODING=utf-8", "PYTHONPATH=", "PATH="+path, "OCTOP_COMPANY_PACKAGES="+filepath.Join(root, "packages"), "OCTOP_COMPANY_TOOL_BIN="+filepath.Join(root, "tools"))
}
