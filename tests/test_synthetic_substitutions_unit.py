#!/usr/bin/env python3
"""Testes do de→para dos dados sintéticos (badge + modal do replay 1-clique).

Cobre:
- ``synthetic_substitutions_payload``: manifest ``de-para.json`` (trilhas
  novas), reconstrução via ``report.json`` + ``dataset.jsonl`` (trilhas
  antigas) com campos-âncora recalculados na KB, log_dir fora da captura,
  trilha sem artefatos e captura inexistente;
- rota ``GET /api/captures/{id}/synthetic-substitutions``.
"""
from __future__ import annotations

import http.cookiejar
import importlib.util
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, build_opener, HTTPCookieProcessor

ROOT = Path(__file__).resolve().parents[1]
GATEWAY_DIR = ROOT / "gateway"
sys.path.insert(0, str(GATEWAY_DIR))

import dakota_gateway.auth as auth
from dakota_gateway.state_db import connect, init_db, now_ms

from control.services.capture_synthesis_service import (
    _build_depara_screens,
    synthetic_substitutions_payload,
)

HMAC_KEY = b"test_hmac_key_synthetic_depara"

CONTROL_SERVER_PATH = GATEWAY_DIR / "control" / "server.py"
SPEC = importlib.util.spec_from_file_location("control_server", CONTROL_SERVER_PATH)
CONTROL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CONTROL)


def _create_user(con, username: str = "admin", role: str = "admin") -> int:
    ph = auth.pbkdf2_hash_password("admin123")
    cur = con.execute(
        "INSERT INTO users(username,password_hash,role,created_at_ms) VALUES(?,?,?,?)",
        (username, ph, role, now_ms()),
    )
    return int(cur.lastrowid)


def _create_capture(con, user_id: int, log_dir: str, capture_id: int | None = None) -> int:
    cols = "(session_uuid,status,created_by,created_by_username,started_at_ms,log_dir)"
    vals = (f"uuid-{capture_id or 'x'}", "finished", user_id, "admin", now_ms(), log_dir)
    if capture_id is None:
        cur = con.execute(f"INSERT INTO capture_sessions{cols} VALUES(?,?,?,?,?,?)", vals)
        return int(cur.lastrowid)
    cur = con.execute(
        f"INSERT INTO capture_sessions(id,session_uuid,status,created_by,created_by_username,started_at_ms,log_dir)"
        " VALUES(?,?,?,?,?,?,?)",
        (capture_id,) + vals,
    )
    return int(cur.lastrowid)


def _screen_mappings() -> list[dict]:
    """Espelho do report.json da captura 13 (arq: cpf/frete/situacao)."""
    return [
        {
            "entity_name": "arq",
            "operation": "read",
            "inputs": [
                {"original": "00109829069", "placeholder": "{{arq.cpf}}", "field_name": "cpf",
                 "method": "by_semantic_type"},
                {"original": "{KEY:ENTER}", "placeholder": None, "field_name": None, "method": "command"},
                {"original": "1", "placeholder": "{{arq.frete}}", "field_name": "frete",
                 "method": "by_cursor_position"},
                {"original": "4", "placeholder": "{{arq.situacao}}", "field_name": "situacao",
                 "method": "by_cursor_position"},
            ],
        },
        {
            "entity_name": None,
            "operation": "",
            "inputs": [
                {"original": "0", "placeholder": None, "field_name": None, "method": ""},
            ],
        },
    ]


_DATASET_ROW = {"cpf": "185.032.574-08", "frete": 104529.05, "situacao": 13, "_entity": "arq"}

# Entidade fake da KB: cpf é identificador de registro → campo-âncora.
_FAKE_ENTITIES = [
    SimpleNamespace(
        name="ARQ",
        indexes=[],
        operations=[],
        fields=[
            SimpleNamespace(name="cpf", datatype="", semantic_type="cpf",
                            unique_flag=False, lookup_table=""),
            SimpleNamespace(name="frete", datatype="decimal", semantic_type="",
                            unique_flag=False, lookup_table=""),
        ],
    )
]


