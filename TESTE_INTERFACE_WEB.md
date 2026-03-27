# Testagem da Interface Web - Dakota Replay2

## Status Atual de Tests

### ✅ Testes Existentes

**Core Engine (Tcl):**
- `tests/all.tcl` → Runner para tcltest
- `tests/capture.test.tcl` → Captura de tela
- `tests/normalize.test.tcl` → Normalização
- `tests/signature.test.tcl` → Geração de assinatura
- `tests/config.test.tcl` → Parsing de CLI
- `tests/integration_legacy_sim.test.tcl` → Integração com simulador

**Gateway (Python):**
- `gateway/tests/test_integrity.py` → Integridade (hash-chain + HMAC)

### ❌ Testes Faltando

- [ ] **Control Server API** (gateway/control/server.py)
- [ ] **Dashboard** (dashboard/server.py)
- [ ] **Web UI (browser tests)**
- [ ] **E2E (end-to-end)**

---

## 1. Executar Testes Existentes

### 1.1 Testes do Core Engine

```bash
cd /home/jmachado/projetos/dakota/replay2

# Rodar todos os testes Tcl
tclsh tests/all.tcl

# Output esperado:
# ---- capture.test.tcl
# ---- normalize.test.tcl
# ---- signature.test.tcl
# ---- config.test.tcl
# ---- integration_legacy_sim.test.tcl
# 
# Passed: 42
# Failed: 0
# 
# exit code: 0
```

### 1.2 Testes de Integridade (Gateway)

```bash
cd /home/jmachado/projetos/dakota/replay2/gateway

# Rodar testes Python
python3 -m pytest tests/test_integrity.py -v

# Ou com unittest (sem pytest)
python3 -m unittest tests.test_integrity -v
```

---

## 2. Testagem Manual da Interface Web

### 2.1 Iniciar Control Server

**Terminal 1:**

```bash
cd /home/jmachado/projetos/dakota/replay2

# Gerar secrets (first time)
mkdir -p /tmp/dakota-test
head -c 32 /dev/urandom > /tmp/dakota-test/hmac.key
head -c 32 /dev/urandom > /tmp/dakota-test/cookie_secret.key

# Rodar server
python3 gateway/control/server.py \
  --listen 127.0.0.1:8090 \
  --db /tmp/dakota-test/replay.db \
  --cookie-secret-file /tmp/dakota-test/cookie_secret.key \
  --hmac-key-file /tmp/dakota-test/hmac.key \
  --bootstrap-admin admin:admin123

# Output:
# Admin criado: admin
# listening on http://127.0.0.1:8090
```

### 2.2 Acessar Manual no Browser

```
URL: http://localhost:8090/login
Username: admin
Password: admin123
```

**Ou testar via curl:**

```bash
# 1. Login
curl -X POST http://localhost:8090/api/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin123"}' \
  -c /tmp/cookies.txt -v

# Esperado: 200 OK + Set-Cookie

# 2. Ver usuário autenticado
curl http://localhost:8090/api/me \
  -b /tmp/cookies.txt

# Esperado: {"id": 1, "username": "admin", "role": "admin"}

# 3. Criar run
curl -X POST http://localhost:8090/api/runs \
  -H "Content-Type: application/json" \
  -b /tmp/cookies.txt \
  -d '{
    "log_dir": "/tmp/test-log",
    "target_host": "localhost",
    "target_user": "testuser",
    "target_command": "",
    "mode": "strict-global"
  }'

# Esperado: {"id": 1}
```

---

## 3. Testagem Automatizada com Python

### 3.1 Criar Test Suite para Control Server

Criar arquivo: `gateway/tests/test_control_server.py`

