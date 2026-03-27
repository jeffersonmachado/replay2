package audit

import (
	"bufio"
	"crypto/rand"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"golang.org/x/sys/unix"
)

type WriterConfig struct {
	Dir         string
	HMACKey     []byte
	RotateBytes int64 // 0 = no rotation (single file)
}

type chainState struct {
	SeqGlobal int64
	PrevHash  string
	CurrentLog string
	Part      int
}

// Writer provides a global total order (seq_global) across all sessions by using
// a single lock+state file in the log directory.
type Writer struct {
	cfg WriterConfig

	lockFile *os.File
	statePath string
}

func NewWriter(cfg WriterConfig) (*Writer, error) {
	if cfg.Dir == "" {
		return nil, errors.New("audit: Dir is required")
	}
	if len(cfg.HMACKey) == 0 {
		return nil, errors.New("audit: HMACKey is required")
	}
	if err := os.MkdirAll(cfg.Dir, 0o755); err != nil {
		return nil, err
	}

	lockPath := filepath.Join(cfg.Dir, "audit.lock")
	lf, err := os.OpenFile(lockPath, os.O_CREATE|os.O_RDWR, 0o600)
	if err != nil {
		return nil, err
	}

	return &Writer{
		cfg:       cfg,
		lockFile:  lf,
		statePath: filepath.Join(cfg.Dir, "audit.state"),
	}, nil
}

func (w *Writer) Close() error {
	if w.lockFile != nil {
		_ = w.lockFile.Close()
	}
	return nil
}

func randomSuffix() string {
	var b [3]byte
	_, _ = rand.Read(b[:])
	return fmt.Sprintf("%02x%02x%02x", b[0], b[1], b[2])
}

func (w *Writer) loadStateLocked() (chainState, error) {
	var st chainState
	data, err := os.ReadFile(w.statePath)
	if err != nil {
		if os.IsNotExist(err) {
			// fresh state
			st.SeqGlobal = 0
			st.PrevHash = ""
			st.CurrentLog = ""
			st.Part = 0
			return st, nil
		}
		return st, err
	}
	lines := strings.Split(string(data), "\n")
	for _, ln := range lines {
		ln = strings.TrimSpace(ln)
		if ln == "" {
			continue
		}
		k, v, ok := strings.Cut(ln, "=")
		if !ok {
			continue
		}
		switch k {
		case "seq_global":
			st.SeqGlobal, _ = strconv.ParseInt(v, 10, 64)
		case "prev_hash":
			st.PrevHash = v
		case "current_log":
			st.CurrentLog = v
		case "part":
			st.Part, _ = strconv.Atoi(v)
		}
	}
	return st, nil
}

func (w *Writer) saveStateLocked(st chainState) error {
	tmp := w.statePath + ".tmp." + randomSuffix()
	content := fmt.Sprintf("seq_global=%d\nprev_hash=%s\ncurrent_log=%s\npart=%d\n",
		st.SeqGlobal, st.PrevHash, st.CurrentLog, st.Part,
	)
	if err := os.WriteFile(tmp, []byte(content), 0o600); err != nil {
		return err
	}
	return os.Rename(tmp, w.statePath)
}

func (w *Writer) currentLogPathLocked(st *chainState) (string, error) {
	if st.CurrentLog != "" {
		return st.CurrentLog, nil
	}
	ts := time.Now().UTC().Format("20060102-150405")
	st.Part = 1
	st.CurrentLog = filepath.Join(w.cfg.Dir, fmt.Sprintf("audit-%s.part%03d.jsonl", ts, st.Part))
	return st.CurrentLog, nil
}

func (w *Writer) maybeRotateLocked(st *chainState) error {
	if w.cfg.RotateBytes <= 0 {
		return nil
	}
	path, err := w.currentLogPathLocked(st)
	if err != nil {
		return err
	}
	fi, err := os.Stat(path)
	if err != nil {
		if os.IsNotExist(err) {
			return nil
		}
		return err
	}
	if fi.Size() < w.cfg.RotateBytes {
		return nil
	}

	// finalize manifest for current file
	if err := WriteManifest(path); err != nil {
		return err
	}

	// rotate to new file (same timestamp prefix is ok; part increments)
	base := filepath.Base(path)
	prefix := strings.Split(base, ".part")[0] // audit-YYYYMMDD-HHMMSS
	st.Part++
	st.CurrentLog = filepath.Join(w.cfg.Dir, fmt.Sprintf("%s.part%03d.jsonl", prefix, st.Part))
	return nil
}

