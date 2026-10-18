package company

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"sort"
)

// Results exposes only receipts belonging to this connection's identity and active grants.
func (c *Client) Results() []FileResult {
	status := c.Status()
	x := c.files
	x.mu.Lock()
	defer x.mu.Unlock()
	out := []FileResult{}
	if !identifier.MatchString(status.DeviceID) || x.journal == "" {
		return out
	}
	entries, err := os.ReadDir(filepath.Join(x.journal, status.DeviceID))
	if err != nil {
		return out
	}
	sort.Slice(entries, func(i, j int) bool {
		a, _ := entries[i].Info()
		b, _ := entries[j].Info()
		if a == nil || b == nil {
			return false
		}
		return a.ModTime().After(b.ModTime())
	})
	for _, entry := range entries {
		if len(out) >= 20 {
			break
		}
		if filepath.Ext(entry.Name()) != ".json" {
			continue
		}
		b, err := os.ReadFile(filepath.Join(x.journal, status.DeviceID, entry.Name()))
		if err != nil {
			continue
		}
		var o operation
		if json.Unmarshal(b, &o) != nil || o.Owner != status.OwnerUserID || x.dirs[o.GrantID] == nil {
			continue
		}
		out = append(out, o.Result)
	}
	return out
}

// A native user click may reveal a known output; callers cannot supply a path.
func (c *Client) ResultPath(operationID string) (string, error) {
	status := c.Status()
	x := c.files
	x.mu.Lock()
	defer x.mu.Unlock()
	if !identifier.MatchString(operationID) || !identifier.MatchString(status.DeviceID) {
		return "", errors.New("invalid operation")
	}
	b, err := os.ReadFile(filepath.Join(x.journal, status.DeviceID, operationID+".json"))
	if err != nil {
		return "", errors.New("receipt unavailable")
	}
	var o operation
	if json.Unmarshal(b, &o) != nil || o.Owner != status.OwnerUserID || o.DeviceID != status.DeviceID || o.Result.Kind != "local_file" {
		return "", errors.New("result not owned")
	}
	d := x.dirs[o.GrantID]
	if d == nil || d.Version != o.GrantVersion {
		return "", errors.New("directory permission revoked")
	}
	path, err := relativePath(o.Result.Path, false)
	if err != nil {
		return "", err
	}
	if err = noLinks(d.root, path, false); err != nil {
		return "", err
	}
	// If the granted directory has moved, fail rather than opening a replacement
	// at the old pathname. File operations themselves continue using the root handle.
	held, err := d.root.Stat(".")
	if err != nil {
		return "", err
	}
	named, err := os.Stat(d.root.Name())
	if err != nil || !os.SameFile(held, named) {
		return "", errors.New("directory moved; select it again")
	}
	return filepath.Join(d.root.Name(), filepath.FromSlash(path)), nil
}
