import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GATEWAY_ROOT = ROOT / "gateway"
if str(GATEWAY_ROOT) not in sys.path:
    sys.path.insert(0, str(GATEWAY_ROOT))


def _load_ui_templates_module():
    module_path = ROOT / "gateway/control/ui_templates.py"
    spec = importlib.util.spec_from_file_location("ui_templates_module", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


UI_TEMPLATES = _load_ui_templates_module()


class UiTemplatesUnitTests(unittest.TestCase):
    def test_templates_directory_contains_expected_files(self):
        templates_dir = ROOT / "gateway/control/templates"
        self.assertTrue((templates_dir / "index.html").is_file())
        self.assertTrue((templates_dir / "base.html").is_file())
        self.assertTrue((templates_dir / "dashboard.html").is_file())
        self.assertTrue((templates_dir / "runs.html").is_file())
        self.assertTrue((templates_dir / "run_new.html").is_file())
        self.assertTrue((templates_dir / "run_detail.html").is_file())
        self.assertTrue((templates_dir / "gateway.html").is_file())
        self.assertTrue((templates_dir / "catalog.html").is_file())
        self.assertTrue((templates_dir / "login.html").is_file())
        self.assertTrue((templates_dir / "observability.html").is_file())
        self.assertTrue((templates_dir / "admin.html").is_file())

    def test_loaded_templates_have_expected_markers(self):
        dashboard = UI_TEMPLATES.render_page(
            "dashboard.html",
            title="t",
            page_title="Painel Operacional",
            page_description="desc",
            page_kicker="kicker",
            active_menu="dashboard",
            page_scripts=["/assets/js/pages/dashboard.js"],
        )
        observability = UI_TEMPLATES.render_page(
            "observability.html",
            title="obs",
            page_title="Observabilidade",
            page_description="desc",
            page_kicker="kicker",
            active_menu="observability",
            page_scripts=["/assets/js/pages/observability.js"],
        )

        self.assertTrue(dashboard.startswith("<!doctype html>"))
        self.assertIn("Painel Operacional", dashboard)
        self.assertIn("Dashboard", dashboard)
        self.assertTrue(UI_TEMPLATES.LOGIN_HTML.startswith("<!doctype html>"))
        self.assertIn("loginForm", UI_TEMPLATES.LOGIN_HTML)
        self.assertIn("/assets/js/pages/login.js", UI_TEMPLATES.LOGIN_HTML)
        self.assertTrue(observability.startswith("<!doctype html>"))
        self.assertIn("Observabilidade", observability)

    def test_menu_uses_real_paths_without_anchor_or_view_query(self):
        for item in UI_TEMPLATES.get_menu_config():
            for child in item.get("children", []):
                href = child.get("href", "")
                self.assertNotIn("#", href)
                self.assertNotIn("?view=", href)

    def test_legacy_index_template_is_marked(self):
        content = UI_TEMPLATES._load_template("index.html", use_cache=False)
        self.assertIn("LEGACY_TEMPLATE", content)

    def test_loader_reads_exact_file_contents(self):
        templates_dir = ROOT / "gateway/control/templates"
        self.assertEqual(
            UI_TEMPLATES._load_template("login.html"),
            (templates_dir / "login.html").read_text(encoding="utf-8"),
        )


if __name__ == "__main__":
    unittest.main()
