package company

import (
	"golang.org/x/sys/windows"
	"os"
)

func singleLink(file *os.File) bool {
	var info windows.ByHandleFileInformation
	return windows.GetFileInformationByHandle(windows.Handle(file.Fd()), &info) == nil && info.NumberOfLinks == 1 && info.FileAttributes&windows.FILE_ATTRIBUTE_REPARSE_POINT == 0
}

func openRead(root *os.Root, path string) (*os.File, error) { return root.Open(path) }
