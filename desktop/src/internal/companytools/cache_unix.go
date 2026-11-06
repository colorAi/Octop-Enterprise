//go:build !windows

package companytools

import (
	"context"
	"errors"
	"os"
	"os/exec"
	"strings"
	"syscall"
	"time"
)

func tryLock(file *os.File, exclusive bool) error {
	mode := syscall.LOCK_SH | syscall.LOCK_NB
	if exclusive {
		mode = syscall.LOCK_EX | syscall.LOCK_NB
	}
	err := syscall.Flock(int(file.Fd()), mode)
	if errors.Is(err, syscall.EWOULDBLOCK) {
		return ErrBusy
	}
	return err
}

// Commands also covers workers from clients predating cache leases. The caller
// only matches runtime paths; command lines must never be logged or persisted.
func Commands() ([]string, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	data, err := exec.CommandContext(ctx, "/bin/ps", "-axww", "-o", "command=").Output()
	if err != nil {
		return nil, err
	}
	return strings.Split(string(data), "\n"), nil
}
