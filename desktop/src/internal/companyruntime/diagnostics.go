package companyruntime

import (
	"regexp"
	"sync"
)

// Keep only a bounded tail in memory and expose allowlisted exception classes.
// Tracebacks, file paths, prompts and credentials never enter UI events or logs.
type runtimeDiagnostics struct {
	mu   sync.Mutex
	tail []byte
}

var exceptionClass = regexp.MustCompile(`(?m)^(ModuleNotFoundError|ImportError|PermissionError|FileNotFoundError|MemoryError|SyntaxError|OSError):`)

func (d *runtimeDiagnostics) Write(p []byte) (int, error) {
	n := len(p)
	d.mu.Lock()
	defer d.mu.Unlock()
	const limit = 4096
	if len(p) > limit {
		p = p[len(p)-limit:]
	}
	d.tail = append(d.tail, p...)
	if len(d.tail) > limit {
		d.tail = d.tail[len(d.tail)-limit:]
	}
	return n, nil
}

func (d *runtimeDiagnostics) category() string {
	d.mu.Lock()
	defer d.mu.Unlock()
	matches := exceptionClass.FindAllSubmatch(d.tail, -1)
	if len(matches) == 0 {
		return "runtime_exit"
	}
	return string(matches[len(matches)-1][1])
}
