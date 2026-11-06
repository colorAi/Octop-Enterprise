// Package companytools coordinates the shared desktop tool cache across clients.
package companytools

import (
	"errors"
	"os"
	"path/filepath"
	"time"
)

var ErrBusy = errors.New("company tool cache is in use")

// Lock holds an OS file lock until Close or process exit. Lock files stay in
// place: unlinking them would let another process lock a different inode.
func Lock(path string, exclusive bool, timeout time.Duration) (*os.File, error) {
	if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
		return nil, err
	}
	file, err := os.OpenFile(path, os.O_CREATE|os.O_RDWR, 0600)
	if err != nil {
		return nil, err
	}
	deadline := time.Now().Add(timeout)
	for {
		err = tryLock(file, exclusive)
		if err == nil {
			return file, nil
		}
		if !errors.Is(err, ErrBusy) || !time.Now().Before(deadline) {
			file.Close()
			return nil, err
		}
		time.Sleep(25 * time.Millisecond)
	}
}
