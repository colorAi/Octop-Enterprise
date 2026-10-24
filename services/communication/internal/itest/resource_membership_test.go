package itest

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"testing"
)

func (h *H) resourceDirectory(purpose string, ids []string) (int, map[string]any) {
	h.t.Helper()
	body, _ := json.Marshal(map[string]any{"user_ids": ids})
	req, _ := http.NewRequest("POST", h.Server.URL+"/internal/resource-memberships", bytes.NewReader(body))
	req.Header.Set("Authorization", "Bearer "+h.token(admin, purpose, 1))
	req.Header.Set("Content-Type", "application/json")
	response, err := http.DefaultClient.Do(req)
	if err != nil {
		h.t.Fatal(err)
	}
	defer response.Body.Close()
	var value map[string]any
	if err := json.NewDecoder(response.Body).Decode(&value); err != nil {
		h.t.Fatal(err)
	}
	return response.StatusCode, value
}

func TestResourceDirectoryBatchIsReadOnlyScopedAndCurrent(t *testing.T) {
	h := newH(t)
	missing := "octop:installation:999"
	code, value := h.resourceDirectory("resource-directory", []string{alice, bob, missing})
	if code != 200 {
		t.Fatalf("directory %d %v", code, value)
	}
	items := value["data"].(map[string]any)["items"].([]any)
	first := items[0].(map[string]any)
	if len(items) != 3 || first["user_id"] != alice || first["department_id"] != h.Dept || first["found"] != true || first["enabled"] != true {
		t.Fatalf("unexpected projection: %v", items)
	}
	if items[2].(map[string]any)["found"] != false || items[2].(map[string]any)["enabled"] != false {
		t.Fatal("missing identity must not appear active")
	}
	_, authoritative := h.resourcePolicy(alice, "GET", "", nil)
	if authoritative["data"].(map[string]any)["revision"] != first["revision"] {
		t.Fatal("batch read mutated the membership")
	}
	h.assign(alice, h.Other)
	h.identity(bob, "disabled", 2)
	code, value = h.resourceDirectory("resource-directory", []string{bob, alice})
	if code != 200 {
		t.Fatal(code, value)
	}
	items = value["data"].(map[string]any)["items"].([]any)
	if items[0].(map[string]any)["enabled"] != false || items[1].(map[string]any)["department_id"] != h.Other || items[1].(map[string]any)["revision"].(float64) <= first["revision"].(float64) {
		t.Fatal("batch served stale memberships", items)
	}
	h.ok(admin, "PATCH", "/organization/departments/"+h.Other, map[string]any{"enabled": false})
	_, value = h.resourceDirectory("resource-directory", []string{alice})
	if value["data"].(map[string]any)["items"].([]any)[0].(map[string]any)["enabled"] != false {
		t.Fatal("disabled department appeared active")
	}
}

func TestResourceDirectoryRejectsPublicCredentialsAndUnboundedQueries(t *testing.T) {
	h := newH(t)
	for _, purpose := range []string{"request", "resource-policy", "identity"} {
		if code, _ := h.resourceDirectory(purpose, []string{alice}); code != 401 {
			t.Fatalf("purpose %s: %d", purpose, code)
		}
	}
	tooMany := make([]string, 201)
	for i := range tooMany {
		tooMany[i] = fmt.Sprintf("octop:installation:%d", i+1)
	}
	for _, ids := range [][]string{nil, {alice, alice}, {"octop:installation:0"}, {"octop:installation:02"}, tooMany} {
		if code, _ := h.resourceDirectory("resource-directory", ids); code != 400 {
			t.Fatalf("invalid batch of %d: %d", len(ids), code)
		}
	}
	if code, _ := h.resourceDirectory("resource-directory", []string{"octop:another:2"}); code != 403 {
		t.Fatalf("cross-installation batch: %d", code)
	}
}

func (h *H) resourcePolicy(u, method, path string, body any) (int, map[string]any) {
	h.t.Helper()
	b, _ := json.Marshal(body)
	req, _ := http.NewRequest(method, h.Server.URL+"/internal/resource-membership/"+u+path, bytes.NewReader(b))
	req.Header.Set("Authorization", "Bearer "+h.token(u, "resource-policy", 1))
	req.Header.Set("Content-Type", "application/json")
	response, err := http.DefaultClient.Do(req)
	if err != nil {
		h.t.Fatal(err)
	}
	defer response.Body.Close()
	var out map[string]any
	if err = json.NewDecoder(response.Body).Decode(&out); err != nil {
		h.t.Fatal(err)
	}
	return response.StatusCode, out
}

