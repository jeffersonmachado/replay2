#!/usr/bin/env python3
"""
Quick test suite para Control Server API - minimal dependencies
Uso: python3 tests/quick-test-api.py
"""

import json
import tempfile
import threading
import time
import sys
import http.client
import http.cookiejar
from pathlib import Path
from urllib.request import Request, build_opener, HTTPCookieProcessor
from urllib.error import HTTPError

# Add gateway to path
GATEWAY_DIR = Path(__file__).resolve().parents[1] / "gateway"
sys.path.insert(0, str(GATEWAY_DIR))

# Import from gateway modules
import dakota_gateway.auth as auth
from dakota_gateway.state_db import connect, init_db, now_ms

# Import server (need to add control to path)
import importlib.util
control_server_path = GATEWAY_DIR / "control" / "server.py"
spec = importlib.util.spec_from_file_location("control_server", control_server_path)
control_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(control_module)
ControlServer = control_module.ControlServer
Handler = control_module.Handler


class Colors:
    BLUE = '\033[0;34m'
    GREEN = '\033[0;32m'
    RED = '\033[0;31m'
    YELLOW = '\033[1;33m'
    NC = '\033[0m'


def log_header(msg):
    print(f"{Colors.BLUE}=== {msg} ==={Colors.NC}")


def log_test(msg):
    print(f"{Colors.YELLOW}Test: {msg}...{Colors.NC}", end=" ", flush=True)


def log_pass():
    print(f"{Colors.GREEN}✓{Colors.NC}")


def log_fail(reason):
    print(f"{Colors.RED}✗ ({reason}){Colors.NC}")


