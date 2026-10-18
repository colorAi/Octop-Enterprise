package httpx

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
)

// Cursor is an opaque token bound to a filter context. The server signs it so
// clients cannot forge pagination state; the payload is a small JSON map.
type Cursor struct {
	key  string
	keyB []byte
}

// NewCursor binds a context string (filter description + owner) with the server key.
func NewCursor(serverKey string) *Cursor {
	return &Cursor{key: serverKey, keyB: []byte(serverKey)}
}

// Encode seals a payload map into a url-safe opaque token.
func (c *Cursor) Encode(payload map[string]any) string {
	b, _ := json.Marshal(payload)
	mac := hmac.New(sha256.New, c.keyB)
	mac.Write(b)
	token := base64.RawURLEncoding.EncodeToString(b) + "." + base64.RawURLEncoding.EncodeToString(mac.Sum(nil)[:16])
	return token
}

// Decode opens a token; on any mismatch it reports false.
func (c *Cursor) Decode(token string) (map[string]any, bool) {
	head, sig, ok := cut(token)
	if !ok {
		return nil, false
	}
	raw, err := base64.RawURLEncoding.DecodeString(head)
	if err != nil {
		return nil, false
	}
	want, err := base64.RawURLEncoding.DecodeString(sig)
	if err != nil {
		return nil, false
	}
	mac := hmac.New(sha256.New, c.keyB)
	mac.Write(raw)
	if !hmac.Equal(mac.Sum(nil)[:16], want) {
		return nil, false
	}
	var m map[string]any
	if json.Unmarshal(raw, &m) != nil {
		return nil, false
	}
	return m, true
}

func cut(s string) (string, string, bool) {
	for i := 0; i < len(s); i++ {
		if s[i] == '.' {
			return s[:i], s[i+1:], true
		}
	}
	return "", "", false
}
