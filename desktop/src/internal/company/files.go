package company

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"sync"
	"time"
	"unicode/utf8"
)

const maxContent = 128 * 1024
const maxInput = 8 * 1024 * 1024

var identifier = regexp.MustCompile(`^[a-f0-9]{32,64}$`)

type Grant struct {
	ID           string `json:"id"`
	Name         string `json:"name"`
	Version      int    `json:"version"`
	Writable     bool   `json:"writable"`
	AllowCompany bool   `json:"allow_company"`
}

func (g *Grant) UnmarshalJSON(data []byte) error {
	type plain Grant
	var value struct {
		plain
		Company *bool `json:"allow_company"`
		Legacy  *bool `json:"allow_h3c"`
	}
	if err := json.Unmarshal(data, &value); err != nil {
		return err
	}
	if value.Company != nil && value.Legacy != nil && *value.Company != *value.Legacy {
		return errors.New("conflicting directory consent")
	}
	*g = Grant(value.plain)
	if value.Company != nil {
		g.AllowCompany = *value.Company
	} else if value.Legacy != nil {
		g.AllowCompany = *value.Legacy
	}
	return nil
}

// Existing protocol-v2 servers reject unknown fields. Keep their wire spelling
// while the native UI and new server use allow_company internally.
func wireGrants(grants []Grant) []map[string]any {
	out := make([]map[string]any, 0, len(grants))
	for _, g := range grants {
		out = append(out, map[string]any{
			"id": g.ID, "name": g.Name, "version": g.Version,
			"writable": g.Writable, "allow_h3c": g.AllowCompany,
		})
	}
	return out
}

type directory struct {
	Grant
	root *os.Root
}
type FileResult struct {
	Kind        string  `json:"kind"`
	Status      string  `json:"status"`
	Path        string  `json:"path,omitempty"`
	Text        string  `json:"text,omitempty"`
	Entries     []Entry `json:"entries,omitempty"`
	Truncated   bool    `json:"truncated,omitempty"`
	SHA256      string  `json:"sha256,omitempty"`
	Bytes       int     `json:"bytes,omitempty"`
	Error       string  `json:"error,omitempty"`
	OperationID string  `json:"operation_id,omitempty"`
}
type Entry struct {
	Name      string `json:"name"`
	Directory bool   `json:"directory"`
	Bytes     int64  `json:"bytes"`
}
type fileArguments struct {
	Path        string `json:"path"`
	Content     string `json:"content"`
	OperationID string `json:"operation_id"`
}
type operation struct {
	Fingerprint  string     `json:"fingerprint"`
	DeviceID     string     `json:"device_id"`
	Owner        int64      `json:"owner"`
	AgentID      string     `json:"agent_id"`
	ThreadID     string     `json:"thread_id"`
	GrantID      string     `json:"grant_id"`
	GrantVersion int        `json:"grant_version"`
	Result       FileResult `json:"result"`
}

// Roots and grants are session-only. Only bounded write receipts persist, never
// credentials, document contents, or absolute workspace paths.
type Files struct {
	mu      sync.Mutex
	dirs    map[string]*directory
	journal string
}

func NewFiles(journal string) *Files {
	return &Files{dirs: make(map[string]*directory), journal: journal}
}
func randomID() string {
	b := make([]byte, 16)
	if _, err := rand.Read(b); err != nil {
		panic(err)
	}
	return hex.EncodeToString(b)
}
func (x *Files) Grants() []Grant {
	x.mu.Lock()
	defer x.mu.Unlock()
	out := make([]Grant, 0, len(x.dirs))
	for _, d := range x.dirs {
		out = append(out, d.Grant)
	}
	return out
}
func (x *Files) Add(path string, writable, allowCompany bool) (Grant, error) {
	x.mu.Lock()
	defer x.mu.Unlock()
	if len(x.dirs) >= 16 {
		return Grant{}, errors.New("最多授权 16 个目录 / Too many directories")
	}
	// Resolve the native selection once; retain an open directory handle thereafter.
	path, err := filepath.EvalSymlinks(path)
	if err != nil {
		return Grant{}, err
	}
	root, err := os.OpenRoot(path)
	if err != nil {
		return Grant{}, err
	}
	name := filepath.Base(path)
	if len([]rune(name)) > 120 {
		name = string([]rune(name)[:120])
	}
	g := Grant{randomID(), name, 1, writable, allowCompany}
	x.dirs[g.ID] = &directory{g, root}
	return g, nil
}
func (x *Files) Revoke(id string) {
	x.mu.Lock()
	defer x.mu.Unlock()
	if d := x.dirs[id]; d != nil {
		d.root.Close()
		delete(x.dirs, id)
	}
}
func (x *Files) Reset() {
	x.mu.Lock()
	defer x.mu.Unlock()
	for id, d := range x.dirs {
		d.root.Close()
		delete(x.dirs, id)
	}
}
func failure(reason string) FileResult {
	return FileResult{Kind: "local_error", Status: "failed", Error: reason}
}