// Append assigns seq_global, fills hash/hmac and appends to the current JSONL log.
// It is safe for concurrent processes (uses flock on audit.lock).
func (w *Writer) Append(ev *Event) error {
	if ev == nil {
		return errors.New("audit: nil event")
	}
	ev.V = "v1"

	// Global lock for seq+chain+rotation
	if err := unix.Flock(int(w.lockFile.Fd()), unix.LOCK_EX); err != nil {
		return err
	}
	defer func() { _ = unix.Flock(int(w.lockFile.Fd()), unix.LOCK_UN) }()

	st, err := w.loadStateLocked()
	if err != nil {
		return err
	}
	if err := w.maybeRotateLocked(&st); err != nil {
		return err
	}
	logPath, err := w.currentLogPathLocked(&st)
	if err != nil {
		return err
	}

	// seq_global + chain
	st.SeqGlobal++
	ev.SeqGlobal = st.SeqGlobal
	ev.PrevHash = st.PrevHash

	payload := []byte(CanonicalString(ev))
	ev.Hash = sha256Hex(payload)
	ev.HMAC = hmacSHA256Hex(w.cfg.HMACKey, payload)

	// append json line
	if err := os.MkdirAll(filepath.Dir(logPath), 0o755); err != nil {
		return err
	}
	f, err := os.OpenFile(logPath, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o600)
	if err != nil {
		return err
	}
	defer f.Close()

	bw := bufio.NewWriterSize(f, 256*1024)
	enc, err := json.Marshal(ev)
	if err != nil {
		return err
	}
	if _, err := bw.Write(enc); err != nil {
		return err
	}
	if err := bw.WriteByte('\n'); err != nil {
		return err
	}
	if err := bw.Flush(); err != nil {
		return err
	}

	// update state
	st.PrevHash = ev.Hash
	return w.saveStateLocked(st)
}

// WriteManifest computes file sha256 and basic metadata.
// It writes sidecar: <file>.manifest.json.
func WriteManifest(jsonlPath string) error {
	fi, err := os.Stat(jsonlPath)
	if err != nil {
		return err
	}
	f, err := os.Open(jsonlPath)
	if err != nil {
		return err
	}
	defer f.Close()

	// Compute file sha256
	h := sha256.New()
	if _, err := io.Copy(h, f); err != nil {
		return err
	}
	fileSHA := fmt.Sprintf("%x", h.Sum(nil))

	// Extract first/last hashes + seq range (best-effort scan)
	if _, err := f.Seek(0, io.SeekStart); err != nil {
		return err
	}
	var firstHash, lastHash string
	var seqStart, seqEnd int64
	sc := bufio.NewScanner(f)
	// allow long lines
	buf := make([]byte, 0, 1024*1024)
	sc.Buffer(buf, 10*1024*1024)
	for sc.Scan() {
		var ev Event
		if err := json.Unmarshal(sc.Bytes(), &ev); err != nil {
			continue
		}
		if seqStart == 0 {
			seqStart = ev.SeqGlobal
			firstHash = ev.Hash
		}
		seqEnd = ev.SeqGlobal
		lastHash = ev.Hash
	}
	_ = sc.Err()

	manifest := map[string]any{
		"path":       filepath.Base(jsonlPath),
		"bytes":      fi.Size(),
		"seq_start":  seqStart,
		"seq_end":    seqEnd,
		"first_hash": firstHash,
		"last_hash":  lastHash,
		"file_sha256": fileSHA,
		"generated_at": time.Now().UTC().Format(time.RFC3339Nano),
	}
	b, err := json.MarshalIndent(manifest, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(jsonlPath+".manifest.json", b, 0o600)
}

