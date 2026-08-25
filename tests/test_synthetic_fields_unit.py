#!/usr/bin/env python3
"""Testes do payload de campos da trilha (multi-select "Manter originais").

Cobre ``synthetic_fields_payload``:
- caminho ``report`` (reusa o report.json da síntese mais recente);
- caminho ``computed`` (parametriza a captura na hora, com KB + índices);
- dedupe de campos por tela, exclusão de comandos/{KEY:...} e flag de chave;
- erros: captura inexistente, source_dir inválido no caminho computed;
- rota ``GET /api/captures/{id}/synthetic-fields``.
"""
from __future__ import annotations

import http.cookiejar
import importlib.util
import json
import os
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

from control.services.capture_synthesis_service import synthetic_fields_payload

HMAC_KEY = b"test_hmac_key_synthetic_fields"

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


def _create_capture(con, user_id: int, log_dir: str) -> int:
    cur = con.execute(
        "INSERT INTO capture_sessions(session_uuid,status,created_by,created_by_username,started_at_ms,log_dir)"
        " VALUES(?,?,?,?,?,?)",
        ("uuid-fields", "finished", user_id, "admin", now_ms(), log_dir),
    )
    return int(cur.lastrowid)


def _screen_mappings() -> list[dict]:
    """Espelho do report.json da captura 13 (arq: cpf/frete/situacao)."""
    return [
        {
            "entity_name": "arq",
            "operation": "read",
            "screen_title": "CADASTRO DE CLIENTES",
            "inputs": [
                {"original": "00109829069", "placeholder": "{{arq.cpf}}", "field_name": "cpf",
                 "method": "by_semantic_type"},
                {"original": "{KEY:ENTER}", "placeholder": None, "field_name": None, "method": "command"},
                {"original": "1", "placeholder": "{{arq.frete}}", "field_name": "frete",
                 "method": "by_cursor_position"},
                {"original": "4", "placeholder": "{{arq.situacao}}", "field_name": "situacao",
                 "method": "by_cursor_position"},
                # duplicado (mesmo campo em outra ocorrência) → dedupe
                {"original": "1", "placeholder": "{{arq.frete}}", "field_name": "frete",
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


def _fake_template() -> SimpleNamespace:
    """JourneyTemplate fake com o mesmo shape usado por _screen_mappings_from_template."""
    def inp(original, placeholder, field_name, method):
        return SimpleNamespace(original=original, placeholder=placeholder,
                               field_name=field_name, method=method)
    return SimpleNamespace(steps=[
        SimpleNamespace(
            screen_title="CADASTRO DE CLIENTES",
            screen_signature="sig1",
            entity_name="arq",
            operation="read",
            inputs=[
                inp("00109829069", "{{arq.cpf}}", "cpf", "by_semantic_type"),
                inp("{KEY:ENTER}", None, None, "command"),
                inp("1", "{{arq.frete}}", "frete", "by_cursor_position"),
            ],
        ),
    ])


class SyntheticFieldsPayloadTests(unittest.TestCase):
    """synthetic_fields_payload: caminhos report/computed e erros."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        base = Path(self.tmpdir.name)
        self.db_path = str(base / "test.db")
        self.con = connect(self.db_path)
        init_db(self.con)
        self.user_id = _create_user(self.con)
        self.log_dir = base / "captures" / "uuid-fields"
        self.log_dir.mkdir(parents=True)
        self.source_dir = base / "prg"
        self.source_dir.mkdir()
        self.capture_id = _create_capture(self.con, self.user_id, str(self.log_dir))

    def tearDown(self):
        self.con.close()
        self.tmpdir.cleanup()

    def _write_report(self, name: str = "capture-13-replay"):
        work_dir = self.log_dir / "synthetic" / name
        work_dir.mkdir(parents=True)
        work_dir.joinpath("report.json").write_text(
            json.dumps({"journey_id": "216cc731", "screen_mappings": _screen_mappings()}),
            encoding="utf-8",
        )
        return work_dir

    def test_report_path_agrupa_campos_e_marca_chave(self):
        self._write_report()
        with mock.patch(
            "dakota_gateway.synthetic.engine.SyntheticEngine.load_entities",
            return_value=_FAKE_ENTITIES,
        ):
            payload = synthetic_fields_payload(self.con, self.capture_id, source_dir="")
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["source"], "report")
        self.assertEqual(payload["key_fields"], ["cpf"])
        self.assertEqual(len(payload["screens"]), 1)
        screen = payload["screens"][0]
        self.assertEqual(screen["entity"], "arq")
        self.assertEqual(screen["screen_title"], "CADASTRO DE CLIENTES")
        fields = {f["field"]: f for f in screen["fields"]}
        # comando {KEY:ENTER} e input sem campo ficam de fora; frete deduplicado
        self.assertEqual(sorted(fields), ["cpf", "frete", "situacao"])
        self.assertTrue(fields["cpf"]["key"])
        self.assertFalse(fields["frete"]["key"])
        self.assertEqual(fields["cpf"]["original"], "00109829069")
        self.assertEqual(sorted(payload["fields"]), ["cpf", "frete", "situacao"])

    def test_computed_path_parametriza_quando_nao_ha_report(self):
        self.log_dir.joinpath("audit-000001.jsonl").write_text(
            json.dumps({"type": "session_start", "logname": "ferblo"}) + "\n",
            encoding="utf-8",
        )
        with mock.patch(
            "dakota_gateway.synthetic.engine.SyntheticEngine.load_entities",
            return_value=_FAKE_ENTITIES,
        ), mock.patch(
            "dakota_gateway.synthetic.engine.SyntheticEngine.load_bindings",
            return_value=[],
        ), mock.patch(
            "control.services.capture_synthesis_service.JourneySynthesizer"
        ) as synth_cls:
            synth_cls.return_value.from_capture.return_value = _fake_template()
            payload = synthetic_fields_payload(
                self.con, self.capture_id, source_dir=str(self.source_dir)
            )
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["source"], "computed")
        fields = {f["field"]: f for f in payload["screens"][0]["fields"]}
        self.assertEqual(sorted(fields), ["cpf", "frete"])
        self.assertTrue(fields["cpf"]["key"])

    def test_computed_sem_source_dir_levanta_value_error(self):
        with mock.patch(
            "dakota_gateway.synthetic.engine.SyntheticEngine.load_entities",
            return_value=_FAKE_ENTITIES,
        ):
            with self.assertRaises(ValueError):
                synthetic_fields_payload(self.con, self.capture_id, source_dir="")
            with self.assertRaises(ValueError):
                synthetic_fields_payload(
                    self.con, self.capture_id, source_dir=str(self.log_dir / "nao-existe")
                )

    def test_captura_inexistente_levanta_not_found(self):
        with self.assertRaises(FileNotFoundError):
            synthetic_fields_payload(self.con, 99999, source_dir="")


class SyntheticFieldsRouteTests(unittest.TestCase):
    """Rota GET /api/captures/{id}/synthetic-fields."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        base = Path(self.tmpdir.name)
        self.db_path = str(base / "test.db")
        self.cookie_secret = b"test_cookie_secret_32_bytes___"

        con = connect(self.db_path)
        init_db(con)
        user_id = _create_user(con)
        self.log_dir = base / "captures" / "uuid-fields"
        work_dir = self.log_dir / "synthetic" / "capture-13-replay"
        work_dir.mkdir(parents=True)
        work_dir.joinpath("report.json").write_text(
            json.dumps({"journey_id": "216cc731", "screen_mappings": _screen_mappings()}),
            encoding="utf-8",
        )
        self.capture_id = _create_capture(con, user_id, str(self.log_dir))
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

    def test_get_retorna_campos_do_report(self):
        with mock.patch(
            "dakota_gateway.synthetic.engine.SyntheticEngine.load_entities",
            return_value=_FAKE_ENTITIES,
        ):
            status, payload = self._request(
                "GET", f"/api/captures/{self.capture_id}/synthetic-fields"
            )
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["source"], "report")
        self.assertEqual(payload["key_fields"], ["cpf"])
        names = [f["field"] for f in payload["screens"][0]["fields"]]
        self.assertEqual(sorted(names), ["cpf", "frete", "situacao"])

    def test_get_sem_report_e_sem_source_dir_retorna_400(self):
        # Remove o report: cai no caminho computed, que exige source_dir válido
        report = self.log_dir / "synthetic" / "capture-13-replay" / "report.json"
        report.unlink()
        with mock.patch.dict(os.environ, {"DAKOTA_SOURCE_ROOT": ""}):
            status, payload = self._request(
                "GET", f"/api/captures/{self.capture_id}/synthetic-fields"
            )
        self.assertEqual(status, 400)
        self.assertFalse(payload["ok"])
        self.assertIn("source_dir", payload["error"])

    def test_get_captura_inexistente_retorna_404(self):
        status, payload = self._request("GET", "/api/captures/99999/synthetic-fields")
        self.assertEqual(status, 404)
        self.assertFalse(payload["ok"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
