"""
Teste de regressao: fundo escuro durante scroll na pagina de replay.

Valida que a estrutura CSS e HTML garantem que o fundo nunca fique
branco/transparente durante a rolagem, mesmo quando o compositor do
browser descarta camadas temporariamente.

O teste DEVE FALHAR antes da correcao e PASSAR depois dela.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.request import HTTPCookieProcessor, Request, build_opener

ROOT = Path(__file__).resolve().parents[2]
GATEWAY_DIR = ROOT / "gateway"
if str(GATEWAY_DIR) not in sys.path:
    sys.path.insert(0, str(GATEWAY_DIR))

import dakota_gateway.auth as auth
from dakota_gateway.state_db import connect, init_db, now_ms

CONTROL_SERVER_PATH = GATEWAY_DIR / "control" / "server.py"
SPEC = importlib.util.spec_from_file_location("control_server", CONTROL_SERVER_PATH)
CONTROL = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(CONTROL)

# Caminhos fixos para os arquivos fonte
BASE_TEMPLATE_PATH = GATEWAY_DIR / "control" / "templates" / "base.html"
CONTROL_CSS_PATH = GATEWAY_DIR / "control" / "static" / "control.css"
TAILWIND_CSS_PATH = GATEWAY_DIR / "control" / "static" / "tailwind.css"


def _read_css_rules(css_content: str, class_name: str) -> list[str]:
    """Extrai todas as regras CSS para uma classe especifica."""
    escaped = re.escape("." + class_name)
    pattern = escaped + r"\{[^}]+\}"
    return re.findall(pattern, css_content)


def _has_property(css_block: str, prop_name: str) -> bool:
    """Verifica se um bloco CSS contem uma propriedade especifica."""
    return bool(re.search(rf"{re.escape(prop_name)}\s*:", css_block))


def _class_present_in_html(html: str, class_name: str) -> bool:
    """Verifica se uma classe esta presente no HTML."""
    pattern = rf'class\s*=\s*"[^"]*\b{re.escape(class_name)}\b[^"]*"'
    return bool(re.search(pattern, html))


class ScrollBackgroundRegressionTests(unittest.TestCase):
    """Testes estruturais que validam as condicoes para fundo estavel durante scroll."""

    @classmethod
    def setUpClass(cls):
        """Carrega os arquivos fonte uma vez para todos os testes."""
        cls.base_html = BASE_TEMPLATE_PATH.read_text(encoding="utf-8")
        cls.control_css = CONTROL_CSS_PATH.read_text(encoding="utf-8")
        cls.tailwind_css = TAILWIND_CSS_PATH.read_text(encoding="utf-8")

    # ── Testes sobre o CSS (control.css) ──────────────────────────

    def test_01_html_has_explicit_dark_background(self):
        """
        Verifica que o elemento html possui background-color escuro explicito.
        Sem ele, o fundo padrao branco do navegador aparece durante scroll.
        """
        html_rules = _read_css_rules(self.control_css, "html")
        if not html_rules:
            # Pode estar definido como elemento sem classe
            # Procura por 'html{' ou 'html {' no CSS
            bare_match = re.search(r"html\s*\{[^}]+\}", self.control_css)
            if bare_match:
                html_rules = [bare_match.group(0)]

        self.assertTrue(len(html_rules) > 0,
                        "html deve ter regra CSS com background-color explicito")

        # Pelo menos uma regra deve ter background-color (nao so gradient)
        has_bg_color = any(
            _has_property(rule, "background-color") or
            _has_property(rule, "background")
            for rule in html_rules
        )
        self.assertTrue(has_bg_color,
                        "html deve ter background-color escuro explicito (ex: #111217)")

        # Verifica que nao eh transparente nem branco
        for rule in html_rules:
            bg_match = re.search(
                r"background(?:-color)?\s*:\s*([^;]+)",
                rule
            )
            if bg_match:
                bg_value = bg_match.group(1).strip().lower()
                self.assertNotIn("transparent", bg_value,
                                 "html background nao pode ser transparent")
                self.assertNotEqual("#fff", bg_value.replace(" ", ""),
                                    "html background nao pode ser branco (#fff)")
                self.assertNotEqual("#ffffff", bg_value.replace(" ", ""),
                                    "html background nao pode ser branco (#ffffff)")

    def test_02_body_has_explicit_dark_background(self):
        """
        Verifica que o elemento body possui background-color escuro explicito.
        O body usa .r2ctl-theme-shell mas se essa camada for descartada,
        precisa de um fallback solido.
        """
        body_rules = _read_css_rules(self.control_css, "body")
        if not body_rules:
            bare_match = re.search(r"body\s*\{[^}]+\}", self.control_css)
            if bare_match:
                body_rules = [bare_match.group(0)]

        self.assertTrue(len(body_rules) > 0,
                        "body deve ter regra CSS com background-color explicito")

        has_bg_color = any(
            _has_property(rule, "background-color") or
            _has_property(rule, "background")
            for rule in body_rules
        )
        self.assertTrue(has_bg_color,
                        "body deve ter background-color escuro explicito")

        for rule in body_rules:
            bg_match = re.search(
                r"background(?:-color)?\s*:\s*([^;]+)",
                rule
            )
            if bg_match:
                bg_value = bg_match.group(1).strip().lower()
                self.assertNotIn("transparent", bg_value,
                                 "body background nao pode ser transparent")
                self.assertNotEqual("#fff", bg_value.replace(" ", ""),
                                    "body background nao pode ser branco")
                self.assertNotEqual("#ffffff", bg_value.replace(" ", ""),
                                    "body background nao pode ser branco")

    def test_03_theme_shell_has_solid_background_fallback(self):
        """
        Verifica que .r2ctl-theme-shell possui um background-color solido
        alem dos gradientes, garantindo que mesmo se a camada de gradiente
        for descartada, o fundo escuro permanece.
        """
        shell_rules = _read_css_rules(self.control_css, "r2ctl-theme-shell")
        self.assertTrue(len(shell_rules) > 0,
                        ".r2ctl-theme-shell deve existir no CSS")

        for rule in shell_rules:
            # Deve conter background-color ou um background com cor solida
            has_solid = (
                _has_property(rule, "background-color") or
                # Verifica se ha uma cor seguida de gradiente (fallback + gradient)
                bool(re.search(r"background\s*:\s*#[0-9a-fA-F]{3,6}\b", rule)) or
                bool(re.search(r"background\s*:\s*rgb\(\d+", rule))
            )
            self.assertTrue(
                has_solid,
                ".r2ctl-theme-shell deve ter background-color solido de fallback "
                "(ex: background-color: #111217)"
            )

    def test_04_shell_panel_has_no_backdrop_filter(self):
        """
        Verifica que .r2ctl-shell-panel NAO usa backdrop-filter nem
        backdrop-blur, pois isso forcaria recalculacao de blur em toda
        a viewport durante cada frame de scroll.
        """
        panel_rules = _read_css_rules(self.control_css, "r2ctl-shell-panel")
        self.assertTrue(len(panel_rules) > 0,
                        ".r2ctl-shell-panel deve existir no CSS")

        for rule in panel_rules:
            self.assertFalse(
                _has_property(rule, "backdrop-filter"),
                ".r2ctl-shell-panel NAO deve ter backdrop-filter "
                "(causa repintura massiva durante scroll)"
            )
            self.assertFalse(
                _has_property(rule, "-webkit-backdrop-filter"),
                ".r2ctl-shell-panel NAO deve ter -webkit-backdrop-filter"
            )

    def test_05_no_blur_on_fullscreen_decorative_elements(self):
        """
        Verifica que os elementos decorativos fixed (.r2ctl-theme-glow-*)
        NAO usam filtros de blur caros (blur-3xl = 96px blur).
        Em vez disso, devem usar gradientes radiais pre-suavizados.
        """
        glow_top_rules = _read_css_rules(self.control_css, "r2ctl-theme-glow-top")
        glow_bottom_rules = _read_css_rules(self.control_css, "r2ctl-theme-glow-bottom")

        for rule in glow_top_rules + glow_bottom_rules:
            self.assertFalse(
                _has_property(rule, "filter"),
                "Elementos glow nao devem usar filter (blur caro). "
                "Use gradientes radiais pre-suavizados."
            )

    def test_06_no_will_change_on_body_or_html(self):
        """
        Verifica que html e body nao usam will-change desnecessario,
        o que forcaria a criacao de camadas de composicao excessivas.
        """
        for selector, content in [("html", self.control_css), ("body", self.control_css)]:
            patterns = [
                rf"{selector}\s*\{{[^}}]*will-change[^}}]*\}}",
            ]
            for pattern in patterns:
                matches = re.findall(pattern, content, re.IGNORECASE)
                self.assertEqual(
                    len(matches), 0,
                    f"{selector} nao deve ter will-change (cria camadas desnecessarias)"
                )

    # ── Testes sobre o template HTML (base.html) ───────────────────

    def test_07_body_has_theme_shell_class(self):
        """O body deve ter a classe r2ctl-theme-shell para o fundo tematico."""
        self.assertTrue(
            _class_present_in_html(self.base_html, "r2ctl-theme-shell"),
            "body deve ter class r2ctl-theme-shell"
        )

    def test_08_shell_panel_has_no_backdrop_blur_class(self):
        """
        O painel principal (r2ctl-shell-panel) NAO deve ter a classe
        backdrop-blur-md do Tailwind, que aplica backdrop-filter: blur(12px).
        """
        self.assertFalse(
            _class_present_in_html(self.base_html, "backdrop-blur-md"),
            "r2ctl-shell-panel NAO deve ter classe backdrop-blur-md"
        )

    def test_09_no_js_scroll_background_manipulation(self):
        """
        Verifica que nao ha JavaScript que adiciona/remove classes de fundo
        durante o evento scroll (gambiarra comum para mascarar o defeito).
        """
        js_dir = GATEWAY_DIR / "control" / "static" / "js"
        suspicious_patterns: list[str] = []

        if js_dir.exists():
            for js_file in js_dir.rglob("*.js"):
                content = js_file.read_text(encoding="utf-8", errors="ignore")
                if re.search(r"scroll.*background|background.*scroll", content, re.IGNORECASE):
                    suspicious_patterns.append(str(js_file.relative_to(GATEWAY_DIR)))

        self.assertEqual(
            len(suspicious_patterns), 0,
            f"JS nao deve manipular background durante scroll. "
            f"Arquivos suspeitos: {suspicious_patterns}"
        )

    def test_10_glow_elements_have_no_blur_classes(self):
        """
        Os elementos decorativos glow nao devem ter classes blur-3xl
        ou similares do Tailwind que aplicam filter: blur().
        """
        # Verifica se ha blur-3xl nos elementos glow no template
        glow_pattern = r'class="[^"]*blur-\w+[^"]*"[^>]*>[\s\n]*</div>[^<]*</div>\s*<main'
        matches = re.findall(glow_pattern, self.base_html)
        # Nao deve haver blur nos elementos dentro do fixed inset-0
        self.assertEqual(
            len(matches), 0,
            "Elementos decorativos fixed nao devem ter classes blur-*"
        )

    def test_11_theme_preserved_visual_identity(self):
        """
        Verifica que a identidade visual foi preservada:
        - gradientes rosa/vermelho
        - cores escuras caracteristicas
        - shell-panel com borda e sombra
        """
        # r2ctl-theme-shell deve manter gradientes
        shell_rules = _read_css_rules(self.control_css, "r2ctl-theme-shell")
        self.assertTrue(len(shell_rules) > 0)

        has_gradient = any(
            "gradient" in rule.lower() for rule in shell_rules
        )
        self.assertTrue(
            has_gradient,
            ".r2ctl-theme-shell deve preservar gradientes (identidade visual)"
        )

        # r2ctl-shell-panel deve manter borda e sombra
        panel_rules = _read_css_rules(self.control_css, "r2ctl-shell-panel")
        self.assertTrue(len(panel_rules) > 0)
        self.assertTrue(
            any(_has_property(r, "border") for r in panel_rules),
            "shell-panel deve manter borda"
        )
        self.assertTrue(
            any(_has_property(r, "box-shadow") for r in panel_rules),
            "shell-panel deve manter box-shadow"
        )


class ScrollBackgroundServerTests(unittest.TestCase):
    """
    Testes que iniciam o servidor de controle e verificam o HTML renderizado
    em tempo real, garantindo que as classes problematicas nao aparecem
    nas paginas servidas.
    """

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmpdir.name) / "test.db")
        self.cookie_secret = b"test_cookie_secret_32_bytes___"
        self.hmac_key = b"test_hmac_key_32_bytes__________"

        con = connect(self.db_path)
        init_db(con)
        ph = auth.pbkdf2_hash_password("admin123")
        con.execute(
            "INSERT INTO users(username,password_hash,role,created_at_ms) VALUES(?,?,'admin',?)",
            ("admin", ph, now_ms()),
        )
        con.close()

        self.server = CONTROL.ControlServer(
            ("127.0.0.1", 0),
            CONTROL.Handler,
            db_path=self.db_path,
            cookie_secret=self.cookie_secret,
            hmac_key=self.hmac_key,
        )
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        time.sleep(0.2)
        self.opener = build_opener(HTTPCookieProcessor())
        self._request("POST", "/api/login", {"username": "admin", "password": "admin123"})

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmpdir.cleanup()

    def _request(self, method: str, path: str, data: dict | None = None):
        url = f"http://127.0.0.1:{self.port}{path}"
        body = None if data is None else json.dumps(data).encode("utf-8")
        req = Request(url, data=body, headers={"Content-Type": "application/json"}, method=method)
        with self.opener.open(req, timeout=5) as resp:
            payload = resp.read().decode("utf-8")
            return resp.status, payload, dict(resp.headers)

    def test_rendered_pages_have_no_backdrop_blur(self):
        """
        Navega por paginas principais e verifica que o HTML renderizado
        nao contem backdrop-blur-md no painel principal.
        """
        pages = ["/", "/runs", "/captures", "/gateway", "/catalog"]

        for page in pages:
            with self.subTest(page=page):
                status, html, _ = self._request("GET", page)
                self.assertEqual(status, 200, f"Pagina {page} deve retornar 200")

                # backdrop-blur-md nao deve estar presente em nenhum elemento section
                # com r2ctl-shell-panel
                pattern = r'<section[^>]*class="[^"]*r2ctl-shell-panel[^"]*backdrop-blur[^"]*"[^>]*>'
                matches = re.findall(pattern, html)
                self.assertEqual(
                    len(matches), 0,
                    f"Pagina {page}: shell-panel NAO deve conter backdrop-blur"
                )

    def test_rendered_body_has_theme_shell(self):
        """Toda pagina renderizada deve ter body com r2ctl-theme-shell."""
        pages = ["/", "/runs", "/gateway"]

        for page in pages:
            with self.subTest(page=page):
                status, html, _ = self._request("GET", page)
                self.assertEqual(status, 200)
                self.assertIn("r2ctl-theme-shell", html,
                              f"Pagina {page}: body deve ter r2ctl-theme-shell")

    def test_login_page_has_no_backdrop_blur_on_shell(self):
        """A pagina de login tambem nao deve ter backdrop-blur no shell panel."""
        url = f"http://127.0.0.1:{self.port}/login"
        req = Request(url, method="GET")
        with build_opener().open(req, timeout=5) as resp:
            html = resp.read().decode("utf-8")

        pattern = r'<div[^>]*class="[^"]*r2ctl-shell-panel[^"]*backdrop-blur[^"]*"[^>]*>'
        matches = re.findall(pattern, html)
        self.assertEqual(len(matches), 0,
                         "Login: shell-panel NAO deve conter backdrop-blur")


if __name__ == "__main__":
    unittest.main()
