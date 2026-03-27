package audit

// Event is the JSONL record written to the append-only audit log.
//
// NOTE: Keep fields stable; verifier/replayer rely on them.
type Event struct {
	V        string `json:"v"`
	SeqGlobal int64  `json:"seq_global"`
	TsMs     int64  `json:"ts_ms"`
	Type     string `json:"type"`

	Actor     string `json:"actor"`
	SessionID string `json:"session_id"`
	SeqSession int64 `json:"seq_session"`

	// bytes-only
	Dir     string `json:"dir,omitempty"`      // in|out
	DataB64 string `json:"data_b64,omitempty"` // chunk payload
	N       int    `json:"n,omitempty"`        // bytes len

	// checkpoint-only
	Sig        string `json:"sig,omitempty"`
	NormSHA256 string `json:"norm_sha256,omitempty"`
	NormLen    int    `json:"norm_len,omitempty"`

	PrevHash string `json:"prev_hash"`
	Hash     string `json:"hash"`
	HMAC     string `json:"hmac"`
}

