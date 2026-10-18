// Keyset pagination over a (timestamp, id) pair, shared by every admin list so
// that no surface has to grow a fixed LIMIT to be usable.
//
// The cursor carries the last row's exact timestamp plus its id, and the next
// page asks the database for the row-pair strictly less (or greater) than it.
// That is what makes paging correct while rows are being inserted: an offset
// would either repeat a row or skip one as the ordering shifts underneath it.
package httpx

import (
	"net/http"
	"strconv"
	"time"
)

// KeysetRow is one row plus the two values its cursor is made of. The timestamp
// is carried apart from the payload because a display stamp is second-precise
// while the keyset has to be exact — rows sharing a second would otherwise be
// skipped between pages.
type KeysetRow struct {
	Item  map[string]any
	Stamp time.Time
	ID    string
}

// Keyset pages a query newest-first. Limit is the caller's page size and Cur
// signs the cursor so a client cannot forge pagination state.
type Keyset struct {
	Limit int
	Cur   *Cursor
}

// NewKeyset builds a page from the request's limit/cursor parameters.
func (k *Keyset) New(r *http.Request, def, max int, cur *Cursor) {
	k.Limit = PageParams(r, def, max)
	k.Cur = cur
}

// Decode reads a cursor from the request. `used` says one was present at all;
// `ok` says the request may proceed (an unusable cursor is an error, not a
// return to the first page).
func (k Keyset) Decode(r *http.Request) (t time.Time, id string, used, ok bool) {
	token := r.URL.Query().Get("cursor")
	if token == "" {
		return time.Time{}, "", false, true
	}
	payload, ok := k.Cur.Decode(token)
	if !ok {
		return time.Time{}, "", false, false
	}
	ts, _ := payload["t"].(string)
	id, _ = payload["i"].(string)
	t, err := time.Parse(time.RFC3339Nano, ts)
	if err != nil || id == "" {
		return time.Time{}, "", false, false
	}
	return t, id, true, true
}

// AddCursor appends the keyset predicate to a query, returning the argument
// count it left off at. `alias` prefixes the two columns (empty for none).
func (k Keyset) AddCursor(r *http.Request, args []any, alias string) (t time.Time, id string, used, ok bool, extra string) {
	t, id, used, ok = k.Decode(r)
	if !ok || !used {
		return
	}
	prefix := ""
	if alias != "" {
		prefix = alias + "."
	}
	extra = " AND (" + prefix + "created_at, " + prefix + "id) < ($" +
		strconv.Itoa(len(args)+1) + "::timestamptz, $" + strconv.Itoa(len(args)+2) + "::text)"
	return t, id, used, ok, extra
}

// Write trims the row fetched only to detect more, and emits the envelope with a
// cursor for the next page.
func (k Keyset) Write(w http.ResponseWriter, r *http.Request, rows []KeysetRow) {
	more := len(rows) > k.Limit
	if more {
		rows = rows[:k.Limit]
	}
	items := make([]map[string]any, 0, len(rows))
	for _, row := range rows {
		items = append(items, row.Item)
	}
	out := map[string]any{"items": items, "has_more": more}
	if more && len(rows) > 0 {
		last := rows[len(rows)-1]
		out["next_cursor"] = k.Cur.Encode(map[string]any{"t": last.Stamp.UTC().Format(time.RFC3339Nano), "i": last.ID})
	}
	WriteData(w, r, 200, out)
}
