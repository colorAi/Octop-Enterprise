package companytools

import (
	"errors"
	"os"
	"strings"
	"unsafe"

	"golang.org/x/sys/windows"
)

func tryLock(file *os.File, exclusive bool) error {
	flags := uint32(windows.LOCKFILE_FAIL_IMMEDIATELY)
	if exclusive {
		flags |= windows.LOCKFILE_EXCLUSIVE_LOCK
	}
	err := windows.LockFileEx(windows.Handle(file.Fd()), flags, 0, 1, 0, &windows.Overlapped{})
	if errors.Is(err, windows.ERROR_LOCK_VIOLATION) {
		return ErrBusy
	}
	return err
}

func Commands() ([]string, error) {
	snapshot, err := windows.CreateToolhelp32Snapshot(windows.TH32CS_SNAPPROCESS, 0)
	if err != nil {
		return nil, err
	}
	defer windows.CloseHandle(snapshot)
	entry := windows.ProcessEntry32{}
	entry.Size = uint32(unsafe.Sizeof(entry))
	err = windows.Process32First(snapshot, &entry)
	var paths []string
	for err == nil {
		name := strings.ToLower(windows.UTF16ToString(entry.ExeFile[:]))
		process, openErr := windows.OpenProcess(windows.PROCESS_QUERY_LIMITED_INFORMATION, false, entry.ProcessID)
		if openErr == nil {
			buffer := make([]uint16, 32768)
			size := uint32(len(buffer))
			openErr = windows.QueryFullProcessImageName(process, 0, &buffer[0], &size)
			windows.CloseHandle(process)
			if openErr == nil {
				paths = append(paths, windows.UTF16ToString(buffer[:size]))
			}
		}
		// Inaccessible system services do not use this cache. An inaccessible
		// Python/media/browser worker could: defer collection rather than guess.
		if openErr != nil && (strings.HasPrefix(name, "python") || name == "ffmpeg.exe" || name == "ffprobe.exe" || name == "node.exe") && !errors.Is(openErr, windows.ERROR_INVALID_PARAMETER) {
			return nil, openErr
		}
		err = windows.Process32Next(snapshot, &entry)
	}
	if !errors.Is(err, windows.ERROR_NO_MORE_FILES) {
		return nil, err
	}
	return paths, nil
}
