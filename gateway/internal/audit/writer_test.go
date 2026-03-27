package audit

import (
	"encoding/base64"
	"os"
	"path/filepath"
	"testing"
)

func TestCanonicalStable(t *testing.T) {
	ev := &Event{
		V:         "v1",
		SeqGlobal: 123,
		TsMs:      456,
		Type:      "bytes",
		Actor:     "alice",
		SessionID: "sess1",
		SeqSession: 7,
		Dir:       "in",
		DataB64:   base64.StdEncoding.EncodeToString([]byte("hi")),
		N:         2,
		PrevHash:  "prev",
	}
	s := CanonicalString(ev)
	if len(s) == 0 {
		t.Fatal("empty canonical")
	}
	// ensures presence of required keys
	for _, k := range []string{"v=", "seq_global=", "ts_ms=", "type=", "actor=", "session_id=", "seq_session=", "prev_hash="} {
		if !containsLinePrefix(s, k) {
			t.Fatalf("missing %q in canonical payload", k)
		}
	}
}

func containsLinePrefix(s, prefix string) bool {
	for _, ln := range splitLines(s) {
		if len(ln) >= len(prefix) && ln[:len(prefix)] == prefix {
			return true
		}
	}
	return false
}

func splitLines(s string) []string {
	var out []string
	start := 0
	for i := 0; i < len(s); i++ {
		if s[i] == '\n' {
			out = append(out, s[start:i])
			start = i + 1
		}
	}
	if start < len(s) {
		out = append(out, s[start:])
	}
	return out
}

func TestWriterAppendsChain(t *testing.T) {
	dir := t.TempDir()
	w, err := NewWriter(WriterConfig{
		Dir:         dir,
		HMACKey:     []byte("secret"),
		RotateBytes: 0,
	})
	if err != nil {
		t.Fatal(err)
	}
	defer w.Close()

	ev1 := &Event{Type: "session_start", Actor: "a", SessionID: "s", SeqSession: 1, TsMs: 1}
	ev2 := &Event{Type: "bytes", Actor: "a", SessionID: "s", SeqSession: 2, TsMs: 2, Dir: "in", DataB64: "AA==", N: 1}
	if err := w.Append(ev1); err != nil {
		t.Fatal(err)
	}
	if err := w.Append(ev2); err != nil {
		t.Fatal(err)
	}
	if ev2.PrevHash != ev1.Hash {
		t.Fatalf("expected chain prev_hash=%s got %s", ev1.Hash, ev2.PrevHash)
	}

	// ensure log file exists
	st, _ := os.ReadFile(filepath.Join(dir, "audit.state"))
	if len(st) == 0 {
		t.Fatal("missing state file")
	}
}

