//go:build windows

package companyruntime

import (
	"golang.org/x/sys/windows"
	"os/exec"
	"strconv"
	"syscall"
)

func configure(cmd *exec.Cmd) {
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: windows.CREATE_NO_WINDOW}
}
func kill(cmd *exec.Cmd) {
	if cmd.Process != nil {
		c := exec.Command("taskkill", "/F", "/T", "/PID", strconv.Itoa(cmd.Process.Pid))
		configure(c)
		_ = c.Run()
	}
}