```python
#!/usr/bin/env python3
"""
Tests para gateway/control/server.py (Control API)
"""
import json
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import HTTPError
import sys

# Add gateway to path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dakota_gateway import auth
from dakota_gateway.control.server import ControlServer, Handler
from dakota_gateway.state_db import connect, init_db, now_ms


class TestControlServer(unittest.TestCase):
    
    @classmethod
    def setUpClass(cls):
        """Inicia server uma vez para toda test class"""
        cls.tmpdir = tempfile.TemporaryDirectory()
        cls.db_path = f"{cls.tmpdir.name}/test.db"
        cls.cookie_secret = b"test_cookie_secret_32_bytes___"
        cls.hmac_key = b"test_hmac_key_32_bytes__________"
        
        # Initialize DB
        con = connect(cls.db_path)
        init_db(con)
        username = "testadmin"
        password = "testpass123"
        ph = auth.pbkdf2_hash_password(password)
        con.execute(
            "INSERT INTO users(username,password_hash,role,created_at_ms) VALUES(?,?,'admin',?)",
            (username, ph, now_ms())
        )
        con.close()
        
        # Start server in background thread
        cls.server = ControlServer(
            ("127.0.0.1", 0),  # Port 0 = random available port
            Handler,
            db_path=cls.db_path,
            cookie_secret=cls.cookie_secret,
            hmac_key=cls.hmac_key
        )
        cls.port = cls.server.server_address[1]
        cls.base_url = f"http://127.0.0.1:{cls.port}"
        
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()
        time.sleep(0.5)  # Give server time to start
    
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.tmpdir.cleanup()
    
    def _request(self, method, path, data=None, authenticated=True):
        """Helper para fazer HTTP requests"""
        url = f"{self.base_url}{path}"
        
        # Get auth cookie if needed
        if authenticated:
            # Login first
            login_req = Request(
                f"{self.base_url}/api/login",
                data=json.dumps({"username": "testadmin", "password": "testpass123"}).encode(),
                headers={"Content-Type": "application/json"}
            )
            with urlopen(login_req) as resp:
                cookie = resp.headers.get('Set-Cookie', '').split(';')[0]
            headers = {
                "Cookie": cookie,
                "Content-Type": "application/json"
            }
        else:
            headers = {"Content-Type": "application/json"}
        
        if data:
            req = Request(url, data=json.dumps(data).encode(), headers=headers, method=method)
        else:
            req = Request(url, headers=headers, method=method)
        
        try:
            with urlopen(req, timeout=5) as resp:
                body = resp.read().decode()
                return resp.status, json.loads(body) if body else {}
        except HTTPError as e:
            return e.code, {}
    
    def test_login_success(self):
        """Test: login com credenciais corretas"""
        status, data = self._request("POST", "/api/login", 
                                     {"username": "testadmin", "password": "testpass123"},
                                     authenticated=False)
        self.assertEqual(status, 200)
    
    def test_login_failure(self):
        """Test: login com senha errada"""
        try:
            self._request("POST", "/api/login",
                         {"username": "testadmin", "password": "wrongpass"},
                         authenticated=False)
            self.fail("Expected HTTP 401")
        except HTTPError as e:
            self.assertEqual(e.code, 401)
    
    def test_get_me(self):
        """Test: GET /api/me retorna usuário autenticado"""
        status, data = self._request("GET", "/api/me", authenticated=True)
        self.assertEqual(status, 200)
        self.assertEqual(data["username"], "testadmin")
        self.assertEqual(data["role"], "admin")
    
    def test_get_runs_empty(self):
        """Test: GET /api/runs lista vazia inicialmente"""
        status, data = self._request("GET", "/api/runs", authenticated=True)
        self.assertEqual(status, 200)
        self.assertEqual(data["runs"], [])
    
    def test_create_run(self):
        """Test: POST /api/runs cria nova run"""
        payload = {
            "log_dir": "/tmp/test-log",
            "target_host": "test.example.com",
            "target_user": "testuser",
            "target_command": "",
            "mode": "strict-global",
            "params": {}
        }
        status, data = self._request("POST", "/api/runs", payload, authenticated=True)
        self.assertEqual(status, 200)
        self.assertIn("id", data)
        self.assertGreater(data["id"], 0)
    
    def test_get_runs_after_create(self):
        """Test: GET /api/runs mostra run criada"""
        # Create
        payload = {
            "log_dir": "/tmp/test-log2",
            "target_host": "test2.example.com",
            "target_user": "testuser2",
            "target_command": "",
            "mode": "strict-global"
        }
        status1, data1 = self._request("POST", "/api/runs", payload, authenticated=True)
        run_id = data1["id"]
        
        # List
        status2, data2 = self._request("GET", "/api/runs", authenticated=True)
        self.assertEqual(status2, 200)
        self.assertGreater(len(data2["runs"]), 0)
        
        # Find our run
        run = next((r for r in data2["runs"] if r["id"] == run_id), None)
        self.assertIsNotNone(run)
        self.assertEqual(run["status"], "queued")


class TestDashboardServer(unittest.TestCase):
    """Tests para dashboard/server.py"""
    
    def test_dashboard_start(self):
        """Test: Dashboard pode ser iniciado"""
        # Note: Isso é mais um smoke test
        # Para testes mais completos, usar Selenium
        pass


if __name__ == "__main__":
    unittest.main(verbosity=2)
```

