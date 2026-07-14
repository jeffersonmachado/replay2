#!/usr/bin/env python3
"""smoke-test-capture.py — Valida o pipeline de captura via API HTTP."""
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
    p = argparse.ArgumentParser(description="Smoke test de captura")
    p.add_argument("--host", default=os.environ.get("TARGET_HOST", "127.0.0.1"))
    p.add_argument("--port", default=os.environ.get("TARGET_PORT", "8080"))
    p.add_argument("--user", default=os.environ.get("ADMIN_USER", ""))
    p.add_argument("--pass", dest="password", default=os.environ.get("ADMIN_PASS", ""))
    args = p.parse_args()

    BASE = f"http://{args.host}:{args.port}"
    print(f"=== Smoke Test: Capture ===")
    print(f"Servidor: {BASE}\n")

    # Cookie jar
    cj = http.cookiejar.MozillaCookieJar()
    cookie_file = "/tmp/smoke-capture-cookies.txt"
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

    # 1. Health
    print("--- 1. Health/Ready ---")
    s, _, _ = req("GET", "/health")
    check(s == 200, "GET /health → 200", f"status={s}")
    s, _, _ = req("GET", "/ready")
    check(s == 200, "GET /ready → 200", f"status={s}")
    print()

    # 2. Login
    print("--- 2. Autenticação ---")
    s, payload, hdrs = req("POST", "/api/login", {"username": args.user, "password": args.password})
    check(s == 200, "POST /api/login → 200", f"status={s}")
    set_cookie = hdrs.get("Set-Cookie") or hdrs.get("set-cookie") or ""
    check("dakota_session" in set_cookie, "Cookie dakota_session presente")
    print()

    # 3. Listagem
    print("--- 3. Listagem ---")
    s, payload, _ = req("GET", "/api/captures")
    check(s == 200, "GET /api/captures → 200", f"status={s}")
    total = payload.get("total", 0)
    print(f"         total de capturas: {total}")
    print()

    # 4. Detalhe + sessões + eventos
    captures = payload.get("captures", [])
    if captures:
        cid = captures[0]["id"]
        print("--- 4. Detalhe ---")
        s, detail, _ = req("GET", f"/api/captures/{cid}")
        check(s == 200, f"GET /api/captures/{cid} → 200", f"status={s}")
        print(f"         status={detail.get('status','?')} sessions={detail.get('session_count','?')} events={detail.get('event_count','?')}")

        print("--- 5. Sessões ---")
        s, sessions, _ = req("GET", f"/api/captures/{cid}/sessions")
        check(s == 200, f"GET sessions → 200", f"status={s}")
        sess_list = sessions.get("sessions", [])
        print(f"         total de sessões: {len(sess_list)}")

        # 6. Replay da primeira sessão
        if sess_list:
            sid = sess_list[0].get("session_id", "")
            print("--- 6. Replay ---")
            s, replay, _ = req("GET", f"/api/captures/{cid}/replay?session_id={sid}")
            check(s == 200, "GET replay → 200", f"status={s}")
            geom = replay.get("geometry", {})
            tl = replay.get("timeline", [])
            pb = replay.get("playback", {})
            print(f"         geometry={geom.get('rows','?')}x{geom.get('cols','?')} timeline_events={len(tl)} playback_events={pb.get('event_count',0)}")

        # 7. Eventos
        print("--- 7. Eventos ---")
        s, events, _ = req("GET", f"/api/captures/{cid}/events")
        check(s == 200, f"GET events → 200", f"status={s}")
        evlist = events.get("events", [])
        print(f"         eventos retornados: {len(evlist)}")
    else:
        print("--- 4-7. Pulados (sem capturas disponíveis) ---")

    print()
    print(f"=== Resultado: Capture Smoke ===")
    print(f"Pass: {PASS} | Fail: {FAIL}")
    sys.exit(1 if FAIL > 0 else 0)

if __name__ == "__main__":
    main()