def main():
    global PORT
    
    log_header("Dakota Replay2 - Quick API Test")
    
    # Setup
    print(f"\n{Colors.YELLOW}Setup{Colors.NC}")
    tmpdir = tempfile.TemporaryDirectory()
    db_path = f"{tmpdir.name}/test.db"
    cookie_secret = b"test_cookie_secret_32_bytes___"
    hmac_key = b"test_hmac_key_32_bytes__________"
    
    print(f"  Temp dir: {tmpdir.name}")
    print(f"  DB: {db_path}")
    
    # Init DB
    con = connect(db_path)
    init_db(con)
    username = "admin"
    password = "admin123"
    ph = auth.pbkdf2_hash_password(password)
    con.execute(
        "INSERT INTO users(username,password_hash,role,created_at_ms) VALUES(?,?,'admin',?)",
        (username, ph, now_ms())
    )
    con.close()
    
    # Start server
    print(f"\n{Colors.YELLOW}Starting Server{Colors.NC}")
    server = ControlServer(
        ("127.0.0.1", 0),  # Random port
        Handler,
        db_path=db_path,
        cookie_secret=cookie_secret,
        hmac_key=hmac_key
    )
    PORT = server.server_address[1]
    print(f"  Server: http://127.0.0.1:{PORT}")
    
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.5)
    
    # Run tests with persistent opener
    print(f"\n{Colors.YELLOW}Tests{Colors.NC}")
    
    passed = 0
    failed = 0
    
    # Create a single persistent opener with cookie jar for all tests
    cookie_jar = http.cookiejar.CookieJar()
    opener = build_opener(HTTPCookieProcessor(cookie_jar))
    
    def test_request(method, path, data=None):
        """Helper to make requests with persistent cookie jar"""
        url = f"http://127.0.0.1:{PORT}{path}"
        headers = {"Content-Type": "application/json"}
        
        body = None
        if data:
            body = json.dumps(data).encode()
        
        req = Request(url, data=body, headers=headers, method=method)
        
        try:
            with opener.open(req, timeout=5) as resp:
                resp_body = resp.read().decode()
                try:
                    return resp.status, json.loads(resp_body) if resp_body else {}
                except json.JSONDecodeError:
                    return resp.status, {"_raw": resp_body}
        except HTTPError as e:
            return e.code, {}
    
    # Test 1: Login page
    log_test("GET /login")
    try:
        status, _ = test_request("GET", "/login")
        if status == 200:
            log_pass()
            passed += 1
        else:
            log_fail(f"status {status}")
            failed += 1
    except Exception as e:
        log_fail(str(e))
        failed += 1
    
    # Test 2: Login success (will set session cookie)
    log_test("POST /api/login (correct password)")
    try:
        status, data = test_request("POST", "/api/login", 
                                    {"username": "admin", "password": "admin123"})
        if status == 200:
            log_pass()
            passed += 1
        else:
            log_fail(f"status {status}: {data}")
            failed += 1
    except Exception as e:
        log_fail(str(e))
        failed += 1
    
    # Test 3: GET /api/me (should work with cookie from login)
    log_test("GET /api/me (authenticated user)")
    try:
        status, data = test_request("GET", "/api/me")
        if status == 200 and data.get("username") == "admin":
            log_pass()
            passed += 1
        else:
            log_fail(f"status {status}, expected 200 with username")
            failed += 1
    except Exception as e:
        log_fail(str(e))
        failed += 1
    
    # Test 4: GET /api/runs (empty)
    log_test("GET /api/runs (empty list)")
    try:
        status, data = test_request("GET", "/api/runs")
        if status == 200 and data.get("runs", []) == []:
            log_pass()
            passed += 1
        else:
            log_fail(f"status {status}: {data}")
            failed += 1
    except Exception as e:
        log_fail(str(e))
        failed += 1
    
    # Test 5: POST /api/runs (create)
    log_test("POST /api/runs (create run)")
    try:
        status, data = test_request("POST", "/api/runs",
                                    {
                                        "log_dir": "/tmp/test",
                                        "target_host": "test.com",
                                        "target_user": "user",
                                        "mode": "strict-global"
                                    })
        if status == 200 and "id" in data:
            log_pass()
            passed += 1
            
            # Test 6: Get runs (non-empty)
            log_test("GET /api/runs (should have 1 run)")
            try:
                status, data = test_request("GET", "/api/runs")
                if status == 200 and len(data.get("runs", [])) > 0:
                    log_pass()
                    passed += 1
                else:
                    log_fail(f"expected 1 run, got {len(data.get('runs', []))}")
                    failed += 1
            except Exception as e:
                log_fail(str(e))
                failed += 1
        else:
            log_fail(f"status {status}: {data}")
            failed += 1
    except Exception as e:
        log_fail(str(e))
        failed += 1
    
    # Test 7: Login failure (do after authenticated tests)
    log_test("POST /api/login (wrong password)")
    try:
        status, _ = test_request("POST", "/api/login",
                                 {"username": "admin", "password": "wrongpass"})
        if status == 401:
            log_pass()
            passed += 1
        else:
            log_fail(f"status {status}, expected 401")
            failed += 1
    except Exception as e:
        log_fail(str(e))
        failed += 1

    # Test 8: Manual Cookie header should work for simple HTTP clients
    log_test("manual Cookie header round-trip")
    try:
        conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=5)
        conn.request(
            "POST",
            "/api/login",
            json.dumps({"username": "admin", "password": "admin123"}),
            {"Content-Type": "application/json"},
        )
        resp = conn.getresponse()
        set_cookie = resp.getheader("Set-Cookie") or ""
        resp.read()
        conn.close()

        cookie_pair = set_cookie.split(";", 1)[0]
        conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=5)
        conn.request("GET", "/api/me", headers={"Cookie": cookie_pair})
        resp = conn.getresponse()
        body = resp.read().decode()
        conn.close()
        data = json.loads(body) if body else {}

        if resp.status == 200 and data.get("username") == "admin" and '"' not in cookie_pair:
            log_pass()
            passed += 1
        else:
            log_fail(f"status {resp.status}, cookie={cookie_pair}")
            failed += 1
    except Exception as e:
        log_fail(str(e))
        failed += 1
    
    # Summary
    print(f"\n{Colors.BLUE}=== Summary ==={Colors.NC}")
    print(f"  Passed: {Colors.GREEN}{passed}{Colors.NC}")
    print(f"  Failed: {Colors.RED}{failed}{Colors.NC}")
    
    # Cleanup
    server.shutdown()
    tmpdir.cleanup()
    
    if failed == 0:
        print(f"\n{Colors.GREEN}✅ All tests passed!{Colors.NC}")
        return 0
    else:
        print(f"\n{Colors.RED}❌ Some tests failed{Colors.NC}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
