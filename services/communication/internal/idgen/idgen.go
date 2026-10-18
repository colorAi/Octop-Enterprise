// Package idgen creates random opaque identifiers (never enumerable).
package idgen

import (
	"crypto/rand"
	"math/big"
	"strings"
)

const alpha = "abcdefghijklmnopqrstuvwxyz0123456789" // 36 chars, lowercase unambiguous

// New returns a prefixed random id (~113 bits entropy); ids stay <= 64 chars.
func New(prefix string) string { return prefix + Random(22) }

// Random returns n cryptographically random chars from the id alphabet.
func Random(n int) string {
	const base = int64(len(alpha))
	out := make([]byte, n)
	raw := make([]byte, (n*13)/8+8) // generous: 13 bits per byte target
	if _, err := rand.Read(raw); err != nil {
		panic("idgen: rand unavailable: " + err.Error())
	}
	x := new(big.Int).SetBytes(raw)
	m := big.NewInt(base)
	for i := n - 1; i >= 0; i-- {
		v := new(big.Int)
		x.DivMod(x, m, v)
		out[i] = alpha[v.Int64()]
	}
	return string(out)
}

// Digits returns n random decimal digits (public miaox codes).
func Digits(n int) string {
	const d = "0123456789"
	out := make([]byte, n)
	raw := make([]byte, n+8)
	if _, err := rand.Read(raw); err != nil {
		panic("idgen: rand unavailable: " + err.Error())
	}
	x := new(big.Int).SetBytes(raw)
	m := big.NewInt(10)
	for i := n - 1; i >= 0; i-- {
		v := new(big.Int)
		x.DivMod(x, m, v)
		out[i] = d[v.Int64()]
	}
	return string(out)
}

// Valid reports whether s is a plausible opaque id (1..64 chars of the alphabet).
func Valid(s string) bool {
	if len(s) == 0 || len(s) > 64 {
		return false
	}
	return strings.IndexFunc(s, func(r rune) bool {
		return !strings.ContainsRune(alpha+"_", r)
	}) < 0
}