class BuildDeparaScreensTests(unittest.TestCase):
    """Helper puro _build_depara_screens."""

    def test_mantidos_e_substituidos(self):
        screens = _build_depara_screens(_screen_mappings(), _DATASET_ROW, {"cpf"})
        self.assertEqual(len(screens), 1)
        fields = {f["field"]: f for f in screens[0]["fields"]}
        self.assertEqual(screens[0]["entity"], "arq")
        # chave de consulta: mantida com o valor original
        self.assertTrue(fields["cpf"]["kept"])
        self.assertEqual(fields["cpf"]["note"], "chave de consulta")
        self.assertEqual(fields["cpf"]["synthetic"], "00109829069")
        # frete: float → decimal pt-BR com vírgula
        self.assertFalse(fields["frete"]["kept"])
        self.assertEqual(fields["frete"]["synthetic"], "104529,05")
        # situacao: int direto
        self.assertFalse(fields["situacao"]["kept"])
        self.assertEqual(fields["situacao"]["synthetic"], "13")

    def test_valor_igual_ao_original_marcado_como_mantido(self):
        row = dict(_DATASET_ROW, situacao=4)
        screens = _build_depara_screens(_screen_mappings(), row, set())
        fields = {f["field"]: f for f in screens[0]["fields"]}
        self.assertTrue(fields["situacao"]["kept"])
        self.assertEqual(fields["situacao"]["note"], "igual ao original")

    def test_inputs_sem_placeholder_ou_comando_nao_entram(self):
        screens = _build_depara_screens(_screen_mappings(), _DATASET_ROW, set())
        originals = [f["original"] for f in screens[0]["fields"]]
        self.assertNotIn("{KEY:ENTER}", originals)
        self.assertEqual(len(screens[0]["fields"]), 3)

    def test_display_name_vem_do_codigo_de_menu(self):
        """Título gravado com linha de menu ("| 3.6.1 PEDIDO E-COMMERCE")
        vira o nome da tela no lugar do entity_name espúrio ("arq")."""
        mappings = [dict(_screen_mappings()[0], screen_title=(
            " DAKOTA S/A                                 ESTOQUE\n"
            "  REDE DE LOJAS          | 3.6.1 PEDIDO E-COMMERCE\n"
            " Pedido.....:"
        ))]
        screens = _build_depara_screens(mappings, _DATASET_ROW, {"cpf"})
        self.assertEqual(screens[0]["display_name"], "3.6.1 Pedido E-Commerce")
        self.assertEqual(screens[0]["entity"], "arq")

    def test_display_name_cai_para_entidade_sem_titulo(self):
        screens = _build_depara_screens(_screen_mappings(), _DATASET_ROW, {"cpf"})
        self.assertEqual(screens[0]["display_name"], "arq")

    def test_preservados_entram_em_tela_com_substituicoes(self):
        """Inputs de dados sem campo mapeado aparecem como 'mantidos' na
        tela que tem substituições (contabiliza tudo que foi digitado)."""
        mappings = [dict(_screen_mappings()[0])]
        mappings[0]["inputs"] = list(mappings[0]["inputs"]) + [
            {"original": "4", "placeholder": None, "field_name": None,
             "method": "kept_layout_field", "layout_field": "ecommerc"},
            {"original": "9", "placeholder": None, "field_name": None,
             "method": "menu_option_kept"},
        ]
        screens = _build_depara_screens(mappings, _DATASET_ROW, set())
        pres = {p["original"]: p for p in screens[0]["preserved"]}
        self.assertEqual(pres["4"]["field"], "ecommerc")
        self.assertIn("fora da KB", pres["4"]["note"])
        self.assertTrue(pres["9"]["note"].startswith("opção/código"))

    def test_tela_so_com_campo_fora_da_kb_entra(self):
        """Tela sem substituições mas com GET de formulário identificado
        pelo cursor (kept_layout_field) entra no de→para."""
        mappings = [{
            "entity_name": "arq", "operation": "read",
            "screen_title": "  REDE DE LOJAS | 3.6.1 PEDIDO E-COMMERCE",
            "inputs": [
                {"original": "4", "placeholder": None, "field_name": None,
                 "method": "kept_layout_field", "layout_field": "ecommerc"},
            ],
        }]
        screens = _build_depara_screens(mappings, _DATASET_ROW, set())
        self.assertEqual(len(screens), 1)
        self.assertEqual(screens[0]["preserved"][0]["field"], "ecommerc")
        self.assertEqual(screens[0]["display_name"], "3.6.1 Pedido E-Commerce")

    def test_tela_de_menu_sem_campos_nao_entra(self):
        """Tela de navegação (só dígito de opção, sem campo mapeado nem
        kept_layout_field) fica de fora do de→para — evita ruído."""
        screens = _build_depara_screens(_screen_mappings(), _DATASET_ROW, {"cpf"})
        self.assertEqual(len(screens), 1)


