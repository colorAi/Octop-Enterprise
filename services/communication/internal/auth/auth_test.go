package auth

import (
	"strings"
	"testing"
	"time"
)

func TestCredentialPurposeAudienceExpiryAndTampering(t *testing.T) {
	key := strings.Repeat("k", 32)
	now := time.Now().Unix()
	good := Claims{Issuer: "octop", Audience: "octop-im", Purpose: "request", UserID: "octop:i:1", SessionID: "s", Issued: now, Expires: now + 30, Version: 1, Permissions: []string{"messaging"}}
	token := Sign(good, key)
	if _, ok := Verify(token, key, "request"); !ok {
		t.Fatal("valid credential rejected")
	}
	if _, ok := Verify(token, key, "identity"); ok {
		t.Fatal("wrong purpose")
	}
	if _, ok := Verify(token, strings.Repeat("x", 32), "request"); ok {
		t.Fatal("wrong signature")
	}
	for _, change := range []func(*Claims){func(c *Claims) { c.Audience = "another-service" }, func(c *Claims) { c.Issuer = "browser" }, func(c *Claims) { c.Expires = now - 1 }, func(c *Claims) { c.Issued = now + 20 }, func(c *Claims) { c.Expires = now + 300 }, func(c *Claims) { c.UserID = "" }, func(c *Claims) { c.SessionID = "" }} {
		c := good
		change(&c)
		if _, ok := Verify(Sign(c, key), key, "request"); ok {
			t.Fatal("invalid credential accepted", c)
		}
	}
}
