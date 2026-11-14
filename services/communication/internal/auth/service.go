package auth

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"io"
	"net/http"

	"octop/communication/internal/deps"
	"octop/communication/internal/httpx"
)

// ServiceRequest binds the service purpose to the exact method, URI and bytes.
// Ordinary user and identity-delivery credentials cannot enter this surface.
func ServiceRequest(d *deps.Deps, next func(http.ResponseWriter, *http.Request, Claims)) http.HandlerFunc {
	return ServiceRequestLimit(d, 2<<20, next)
}

// ServiceRequestLimit is only used by the bounded generated-attachment endpoint.
func ServiceRequestLimit(d *deps.Deps, limit int64, next func(http.ResponseWriter, *http.Request, Claims)) http.HandlerFunc {
	return Credential(d, "group-experts-v1", func(w http.ResponseWriter, r *http.Request, c Claims) {
		body, err := io.ReadAll(http.MaxBytesReader(w, r.Body, limit))
		sum := sha256.Sum256(body)
		if err != nil || c.Expires-c.Issued > 15 || c.Method != r.Method || c.Path != r.URL.RequestURI() || c.BodyDigest != hex.EncodeToString(sum[:]) {
			httpx.WriteError(w, r, httpx.ErrSessionInvalid)
			return
		}
		r.Body = io.NopCloser(bytes.NewReader(body))
		next(w, r, c)
	})
}
