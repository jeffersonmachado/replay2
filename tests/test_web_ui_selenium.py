#!/usr/bin/env python3
"""Basic browser automation tests for Control Server UI."""

from __future__ import annotations

import importlib.util
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


@unittest.skipIf(webdriver is None, "selenium not installed")
class TestWebUISelenium(unittest.TestCase):
    def setUp(self):
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
        self.driver = webdriver.Chrome(options=chrome_opts)

    def tearDown(self):
        if getattr(self, "driver", None):
            self.driver.quit()
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

        self.driver.find_element(By.ID, "log_dir").send_keys("/tmp/test")
        self.driver.find_element(By.ID, "target_host").send_keys("host")
        self.driver.find_element(By.ID, "target_user").send_keys("user")
        self.driver.find_element(By.ID, "target_cmd").send_keys("echo ok")

        buttons = self.driver.find_elements(By.TAG_NAME, "button")
        # First action button in form is "Criar run".
        for b in buttons:
            if b.text.strip().lower().startswith("criar"):
                b.click()
                break

        time.sleep(0.8)
        self.assertIn("runs:", self.driver.page_source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
