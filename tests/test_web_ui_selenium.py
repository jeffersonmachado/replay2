#!/usr/bin/env python3
"""Basic browser automation tests for Control Server UI."""

from __future__ import annotations

import importlib.util
import os
import signal
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

GATEWAY_DIR = Path(__file__).resolve().parents[1] / "gateway"

import sys
sys.path.insert(0, str(GATEWAY_DIR))

import dakota_gateway.auth as auth
from dakota_gateway.state_db import connect, init_db, now_ms

CONTROL_SERVER_PATH = GATEWAY_DIR / "control" / "server.py"
SPEC = importlib.util.spec_from_file_location("control_server", CONTROL_SERVER_PATH)
CONTROL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CONTROL)
ControlServer = CONTROL.ControlServer
Handler = CONTROL.Handler

try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.chrome.options import Options as ChromeOptions
except Exception:  # pragma: no cover - optional dependency
    webdriver = None


def _kill_process_tree(pid):
    """Mata o processo e todos os descendentes (chromedriver -> chrome ->
    helpers). Teardown obrigatorio: sem allowlist por nome no process_tree.py,
    qualquer processo do browser que sobreviva ao teste e' classificado como
    vazamento/escape."""
    if not pid:
        return

    def _descendants(root_pid):
        try:
            r = subprocess.run(
                ["ps", "--no-headers", "-eo", "pid,ppid"],
                capture_output=True, text=True, timeout=5,
            )
        except Exception:
            return []
        children = {}
        for line in r.stdout.splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                children.setdefault(int(parts[1]), []).append(int(parts[0]))
        found, stack = [], [root_pid]
        while stack:
            cur = stack.pop()
            for child in children.get(cur, []):
                found.append(child)
                stack.append(child)
        return found

    for _ in range(5):
        alive = []
        for target in _descendants(pid) + [pid]:
            try:
                os.kill(target, 0)
                alive.append(target)
            except OSError:
                pass
        if not alive:
            return
        for target in alive:
            try:
                os.kill(target, signal.SIGKILL)
            except OSError:
                pass
        time.sleep(1)


@unittest.skipUnless(webdriver is not None, "selenium nao instalado — testes de UI pulados (sem falso positivo)")
class TestWebUISelenium(unittest.TestCase):
    def setUp(self):
        self.tmpdir = None
        self.server = None
        self.driver = None
        if webdriver is None:
            return

        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = f"{self.tmpdir.name}/test.db"

        con = connect(self.db_path)
        init_db(con)
        ph = auth.pbkdf2_hash_password("admin123")
        con.execute(
            "INSERT INTO users(username,password_hash,role,created_at_ms) VALUES(?,?,'admin',?)",
            ("admin", ph, now_ms()),
        )
        con.close()

        self.server = ControlServer(
            ("127.0.0.1", 0),
            Handler,
            db_path=self.db_path,
            cookie_secret=b"test_cookie_secret_32_bytes___",
            hmac_key=b"test_hmac_key_32_bytes__________",
        )
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        time.sleep(0.4)

        chrome_opts = ChromeOptions()
        chrome_opts.add_argument("--headless=new")
        chrome_opts.add_argument("--no-sandbox")
        chrome_opts.add_argument("--disable-dev-shm-usage")
        # Sem crashpad: o handler se auto-destaca (sessao propria) e seria
        # flagado como escape/vazamento pelo process_tree.py.
        chrome_opts.add_argument("--disable-crashpad")
        chrome_opts.add_argument("--disable-crash-reporter")
        self.driver = webdriver.Chrome(options=chrome_opts)

    def tearDown(self):
        driver = getattr(self, "driver", None)
        if driver is not None:
            service_pid = None
            try:
                service_pid = driver.service.process.pid
            except Exception:
                service_pid = None
            try:
                driver.quit()
            except Exception:
                pass
            # Garante que nada do browser sobrevive (quit() best-effort).
            _kill_process_tree(service_pid)
        if getattr(self, "server", None):
            self.server.shutdown()
        if getattr(self, "tmpdir", None):
            self.tmpdir.cleanup()

    def test_login_and_open_dashboard(self):
        self.driver.get(f"http://127.0.0.1:{self.port}/login")

        self.driver.find_element(By.ID, "u").send_keys("admin")
        self.driver.find_element(By.ID, "p").send_keys("admin123")
        self.driver.find_element(By.TAG_NAME, "button").click()

        time.sleep(0.8)
        self.assertIn("Replay Control", self.driver.page_source)

    def test_create_run_from_ui(self):
        self.driver.get(f"http://127.0.0.1:{self.port}/login")
        self.driver.find_element(By.ID, "u").send_keys("admin")
        self.driver.find_element(By.ID, "p").send_keys("admin123")
        self.driver.find_element(By.TAG_NAME, "button").click()
        time.sleep(0.8)

        # O formulário de criação vive na página dedicada /runs/new.
        self.driver.get(f"http://127.0.0.1:{self.port}/runs/new")
        time.sleep(0.8)
        self.driver.find_element(By.ID, "log_dir").send_keys(self.tmpdir.name)
        self.driver.find_element(By.ID, "target_host").send_keys("host")
        self.driver.find_element(By.ID, "target_user").send_keys("user")
        self.driver.find_element(By.ID, "target_cmd").send_keys("echo ok")
        self.driver.find_element(By.ID, "create_run_btn").click()

        # Ao criar, a UI redireciona para o detalhe da run (/runs/{id}).
        for _ in range(30):
            url = self.driver.current_url
            if "/runs/" in url and not url.endswith("/runs/new"):
                break
            time.sleep(0.2)
        self.assertIn("/runs/", self.driver.current_url)
        self.assertNotEqual(self.driver.current_url.rstrip("/"), f"http://127.0.0.1:{self.port}/runs/new")


if __name__ == "__main__":
    unittest.main(verbosity=2)