class SubstitutionsPayloadTests(unittest.TestCase):
    """synthetic_substitutions_payload: manifest, rebuild e erros."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        base = Path(self.tmpdir.name)
        self.db_path = str(base / "test.db")
        self.con = connect(self.db_path)
        init_db(self.con)
        self.user_id = _create_user(self.con)
        self.log_dir = base / "captures" / "uuid-13"
        self.work_dir = self.log_dir / "synthetic" / "capture-13-replay"
        self.trail_dir = self.work_dir / "trail"
        self.trail_dir.mkdir(parents=True)
        self.capture_id = _create_capture(self.con, self.user_id, str(self.log_dir))

    def tearDown(self):
        self.con.close()
        self.tmpdir.cleanup()

    def _write_synthesis_artifacts(self):
        self.work_dir.joinpath("report.json").write_text(
            json.dumps({"journey_id": "216cc731", "screen_mappings": _screen_mappings()}),
            encoding="utf-8",
        )
        self.work_dir.joinpath("dataset.jsonl").write_text(
            json.dumps(_DATASET_ROW) + "\n", encoding="utf-8"
        )

    def test_manifest_tem_precedencia(self):
        manifest = {
            "capture_id": self.capture_id,
            "journey_id": "abc123",
            "key_fields": ["cpf"],
            "screens": [{"entity": "arq", "operation": "read", "fields": [
                {"field": "cpf", "original": "00109829069", "synthetic": "00109829069",
                 "kept": True, "note": "chave de consulta", "method": "by_semantic_type"},
            ]}],
        }
        self.trail_dir.joinpath("de-para.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
        payload = synthetic_substitutions_payload(
            self.con, self.capture_id, log_dir=str(self.trail_dir)
        )
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["source"], "manifest")
        self.assertEqual(payload["journey_id"], "abc123")
        self.assertEqual(payload["key_fields"], ["cpf"])
        self.assertEqual(payload["screens"], manifest["screens"])

    def test_rebuild_de_report_e_dataset_com_chave_da_kb(self):
        self._write_synthesis_artifacts()
        with mock.patch(
            "dakota_gateway.synthetic.engine.SyntheticEngine.load_entities",
            return_value=_FAKE_ENTITIES,
        ):
            payload = synthetic_substitutions_payload(
                self.con, self.capture_id, log_dir=str(self.trail_dir)
            )
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["source"], "rebuilt")
        self.assertEqual(payload["journey_id"], "216cc731")
        self.assertEqual(payload["key_fields"], ["cpf"])
        fields = {f["field"]: f for f in payload["screens"][0]["fields"]}
        self.assertTrue(fields["cpf"]["kept"])
        self.assertEqual(fields["cpf"]["synthetic"], "00109829069")
        self.assertFalse(fields["frete"]["kept"])
        self.assertEqual(fields["frete"]["synthetic"], "104529,05")

    def test_log_dir_fora_da_captura_rejeitado(self):
        with self.assertRaises(ValueError):
            synthetic_substitutions_payload(
                self.con, self.capture_id, log_dir="/etc"
            )
        with self.assertRaises(ValueError):
            synthetic_substitutions_payload(
                self.con, self.capture_id, log_dir=str(self.log_dir) + "-evil/trail"
            )

    def test_trilha_sem_artefatos_levanta_not_found(self):
        with self.assertRaises(FileNotFoundError):
            synthetic_substitutions_payload(
                self.con, self.capture_id, log_dir=str(self.trail_dir)
            )

    def test_captura_inexistente_levanta_not_found(self):
        with self.assertRaises(FileNotFoundError):
            synthetic_substitutions_payload(
                self.con, 99999, log_dir=str(self.trail_dir)
            )


class SubstitutionsRouteTests(unittest.TestCase):
    """Rota GET /api/captures/{id}/synthetic-substitutions."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        base = Path(self.tmpdir.name)
        self.db_path = str(base / "test.db")
        self.cookie_secret = b"test_cookie_secret_32_bytes___"

        con = connect(self.db_path)
        init_db(con)
        user_id = _create_user(con)
        self.log_dir = base / "captures" / "uuid-13"
        self.trail_dir = self.log_dir / "synthetic" / "capture-13-replay" / "trail"
        self.trail_dir.mkdir(parents=True)
        self.capture_id = _create_capture(con, user_id, str(self.log_dir))
        self.trail_dir.joinpath("de-para.json").write_text(json.dumps({
            "capture_id": self.capture_id,
            "journey_id": "abc123",
            "key_fields": ["cpf"],
            "screens": [{"entity": "arq", "operation": "read", "fields": [
                {"field": "cpf", "original": "00109829069", "synthetic": "00109829069",
                 "kept": True, "note": "chave de consulta", "method": "by_semantic_type"},
            ]}],
        }), encoding="utf-8")
        con.close()

        try:
            self.server = CONTROL.ControlServer(
                ("127.0.0.1", 0),
                CONTROL.Handler,
                db_path=self.db_path,
                cookie_secret=self.cookie_secret,
                hmac_key=HMAC_KEY,
            )
        except PermissionError as exc:
            raise unittest.SkipTest(f"sandbox sem permissao para abrir socket local: {exc}") from exc
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        time.sleep(0.2)

        self.opener = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self._request("POST", "/api/login", {"username": "admin", "password": "admin123"})

    def tearDown(self):
        if hasattr(self, "server"):
            self.server.shutdown()
            self.server.server_close()
        self.tmpdir.cleanup()

    def _request(self, method: str, path: str, data: dict | None = None):
        url = f"http://127.0.0.1:{self.port}{path}"
        body = None if data is None else json.dumps(data).encode("utf-8")
        req = Request(url, data=body, headers={"Content-Type": "application/json"}, method=method)
        try:
            with self.opener.open(req, timeout=5) as resp:
                raw = resp.read().decode("utf-8")
                return resp.status, json.loads(raw) if raw else {}
        except HTTPError as exc:
            raw = exc.read().decode("utf-8")
            return exc.code, json.loads(raw) if raw else {}

    def test_get_retorna_depara_do_manifest(self):
        status, payload = self._request(
            "GET",
            f"/api/captures/{self.capture_id}/synthetic-substitutions?log_dir={quote(str(self.trail_dir))}",
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["source"], "manifest")
        self.assertEqual(payload["key_fields"], ["cpf"])
        fields = payload["screens"][0]["fields"]
        self.assertEqual(fields[0]["field"], "cpf")
        self.assertTrue(fields[0]["kept"])

    def test_get_sem_log_dir_retorna_400(self):
        status, payload = self._request(
            "GET", f"/api/captures/{self.capture_id}/synthetic-substitutions"
        )
        self.assertEqual(status, 400)
        self.assertFalse(payload["ok"])

    def test_get_log_dir_fora_da_captura_retorna_400(self):
        status, payload = self._request(
            "GET",
            f"/api/captures/{self.capture_id}/synthetic-substitutions?log_dir={quote('/etc')}",
        )
        self.assertEqual(status, 400)
        self.assertFalse(payload["ok"])

    def test_get_trilha_inexistente_retorna_404(self):
        vazio = self.log_dir / "synthetic" / "outro" / "trail"
        vazio.mkdir(parents=True)
        status, payload = self._request(
            "GET",
            f"/api/captures/{self.capture_id}/synthetic-substitutions?log_dir={quote(str(vazio))}",
        )
        self.assertEqual(status, 404)
        self.assertFalse(payload["ok"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