**Executar:**

```bash
python3 gateway/tests/test_control_server.py -v
```

---

## 4. Testagem com Selenium (Browser Automation)

### 4.1 Instalar Selenium

```bash
pip3 install selenium
pip3 install webdriver-manager  # Gerencia driver automaticamente
```

### 4.2 Criar Test Suite

Criar: `gateway/tests/test_web_ui.py`

```python
#!/usr/bin/env python3
"""
Browser tests para interface web (selenium)
"""
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options as ChromeOptions
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dakota_gateway import auth
from dakota_gateway.control.server import ControlServer, Handler
from dakota_gateway.state_db import connect, init_db, now_ms


class TestWebUI(unittest.TestCase):
    
    @classmethod
    def setUpClass(cls):
        """Setup server e browser driver"""
        cls.tmpdir = tempfile.TemporaryDirectory()
        cls.db_path = f"{cls.tmpdir.name}/test.db"
        cls.cookie_secret = b"test_cookie_secret_32_bytes___"
        cls.hmac_key = b"test_hmac_key_32_bytes__________"
        
        # Init DB
        con = connect(cls.db_path)
        init_db(con)
        username = "testadmin"
        password = "testpass123"
        ph = auth.pbkdf2_hash_password(password)
        con.execute(
            "INSERT INTO users(username,password_hash,role,created_at_ms) VALUES(?,?,'admin',?)",
            (username, ph, now_ms())
        )
        con.close()
        
        # Start server
        cls.server = ControlServer(
            ("127.0.0.1", 0),
            Handler,
            db_path=cls.db_path,
            cookie_secret=cls.cookie_secret,
            hmac_key=cls.hmac_key
        )
        cls.port = cls.server.server_address[1]
        cls.base_url = f"http://127.0.0.1:{cls.port}"
        
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()
        time.sleep(0.5)
        
        # Setup Chrome driver
        options = ChromeOptions()
        options.add_argument("--headless")  # Sem interface
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        cls.driver = webdriver.Chrome(options=options)
    
    @classmethod
    def tearDownClass(cls):
        cls.driver.quit()
        cls.server.shutdown()
        cls.tmpdir.cleanup()
    
    def test_login_page_loads(self):
        """Test: Página de login carrega corretamente"""
        self.driver.get(f"{self.base_url}/login")
        self.assertIn("Login", self.driver.title)
        
        # Verifica elementos
        username_field = self.driver.find_element(By.ID, "u")
        password_field = self.driver.find_element(By.ID, "p")
        self.assertIsNotNone(username_field)
        self.assertIsNotNone(password_field)
    
    def test_login_success(self):
        """Test: Login com credenciais corretas"""
        self.driver.get(f"{self.base_url}/login")
        
        self.driver.find_element(By.ID, "u").send_keys("testadmin")
        self.driver.find_element(By.ID, "p").send_keys("testpass123")
        self.driver.find_element(By.TAG_NAME, "button").click()
        
        # Espera até que seja redirecionado para a dashboard
        WebDriverWait(self.driver, 10).until(
            EC.presence_of_element_located((By.ID, "me"))
        )
        
        # Verifica que está na dashboard
        self.assertIn("dashboard", self.driver.current_url)
    
    def test_dashboard_shows_username(self):
        """Test: Dashboard mostra username do usuário logado"""
        self.driver.get(f"{self.base_url}/login")
        self.driver.find_element(By.ID, "u").send_keys("testadmin")
        self.driver.find_element(By.ID, "p").send_keys("testpass123")
        self.driver.find_element(By.TAG_NAME, "button").click()
        
        WebDriverWait(self.driver, 10).until(
            EC.presence_of_element_located((By.ID, "me"))
        )
        
        me_element = self.driver.find_element(By.ID, "me")
        self.assertIn("testadmin", me_element.text)
    
    def test_create_run_form(self):
        """Test: Formulário de criar run está disponível"""
        self._login()
        
        # Verifica que form fields existem
        log_dir_input = self.driver.find_element(By.ID, "log_dir")
        target_host_input = self.driver.find_element(By.ID, "target_host")
        target_user_input = self.driver.find_element(By.ID, "target_user")
        create_button = self.driver.find_element(By.XPATH, "//button[text()='Criar run']")
        
        self.assertIsNotNone(log_dir_input)
        self.assertIsNotNone(target_host_input)
        self.assertIsNotNone(target_user_input)
        self.assertIsNotNone(create_button)
    
    def test_create_run_scenario(self):
        """Test: Cenário completo de criar uma run"""
        self._login()
        
        # Preenche form
        self.driver.find_element(By.ID, "log_dir").send_keys("/tmp/test-log")
        self.driver.find_element(By.ID, "target_host").send_keys("test.example.com")
        self.driver.find_element(By.ID, "target_user").send_keys("testuser")
        
        # Click criar
        self.driver.find_element(By.XPATH, "//button[text()='Criar run']").click()
        
        # Aguarda a run aparecer na tabela
        WebDriverWait(self.driver, 10).until(
            EC.presence_of_element_located((By.TAG_NAME, "td"))
        )
        
        # Verifica que run aparece
        rows = self.driver.find_elements(By.TAG_NAME, "tr")
        self.assertGreater(len(rows), 1)  # Header + pelo menos 1 row
    
    def _login(self):
        """Helper: Faz login"""
        self.driver.get(f"{self.base_url}/login")
        self.driver.find_element(By.ID, "u").send_keys("testadmin")
        self.driver.find_element(By.ID, "p").send_keys("testpass123")
        self.driver.find_element(By.TAG_NAME, "button").click()
        
        WebDriverWait(self.driver, 10).until(
            EC.presence_of_element_located((By.ID, "me"))
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
```

