package main

import (
	"encoding/json"
	"testing"
)

func TestConfigRefreshDoesNotRequireProjectOrStartRuntime(t *testing.T) {
	if _, method, _, ok := parseRuntimeEvent("desktop:runtime:nonce:1:refresh_status:%7B%7D", "nonce"); !ok || method != "refresh_status" {
		t.Fatal("scoped refresh request was rejected")
	}
	a := &App{companyURL: "https://company.test", workspaceNonce: "nonce"}
	result, err := a.performRuntimeAction("nonce", a.companyURL, "refresh_status", nil)
	if err != nil {
		t.Fatal(err)
	}
	data, _ := json.Marshal(result)
	if string(data) != `{"active_tasks":[]}` || a.companyRuntime != nil {
		t.Fatalf("refresh initialized tools or reported invalid status: %s", data)
	}
	for _, scope := range [][2]string{{"other", a.companyURL}, {"nonce", "https://other.test"}} {
		if _, err := a.performRuntimeAction(scope[0], scope[1], "refresh_status", nil); err == nil {
			t.Fatal("refresh status escaped its account scope")
		}
	}
}
