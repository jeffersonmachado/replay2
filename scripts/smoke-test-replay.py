#!/usr/bin/env python3
"""smoke-test-replay.py — Valida a estrutura de dados de replay via API HTTP."""
import argparse, http.cookiejar, json, sys, os, urllib.request, urllib.error

PASS = FAIL = 0

def check(ok: bool, label: str, detail: str = ""):
    global PASS, FAIL
    if ok:
        print(f"  [PASS] {label}")
        PASS += 1
    else:
        print(f"  [FAIL] {label} — {detail}")
        FAIL += 1

def main():
    global PASS, FAIL
    p = argparse.ArgumentParser(description="Smoke test de replay")
    p.add_argument("--host", default=os.environ.get("TARGET_HOST", "127.0.0.1"))
    p.add_argument("--port", default=os.environ.get("TARGET_PORT", "8080"))
    p.add_argument("--user", default=os.environ.get("ADMIN_USER", ""))
    p.add_argument("--pass", dest="password", default=os.environ.get("ADMIN_PASS", ""))
    args = p.parse_args()

    BASE = f"http://{args.host}:{args.port}"
    print(f"=== Smoke Test: Replay ===")
    print(f"Servidor: {BASE}\n")

    # Cookie jar
    cj = http.cookiejar.MozillaCookieJar()
    cookie_file = "/tmp/smoke-replay-cookies.txt"
    if os.path.exists(cookie_file):
        try: cj.load(cookie_file, ignore_discard=True, ignore_expires=True)
        except: pass
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

    def req(method, path, body=None):
        url = f"{BASE}{path}"
        data = json.dumps(body).encode() if body else None
        r = urllib.request.Request(url, data=data, method=method)
        r.add_header("Content-Type", "application/json")
        try:
            resp = opener.open(r, timeout=10)
            cj.save(cookie_file, ignore_discard=True, ignore_expires=True)
            raw = resp.read().decode()
            return resp.status, (json.loads(raw) if raw.strip() else {}), dict(resp.headers)
        except urllib.error.HTTPError as e:
            cj.save(cookie_file, ignore_discard=True, ignore_expires=True)
            raw = e.read().decode()
            try:
                return e.code, json.loads(raw) if raw.strip() else {}, dict(e.headers)
            except:
                return e.code, {"error": raw}, dict(e.headers)
        except Exception as e:
            return 0, {"error": str(e)}, {}

    # Login
    s, _, _ = req("POST", "/api/login", {"username": args.user, "password": args.password})
    if s != 200:
        print(f"  [FAIL] Login — status={s}")
        sys.exit(1)

    # Find a session with data
    print("--- Buscando sessão com dados de replay ---")
    s, caps, _ = req("GET", "/api/captures")
    if not caps.get("captures"):
        check(False, "Setup", "nenhuma captura disponível")
        sys.exit(1)

    cid, sid = None, None
    for cap in caps["captures"]:
        s, sessions, _ = req("GET", f"/api/captures/{cap['id']}/sessions")
        for sess in sessions.get("sessions", []):
            if sess.get("session_id"):
                cid, sid = cap["id"], sess["session_id"]
                break
        if sid:
            break

    if not sid:
        check(False, "Setup", "nenhuma sessão disponível")
        sys.exit(1)

    print(f"         capture_id={cid} session_id={sid[:20]}...")
    print()

    # Get replay data
    s, data, _ = req("GET", f"/api/captures/{cid}/replay?session_id={sid}")
    if s != 200:
        check(False, "GET replay", f"status={s}")
        sys.exit(1)

    # 1. Geometry
    print("--- 1. Geometria ---")
    geom = data.get("geometry", {})
    rows, cols = geom.get("rows"), geom.get("cols")
    src = geom.get("geometry_source", "?")
    check(rows and cols, f"geometry: {rows}x{cols} (source={src})", f"rows={rows} cols={cols}")
    check(src in ("explicit", "session_metadata", "tty", "environment", "resize_event", "legacy_fallback"), f"geometry_source válido: {src}")

    # 2. Encoding
    print("--- 2. Encoding ---")
    enc = geom.get("encoding", "?")
    check(enc and enc != "?", f"encoding: {enc}")

    # 3. Timeline
    print("--- 3. Timeline ---")
    tl = data.get("timeline", [])
    has_ts = all(e.get("timestamp_ms") is not None for e in tl) if tl else True
    check(len(tl) > 0, f"timeline: {len(tl)} eventos")
    check(has_ts, "timestamp_ms em todos os eventos")

    # 4. Playback
    print("--- 4. Playback ---")
    pb = data.get("playback", {})
    evs = pb.get("events", [])
    has_b64 = all(e.get("data_b64") for e in evs) if evs else True
    check(pb.get("event_count", 0) > 0, f"playback: {pb.get('event_count', 0)} eventos")
    check(has_b64, "data_b64 em todos os eventos")

    # 5. Snapshots
    print("--- 5. Snapshots ---")
    snaps = [e for e in tl if e.get("content_kind") == "terminal_snapshot"]
    has_text_sig = any(e.get("text_sig") for e in snaps)
    has_visual_sig = any(e.get("visual_sig") for e in snaps)
    if snaps:
        check(True, f"snapshots: {len(snaps)} terminal_snapshot")
        check(has_text_sig, f"text_sig presente")
        check(has_visual_sig, f"visual_sig presente")
    else:
        print(f"  [INFO] snapshots: 0 (sem grupos OUT na sessão)")

    # 6. Session start
    print("--- 6. Session Start ---")
    ss = data.get("session_start")
    if ss:
        check(True, f"session_start: {ss.get('rows','?')}x{ss.get('cols','?')} term={ss.get('term','?')} enc={ss.get('encoding','?')}")
    else:
        check(False, "session_start ausente")

    print()
    print(f"=== Resultado: Replay Smoke ===")
    print(f"Pass: {PASS} | Fail: {FAIL}")
    sys.exit(1 if FAIL > 0 else 0)

if __name__ == "__main__":
    main()