**Executar:**

```bash
python3 gateway/tests/test_web_ui.py -v
```

---

## 5. Testagem com cURL (API Testing)

### 5.1 Criar Script de Teste

Criar: `tests/test_web_api.sh`

```bash
#!/bin/bash
set -e

BASE_URL="http://localhost:8090"
TMPDIR="/tmp/dakota-api-test"
COOKIES="$TMPDIR/cookies.txt"

mkdir -p "$TMPDIR"

echo "=== Test 1: Login failure (wrong password) ==="
STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
  -X POST "$BASE_URL/api/login" \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"wrongpass"}')
echo "Status: $STATUS (expected 401)"
[[ $STATUS == "401" ]] || exit 1

echo ""
echo "=== Test 2: Login success ==="
STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
  -X POST "$BASE_URL/api/login" \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin123"}' \
  -c "$COOKIES")
echo "Status: $STATUS (expected 200)"
[[ $STATUS == "200" ]] || exit 1

echo ""
echo "=== Test 3: Get current user ==="
RESULT=$(curl -s \
  -X GET "$BASE_URL/api/me" \
  -b "$COOKIES")
echo "Result: $RESULT"
echo "$RESULT" | grep -q "admin" || exit 1

echo ""
echo "=== Test 4: GET /api/runs (empty) ==="
RESULT=$(curl -s \
  -X GET "$BASE_URL/api/runs" \
  -b "$COOKIES")
echo "Result: $RESULT"
echo "$RESULT" | grep -q '"runs"' || exit 1

echo ""
echo "=== Test 5: Create run ==="
RUN_ID=$(curl -s \
  -X POST "$BASE_URL/api/runs" \
  -H "Content-Type: application/json" \
  -b "$COOKIES" \
  -d '{
    "log_dir": "/tmp/test-log",
    "target_host": "test.example.com",
    "target_user": "testuser",
    "mode": "strict-global"
  }' | jq -r '.id')
echo "Created run with ID: $RUN_ID"
[[ $RUN_ID != "null" ]] && [[ $RUN_ID != "" ]] || exit 1

echo ""
echo "=== Test 6: GET /api/runs (should have 1 run) ==="
RUNS_COUNT=$(curl -s \
  -X GET "$BASE_URL/api/runs" \
  -b "$COOKIES" | jq '.runs | length')
echo "Runs count: $RUNS_COUNT (expected >= 1)"
[[ $RUNS_COUNT -ge 1 ]] || exit 1

echo ""
echo "=== Test 7: Start run ==="
STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
  -X POST "$BASE_URL/api/runs/$RUN_ID/start" \
  -b "$COOKIES")
echo "Status: $STATUS (expected 200)"
[[ $STATUS == "200" ]] || exit 1

echo ""
echo "=== Test 8: Get run events ==="
RESULT=$(curl -s \
  -X GET "$BASE_URL/api/runs/$RUN_ID/events" \
  -b "$COOKIES")
echo "Result: $RESULT"
echo "$RESULT" | grep -q '"events"' || exit 1

echo ""
echo "✅ All tests passed!"
```