func TestResourceMembershipMoveReceiptAndRevision(t *testing.T) {
	h := newH(t)
	if err := h.D.Store.CheckShape(context.Background()); err != nil {
		t.Fatal("current communication server cannot start", err)
	}
	code, value := h.resourcePolicy(alice, "GET", "", nil)
	if code != 200 {
		t.Fatal(code, value)
	}
	before := value["data"].(map[string]any)["revision"].(float64)
	move := map[string]any{"department_id": h.Other, "expected_revision": int64(before), "operation_id": "move-one"}
	h.ok(admin, "PUT", "/organization/users/"+alice+"/department", move)
	h.ok(admin, "PUT", "/organization/users/"+alice+"/department", move)
	_, value = h.resourcePolicy(alice, "GET", "", nil)
	after := value["data"].(map[string]any)
	if after["revision"].(float64) != before+1 || after["department_id"] != h.Other {
		t.Fatal(after)
	}
	code, _ = h.request(admin, "PUT", "/organization/users/"+alice+"/department", map[string]any{"department_id": h.Dept, "expected_revision": int64(before)})
	if code != 409 {
		t.Fatal("stale revision accepted", code)
	}
	code, value = h.resourcePolicy(alice, "POST", "/resolve", map[string]any{"operation_id": "move-one", "expected_revision": int64(before)})
	if code != 200 || value["data"].(map[string]any)["status"] != "complete" {
		t.Fatal(code, value)
	}
	h.ok(admin, "PATCH", "/organization/departments/"+h.Other, map[string]any{"enabled": false})
	_, value = h.resourcePolicy(alice, "GET", "", nil)
	disabled := value["data"].(map[string]any)
	if disabled["enabled"] != false || disabled["revision"].(float64) != before+2 {
		t.Fatal(disabled)
	}
}

func TestResourceMembershipMigrationPreservesDepartment(t *testing.T) {
	h := newH(t)
	ctx := context.Background()
	st := h.D.Store
	_, err := st.Pool.Exec(ctx, `DROP TABLE resource_department_operations;
        ALTER TABLE users DROP COLUMN resource_revision;
        DELETE FROM schema_migrations WHERE version=16`)
	if err != nil {
		t.Fatal(err)
	}
	if st.CheckShape(ctx) == nil {
		t.Fatal("old resource schema accepted")
	}
	versions, err := st.Migrate(ctx)
	if err != nil || len(versions) != 1 || versions[0] != 16 {
		t.Fatal("resource migration", versions, err)
	}
	if err := st.CheckShape(ctx); err != nil {
		t.Fatal("migrated communication server cannot start", err)
	}
	var department string
	var revision int64
	err = st.Pool.QueryRow(ctx, `SELECT dm.department_id,u.resource_revision FROM users u JOIN department_members dm ON dm.user_id=u.id WHERE u.id=$1`, alice).Scan(&department, &revision)
	if err != nil || department != h.Dept || revision != 0 {
		t.Fatal("existing department changed", department, revision, err)
	}
	versions, err = st.Migrate(ctx)
	if err != nil || len(versions) != 0 {
		t.Fatal("resource migration replay", versions, err)
	}
}

func TestResourceMoveCancellationRejectsDelayedRequest(t *testing.T) {
	h := newH(t)
	_, value := h.resourcePolicy(alice, "GET", "", nil)
	revision := value["data"].(map[string]any)["revision"].(float64)
	code, value := h.resourcePolicy(alice, "POST", "/resolve", map[string]any{"operation_id": "lost-request", "expected_revision": int64(revision)})
	if code != 200 || value["data"].(map[string]any)["status"] != "cancelled" {
		t.Fatal(code, value)
	}
	code, _ = h.request(admin, "PUT", "/organization/users/"+alice+"/department", map[string]any{"department_id": h.Other, "expected_revision": int64(revision), "operation_id": "lost-request"})
	if code != 409 {
		t.Fatal("cancelled delayed request committed", code)
	}
	_, value = h.resourcePolicy(alice, "GET", "", nil)
	state := value["data"].(map[string]any)
	if state["department_id"] != h.Dept || state["revision"].(float64) != revision {
		t.Fatal(state)
	}
	// A normal user request token cannot call control-plane policy endpoints.
	req, _ := http.NewRequest("GET", h.Server.URL+"/internal/resource-membership/"+alice, nil)
	req.Header.Set("Authorization", "Bearer "+h.token(alice, "request", h.Versions[alice]))
	response, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	if response.StatusCode != 401 {
		t.Fatal("public token accepted", response.StatusCode)
	}
}
