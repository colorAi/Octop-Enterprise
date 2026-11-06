package companytools

import (
	"bufio"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

func TestLeasesShareAndPreventCollection(t *testing.T) {
	path := filepath.Join(t.TempDir(), "runtime.lock")
	a, err := Lock(path, false, 0)
	if err != nil {
		t.Fatal(err)
	}
	defer a.Close()
	b, err := Lock(path, false, 0)
	if err != nil {
		t.Fatal(err)
	}
	defer b.Close()
	if lock, err := Lock(path, true, 0); !errors.Is(err, ErrBusy) {
		if lock != nil {
			lock.Close()
		}
		t.Fatalf("collector acquired a live lease: %v", err)
	}
	a.Close()
	b.Close()
	collector, err := Lock(path, true, 0)
	if err != nil {
		t.Fatal(err)
	}
	collector.Close()
}

func TestLeaseChildProcess(t *testing.T) {
	path := os.Getenv("OCTOP_TEST_TOOL_LEASE")
	if path == "" {
		return
	}
	lease, err := Lock(path, false, 0)
	if err != nil {
		os.Exit(2)
	}
	defer lease.Close()
	os.Stdout.WriteString("locked\n")
	var one [1]byte
	os.Stdin.Read(one[:])
	os.Exit(0) // Abrupt exit releases the OS lock without executing defer.
}

func TestLeaseSurvivesAcrossProcessesAndReleasesOnExit(t *testing.T) {
	path := filepath.Join(t.TempDir(), "runtime.lock")
	cmd := exec.Command(os.Args[0], "-test.run=^TestLeaseChildProcess$")
	cmd.Env = append(os.Environ(), "OCTOP_TEST_TOOL_LEASE="+path)
	in, err := cmd.StdinPipe()
	if err != nil {
		t.Fatal(err)
	}
	out, err := cmd.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	if err = cmd.Start(); err != nil {
		t.Fatal(err)
	}
	defer func() { in.Close(); cmd.Process.Kill(); cmd.Wait() }()
	if line, err := bufio.NewReader(out).ReadString('\n'); err != nil || line != "locked\n" {
		t.Fatal(line, err)
	}
	if lock, err := Lock(path, true, 0); !errors.Is(err, ErrBusy) {
		if lock != nil {
			lock.Close()
		}
		t.Fatalf("collected another process's runtime: %v", err)
	}
	in.Close()
	if err = cmd.Wait(); err != nil {
		t.Fatal(err)
	}
	lock, err := Lock(path, true, 0)
	if err != nil {
		t.Fatal(err)
	}
	lock.Close()
}

func TestCommandsIncludesCurrentExecutable(t *testing.T) {
	commands, err := Commands()
	if err != nil {
		t.Fatal(err)
	}
	for _, command := range commands {
		if strings.Contains(strings.ToLower(command), strings.ToLower(filepath.Base(os.Args[0]))) {
			return
		}
	}
	t.Fatal("process inventory omitted the test executable")
}