**Executar:**

```bash
bash tests/test_web_api.sh
```

---

## 6. Testagem do Dashboard

### 6.1 Iniciar com JSONL

**Terminal 1: Engine com logging JSON**

```bash
mkdir -p /tmp/replay2-test
expect bin/main.exp \
  --legacy-cmd "tclsh examples/legacy_sim.tcl" \
  --log-format json \
  --log-stream stdout > /tmp/replay2-test/events.jsonl &
```

**Terminal 2: Dashboard**

```bash
python3 dashboard/server.py \
  --events-file /tmp/replay2-test/events.jsonl \
  --listen 127.0.0.1:8080
```

**Terminal 3: Browser**

```
http://localhost:8080
```

---

## 7. Testagem de Carga

### 7.1 Com Apache Bench

```bash
# Teste simples: 100 requisições, 10 concorrentes
ab -n 100 -c 10 http://localhost:8090/login
```

### 7.2 Com k6 (mais avançado)

Criar: `tests/load_test.js`

```javascript
import http from 'k6/http';
import { check } from 'k6';

export const options = {
  vus: 10,           // 10 virtual users
  duration: '30s',   // 30 segundos
};

export default function () {
  // Test login
  let loginRes = http.post('http://localhost:8090/api/login', JSON.stringify({
    username: 'admin',
    password: 'admin123',
  }), {
    headers: { 'Content-Type': 'application/json' },
  });
  
  check(loginRes, {
    'login status 200': (r) => r.status === 200,
  });
  
  // Get runs
  let runsRes = http.get('http://localhost:8090/api/runs', {
    headers: { 'Content-Type': 'application/json' },
  });
  
  check(runsRes, {
    'get runs status 200': (r) => r.status === 200,
  });
}
```

**Executar:**

```bash
pip3 install k6
k6 run tests/load_test.js
```

---

## 8. Checklist de Testagem Manual

### Antes de Lançar

- [ ] Login page loads
- [ ] Login success (correct password)
- [ ] Login failure (wrong password)
- [ ] Session timeout (leave open > 12h)
- [ ] Logout works
- [ ] Create run form loads
- [ ] Create run with valid params
- [ ] Create run with invalid params
- [ ] List runs shows created run
- [ ] Start run updates status
- [ ] Pause run pauses execution
- [ ] Resume run continues
- [ ] Cancel run stops
- [ ] Retry creates new run
- [ ] View run details (JSON)
- [ ] View run events (JSON)
- [ ] User management (create, delete)
- [ ] RBAC enforcement (operator can't manage users)
- [ ] Responsive design (mobile, tablet, desktop)
- [ ] Error messages clear
- [ ] Performance acceptable (< 1s page load)

---

## 9. CI/CD Integration

### 9.1 GitHub Actions Example

Criar: `.github/workflows/test-web.yml`

```yaml
name: Web Tests

on: [push, pull_request]

jobs:
  test-control-server:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.10'
      - run: pip install -r gateway/requirements.txt
      - run: python3 gateway/tests/test_control_server.py
  
  test-web-ui:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.10'
      - uses: browser-actions/setup-chrome@latest
      - run: pip install selenium webdriver-manager
      - run: pip install -r gateway/requirements.txt
      - run: python3 gateway/tests/test_web_ui.py
```

---

## 10. Próximos Passos

**Criar testes:**

1. [ ] `gateway/tests/test_control_server.py` (API unit tests)
2. [ ] `gateway/tests/test_web_ui.py` (Selenium browser tests)
3. [ ] `tests/test_web_api.sh` (cURL regression tests)
4. [ ] `.github/workflows/test-web.yml` (CI/CD)
5. [ ] `tests/test_web_integration.py` (E2E)

**Ferramentas recomendadas:**

- **Unit/Integration:** Python `unittest` ou `pytest`
- **Browser:** Selenium (Chrome, Firefox)
- **Load:** `k6` ou Apache Bench
- **API:** `curl` ou `requests`
- **Headless:** github-actions/setup-chrome

---

**Status:** Guia completo de testagem web  
**Versão:** 0.1.0  
**Criado:** 27 de março de 2026
