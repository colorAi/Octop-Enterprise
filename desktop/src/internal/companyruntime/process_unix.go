//go:build !windows

package companyruntime

import (
	"os/exec"
	"strconv"
	"strings"
	"syscall"
	"time"
)

func configure(cmd *exec.Cmd) { cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true} }

// Tool subprocesses deliberately own separate sessions so individual task cancel
// works. Snapshot their ancestry before terminating the worker; killing only the
// worker process group would leave FFmpeg and Python children running on logout.
func descendants(root int) []int {
	output, err := exec.Command("/bin/ps", "-eo", "pid=,ppid=").Output()
	if err != nil {
		return nil
	}
	children := map[int][]int{}
	for _, line := range strings.Split(string(output), "\n") {
		fields := strings.Fields(line)
		if len(fields) != 2 {
			continue
		}
		pid, e1 := strconv.Atoi(fields[0])
		parent, e2 := strconv.Atoi(fields[1])
		if e1 == nil && e2 == nil {
			children[parent] = append(children[parent], pid)
		}
	}
	var result []int
	var visit func(int)
	visit = func(parent int) {
		for _, pid := range children[parent] {
			visit(pid)
			result = append(result, pid)
		}
	}
	visit(root)
	return result
}
func kill(cmd *exec.Cmd) {
	if cmd.Process == nil {
		return
	}
	children := descendants(cmd.Process.Pid)
	// Invalidate every separate child group immediately, then let the worker flush
	// its cancellation receipts. stdin is already closed, so its reader also exits.
	for _, pid := range children {
		if group, err := syscall.Getpgid(pid); err == nil && group == pid {
			_ = syscall.Kill(-group, syscall.SIGKILL)
		}
		_ = syscall.Kill(pid, syscall.SIGKILL)
	}
	_ = syscall.Kill(-cmd.Process.Pid, syscall.SIGTERM)
	time.AfterFunc(time.Second, func() { _ = syscall.Kill(-cmd.Process.Pid, syscall.SIGKILL) })
}
