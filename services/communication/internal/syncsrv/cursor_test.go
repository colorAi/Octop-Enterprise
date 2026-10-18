package syncsrv

import (
	"net/http/httptest"
	"net/url"
	"testing"

	"octop/communication/internal/deps"
	"octop/communication/internal/httpx"
)

func TestCursorKeepsInt64Precision(t *testing.T) {
	s := New(&deps.Deps{Cursor: httpx.NewCursor("synthetic-cursor-key")})
	const seq int64 = 9007199254740993
	req := httptest.NewRequest("GET", "/sync?cursor="+url.QueryEscape(s.cursorFor("user", seq)), nil)
	got, err := s.parseCursor(req, "user")
	if err != nil || got != seq {
		t.Fatalf("cursor lost precision: got %d want %d err=%v", got, seq, err)
	}
}
