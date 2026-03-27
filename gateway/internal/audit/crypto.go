package audit

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
)

func sha256Hex(b []byte) string {
	sum := sha256.Sum256(b)
	return hex.EncodeToString(sum[:])
}

func hmacSHA256Hex(key []byte, b []byte) string {
	m := hmac.New(sha256.New, key)
	_, _ = m.Write(b)
	return hex.EncodeToString(m.Sum(nil))
}

