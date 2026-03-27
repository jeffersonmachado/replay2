package audit

import (
	"strconv"
	"strings"
)

// CanonicalString builds the deterministic payload used by hash-chain and HMAC.
// Fields absent in an event are encoded as empty string.
func CanonicalString(ev *Event) string {
	// IMPORTANT: Keep key order stable.
	var b strings.Builder
	writeKV := func(k, v string) {
		b.WriteString(k)
		b.WriteByte('=')
		b.WriteString(v)
		b.WriteByte('\n')
	}

	writeKV("v", ev.V)
	writeKV("seq_global", strconv.FormatInt(ev.SeqGlobal, 10))
	writeKV("ts_ms", strconv.FormatInt(ev.TsMs, 10))
	writeKV("type", ev.Type)
	writeKV("actor", ev.Actor)
	writeKV("session_id", ev.SessionID)
	writeKV("seq_session", strconv.FormatInt(ev.SeqSession, 10))
	writeKV("dir", ev.Dir)
	writeKV("n", strconv.Itoa(ev.N))
	writeKV("data_b64", ev.DataB64)
	writeKV("sig", ev.Sig)
	writeKV("norm_sha256", ev.NormSHA256)
	writeKV("norm_len", strconv.Itoa(ev.NormLen))
	writeKV("prev_hash", ev.PrevHash)

	return b.String()
}