// Wire paths have one portable spelling. This also rejects Windows ADS, device
// names, UNC paths and hidden metadata on macOS. os.Root enforces containment
// on the actual open, including when a component changes after this check.
func relativePath(raw string, list bool) (string, error) {
	if list && (raw == "" || raw == ".") {
		return ".", nil
	}
	if raw == "" || len(raw) > 512 || strings.ContainsAny(raw, "\\:\x00") || strings.HasPrefix(raw, "/") {
		return "", errors.New("invalid_relative_path")
	}
	for _, p := range strings.Split(raw, "/") {
		if p == "" || p == "." || p == ".." || strings.HasPrefix(p, ".") || strings.HasSuffix(p, ".") || strings.HasSuffix(p, " ") {
			return "", errors.New("invalid_relative_path")
		}
		for _, r := range p {
			if r < 32 || r == 127 {
				return "", errors.New("invalid_relative_path")
			}
		}
		base := strings.ToUpper(strings.SplitN(p, ".", 2)[0])
		if base == "CON" || base == "PRN" || base == "AUX" || base == "NUL" || regexp.MustCompile(`^(COM|LPT)[1-9]$`).MatchString(base) {
			return "", errors.New("reserved_path")
		}
	}
	return raw, nil
}
func noLinks(root *os.Root, path string, newFile bool) error {
	parts := strings.Split(path, "/")
	for i := range parts {
		info, err := root.Lstat(strings.Join(parts[:i+1], "/"))
		if newFile && i == len(parts)-1 && os.IsNotExist(err) {
			return nil
		}
		if err != nil {
			return errors.New("path_unavailable")
		}
		if info.Mode()&os.ModeSymlink != 0 || info.Mode()&os.ModeIrregular != 0 {
			return errors.New("links_not_allowed")
		}
	}
	return nil
}
func fingerprint(f frame) string {
	b, _ := json.Marshal(f)
	h := sha256.Sum256(b)
	return hex.EncodeToString(h[:])
}
func (x *Files) Execute(ctx context.Context, f frame) FileResult {
	x.mu.Lock()
	defer x.mu.Unlock() // serial execution; revoke returns only after a bounded operation finishes
	if ctx.Err() != nil || f.ExpiresAt <= time.Now().Unix() {
		return failure("cancelled_before_execution")
	}
	d := x.dirs[f.GrantID]
	if d == nil || d.Version != f.GrantVersion || !d.AllowCompany {
		return failure("directory_permission_or_h3c_consent_required")
	}
	raw, _ := json.Marshal(f.Arguments)
	var args fileArguments
	decoder := json.NewDecoder(strings.NewReader(string(raw)))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&args); err != nil {
		return failure("invalid_arguments")
	}
	if f.Tool == "local_operation_status" {
		return x.lookup(f, args.OperationID)
	}
	path, err := relativePath(args.Path, f.Tool == "local_list_directory")
	if err != nil {
		return failure(err.Error())
	}
	if err = noLinks(d.root, path, f.Tool == "local_write_file"); err != nil {
		return failure(err.Error())
	}
	switch f.Tool {
	case "local_list_directory":
		dir, err := d.root.Open(path)
		if err != nil {
			return failure("directory_unavailable")
		}
		defer dir.Close()
		infos, err := dir.ReadDir(201)
		if err != nil && err != io.EOF {
			return failure("directory_unavailable")
		}
		result := FileResult{Kind: "local_directory", Status: "completed", Path: path, Entries: []Entry{}, Truncated: len(infos) > 200}
		for _, item := range infos[:min(len(infos), 200)] {
			if strings.HasPrefix(item.Name(), ".") || item.Type()&os.ModeSymlink != 0 {
				continue
			}
			info, err := item.Info()
			if err != nil || (!info.IsDir() && !info.Mode().IsRegular()) {
				continue
			}
			result.Entries = append(result.Entries, Entry{item.Name(), item.IsDir(), info.Size()})
		}
		return result
	case "local_read_file":
		file, err := openRead(d.root, path)
		if err != nil {
			return failure("file_unavailable")
		}
		defer file.Close()
		info, err := file.Stat()
		if err != nil || !info.Mode().IsRegular() || info.Size() > maxInput || !singleLink(file) {
			return failure("unsupported_file_or_size")
		}
		data, err := io.ReadAll(io.LimitReader(file, maxInput+1))
		if err != nil || len(data) > maxInput {
			return failure("file_too_large_or_unreadable")
		}
		content, err := readDocument(path, data)
		if err != nil {
			return failure(err.Error())
		}
		truncated := len(content) > maxContent
		if truncated {
			content = content[:maxContent]
			for !utf8.ValidString(content) {
				content = content[:len(content)-1]
			}
		}
		return FileResult{Kind: "local_text", Status: "completed", Path: path, Text: content, Truncated: truncated, Bytes: len(data)}
	case "local_write_file":
		if !d.Writable {
			return failure("directory_read_only")
		}
		if len(args.Content) > maxContent {
			return failure("content_too_large")
		}
		data, err := writeDocument(path, args.Content)
		if err != nil {
			return failure(err.Error())
		}
		return x.write(ctx, d, f, path, data)
	default:
		return failure("tool_unavailable")
	}
}
func (x *Files) journalPath(f frame, id string) (string, error) {
	if x.journal == "" || !identifier.MatchString(id) || !identifier.MatchString(f.DeviceID) {
		return "", errors.New("journal_unavailable")
	}
	dir := filepath.Join(x.journal, f.DeviceID)
	if err := os.MkdirAll(dir, 0700); err != nil {
		return "", err
	}
	return filepath.Join(dir, id+".json"), nil
}
func sameScope(o operation, f frame) bool {
	return o.DeviceID == f.DeviceID && o.Owner == f.OwnerUserID && o.AgentID == f.AgentID && o.ThreadID == f.ThreadID && o.GrantID == f.GrantID && o.GrantVersion == f.GrantVersion
}
func (x *Files) lookup(f frame, id string) FileResult {
	path, err := x.journalPath(f, id)
	if err != nil {
		return failure("journal_unavailable")
	}
	b, err := os.ReadFile(path)
	if os.IsNotExist(err) {
		return FileResult{Kind: "local_operation", Status: "not_found", OperationID: id}
	}
	if err != nil {
		return failure("journal_unavailable")
	}
	var o operation
	if json.Unmarshal(b, &o) != nil || !sameScope(o, f) {
		return failure("operation_not_owned")
	}
	return o.Result
}
func saveReceipt(path string, o operation, exclusive bool) error {
	data, err := json.Marshal(o)
	if err != nil {
		return err
	}
	target := path
	flags := os.O_WRONLY | os.O_CREATE | os.O_EXCL
	if !exclusive {
		target = path + "." + randomID() + ".tmp"
	}
	file, err := os.OpenFile(target, flags, 0600)
	if err != nil {
		return err
	}
	_, err = file.Write(data)
	if err == nil {
		err = file.Sync()
	}
	closeErr := file.Close()
	if err == nil {
		err = closeErr
	}
	if !exclusive {
		if err == nil {
			err = os.Rename(target, path)
		}
		if err != nil {
			_ = os.Remove(target)
		}
	}
	return err
}
func (x *Files) write(ctx context.Context, d *directory, f frame, path string, data []byte) FileResult {
	receipt, err := x.journalPath(f, f.CallID)
	if err != nil {
		return failure("journal_unavailable")
	}
	sum := sha256.Sum256(data)
	o := operation{fingerprint(f), f.DeviceID, f.OwnerUserID, f.AgentID, f.ThreadID, f.GrantID, f.GrantVersion, FileResult{Kind: "local_file", Status: "uncertain", Path: path, OperationID: f.CallID, SHA256: hex.EncodeToString(sum[:]), Bytes: len(data)}}
	if err = saveReceipt(receipt, o, true); err != nil {
		b, readErr := os.ReadFile(receipt)
		var previous operation
		if readErr != nil || json.Unmarshal(b, &previous) != nil || previous.Fingerprint != o.Fingerprint || !sameScope(previous, f) {
			return failure("operation_id_conflict")
		}
		return previous.Result
	}
	if ctx.Err() != nil {
		o.Result = failure("cancelled_before_execution")
		o.Result.OperationID = f.CallID
		_ = saveReceipt(receipt, o, false)
		return o.Result
	}
	// Exclusive creation is the conflict boundary: no source or existing output is overwritten.
	file, err := d.root.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if err != nil {
		o.Result = failure("output_exists_or_not_writable")
		o.Result.OperationID = f.CallID
		_ = saveReceipt(receipt, o, false)
		return o.Result
	}
	_, err = file.Write(data)
	if err == nil {
		err = file.Sync()
	}
	closeErr := file.Close()
	if err == nil {
		err = closeErr
	}
	if err == nil {
		o.Result.Status = "completed"
	} else {
		o.Result.Error = "write_incomplete_check_output"
	}
	if saveReceipt(receipt, o, false) != nil {
		o.Result.Status = "uncertain"
		o.Result.Error = "receipt_not_saved_check_output"
	}
	return o.Result
}
