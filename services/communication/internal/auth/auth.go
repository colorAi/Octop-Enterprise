// Internal short-lived credentials are issued exclusively by the Octop bridge.
package auth

import (
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"net/http"
	"octop/communication/internal/deps"
	"octop/communication/internal/httpx"
	"strings"
	"time"
)

type contextKey struct{}
type Actor struct {
	UserID, SessionID string
	Permissions       []string
	Version           int64
}
type Claims struct {
	Issuer      string   `json:"iss"`
	Audience    string   `json:"aud"`
	Purpose     string   `json:"purpose"`
	UserID      string   `json:"sub"`
	SessionID   string   `json:"sid"`
	Issued      int64    `json:"iat"`
	Expires     int64    `json:"exp"`
	Version     int64    `json:"version"`
	Permissions []string `json:"permissions"`
}

func From(r *http.Request) (Actor, bool) {
	a, ok := r.Context().Value(contextKey{}).(Actor)
	return a, ok
}
func Sign(c Claims, key string) string {
	b, _ := json.Marshal(c)
	p := base64.RawURLEncoding.EncodeToString(b)
	h := hmac.New(sha256.New, []byte(key))
	h.Write([]byte(p))
	return p + "." + base64.RawURLEncoding.EncodeToString(h.Sum(nil))
}
func Verify(token, key, purpose string) (Claims, bool) {
	var c Claims
	p, s, ok := strings.Cut(token, ".")
	if !ok || len(token) > 8192 {
		return c, false
	}
	sig, e := base64.RawURLEncoding.DecodeString(s)
	if e != nil {
		return c, false
	}
	h := hmac.New(sha256.New, []byte(key))
	h.Write([]byte(p))
	if !hmac.Equal(sig, h.Sum(nil)) {
		return c, false
	}
	b, e := base64.RawURLEncoding.DecodeString(p)
	if e != nil || json.Unmarshal(b, &c) != nil {
		return c, false
	}
	now := time.Now().Unix()
	return c, c.Issuer == "octop" && c.Audience == "octop-im" && c.Purpose == purpose && c.UserID != "" && c.SessionID != "" && c.Expires > now && c.Issued <= now+5 && c.Expires-c.Issued <= 60 && c.Expires > c.Issued
}
func Has(perms []string, key string) bool {
	for _, p := range perms {
		if p == key {
			return true
		}
	}
	return false
}
func Credential(d *deps.Deps, purpose string, next func(http.ResponseWriter, *http.Request, Claims)) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		c, ok := Verify(strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer "), d.Cfg.IdentityKey, purpose)
		if !ok {
			httpx.WriteError(w, r, httpx.ErrSessionInvalid)
			return
		}
		next(w, r, c)
	}
}
func Require(d *deps.Deps, permission string, next http.HandlerFunc) http.HandlerFunc {
	return RequireAny(d, []string{permission}, next)
}

// RequireAny permits a shared notification connection for contacts or messaging.
func RequireAny(d *deps.Deps, permissions []string, next http.HandlerFunc) http.HandlerFunc {
	return Credential(d, "request", func(w http.ResponseWriter, r *http.Request, c Claims) {
		var status string
		var version int64
		var perms []string
		if e := d.Store.Pool.QueryRow(r.Context(), `SELECT status,identity_version,permissions FROM users WHERE id=$1`, c.UserID).Scan(&status, &version, &perms); e != nil {
			httpx.WriteError(w, r, httpx.ErrSessionInvalid)
			return
		}
		if status != "active" {
			httpx.WriteError(w, r, httpx.ErrAccountDisabled)
			return
		}
		allowed := false
		for _, permission := range permissions {
			allowed = allowed || (Has(c.Permissions, permission) && Has(perms, permission))
		}
		if version != c.Version || !allowed {
			httpx.WriteError(w, r, httpx.ErrForbidden)
			return
		}
		a := Actor{c.UserID, c.SessionID, perms, version}
		next(w, r.WithContext(context.WithValue(r.Context(), contextKey{}, a)))
	})
}
func Middleware(d *deps.Deps, next http.HandlerFunc) http.HandlerFunc {
	return Require(d, "messaging", next)
}
