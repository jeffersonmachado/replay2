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
            # 30s: hosts lentos (AIX sob carga) respondem o endpoint de replay
            # em 4-15s; o timeout de 10s gerava falhas intermitentes falsas.
            resp = opener.open(r, timeout=30)
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
    # /api/captures pagina (default 20, máx 500/página): percorre todas as
    # páginas — capturas com conteúdo fora da primeira página não podem
    # ficar invisíveis para os checks de sessão/replay.
    s, payload, _ = req("GET", "/api/captures?limit=500")
    check(s == 200, "GET /api/captures → 200", f"status={s}")
    total = payload.get("total", 0)
    captures_all = list(payload.get("captures", []))
    while len(captures_all) < total:
        s, page, _ = req("GET", f"/api/captures?limit=500&offset={len(captures_all)}")
        if s != 200 or not page.get("captures"):
            break
        captures_all.extend(page["captures"])
    payload["captures"] = captures_all
    print(f"         total de capturas: {total}")
    print()

    # 4. Detalhe + sessões + eventos
    # Para os passos 4-7 usa a MENOR captura com conteúdo real (event_count>10):
    # capturas enormes (ex.: >50k eventos) levam dezenas de segundos por
    # endpoint e estouram o timeout do smoke — valida-se o pipeline, não o
    # desempenho com sessões gigantes.
    captures = payload.get("captures", [])
    if captures:
        com_conteudo = [c for c in captures if c.get("session_count", 0) > 0 and c.get("event_count", 0) > 10]
        com_sessao = [c for c in captures if c.get("session_count", 0) > 0]
        pool = com_conteudo or com_sessao or captures
        cid = min(pool, key=lambda c: c.get("event_count", 0))["id"]
        print("--- 4. Detalhe ---")
        s, detail, _ = req("GET", f"/api/captures/{cid}")
        check(s == 200, f"GET /api/captures/{cid} → 200", f"status={s}")
        print(f"         status={detail.get('status','?')} sessions={detail.get('session_count','?')} events={detail.get('event_count','?')}")

        print("--- 5. Sessões ---")
        s, sessions, _ = req("GET", f"/api/captures/{cid}/sessions")
        check(s == 200, f"GET sessions → 200", f"status={s}")
        sess_list = sessions.get("sessions", [])
        print(f"         total de sessões: {len(sess_list)}")

        # 6. Replay — usa a menor sessão com saída de tela (bytes_out>0)
        # entre todas as capturas: sessões enormes (ex.: >50k eventos)
        # estouram o timeout do smoke; o objetivo é validar o pipeline.
        print("--- 6. Replay ---")
        best = None
        for cap in captures:
            if cap.get("event_count", 0) > 10000:
                continue
            s, sess_payload, _ = req("GET", f"/api/captures/{cap['id']}/sessions")
            for sess in sess_payload.get("sessions", []):
                if sess.get("session_id") and sess.get("bytes_out", 0) > 0:
                    count = sess.get("event_count", 0)
                    if best is None or count < best[2]:
                        best = (cap["id"], sess["session_id"], count)
        if best:
            rid, rsid, _ = best
            s, replay, _ = req("GET", f"/api/captures/{rid}/replay?session_id={rsid}&limit=20")
            check(s == 200, "GET replay → 200", f"status={s}")
            geom = replay.get("geometry", {})
            tl = replay.get("timeline", [])
            pb = replay.get("playback", {})
            print(f"         capture={rid} geometry={geom.get('rows','?')}x{geom.get('cols','?')} timeline_events={len(tl)} playback_events={pb.get('event_count',0)}")

        # 7. Eventos
        print("--- 7. Eventos ---")
        s, events, _ = req("GET", f"/api/captures/{cid}/events?limit=20")
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
