#!/usr/bin/env python3
"""Testes para a API synthetic completa (equivalente CLI → UI)."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATEWAY_DIR = ROOT / "gateway"
sys.path.insert(0, str(GATEWAY_DIR))
# control é módulo irmão de dakota_gateway, em gateway/control/
sys.path.insert(0, str(GATEWAY_DIR / "control"))
# Adicionar gateway/ para que 'control' seja importável
import control  # noqa

from dakota_gateway.state_db import connect, init_db


class SyntheticAPITests(unittest.TestCase):
    """Valida que todos os comandos CLI têm equivalente na API web."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmpdir.name) / "test.db")
        self.source_dir = Path(self.tmpdir.name) / "source"
        self.source_dir.mkdir()
        self.con = connect(self.db_path)
        init_db(self.con)

        # Setup: analisar código-fonte (simulado)
        self._setup_source()
        self._setup_journeys()

    def tearDown(self):
        self.con.close()
        self.tmpdir.cleanup()

    def _setup_source(self):
        """Simula analyze-source."""
        (self.source_dir / "cad_pedido.prg").write_text(
            """TITLE "Pedido de Venda"
@ 01,01 GET COD_CLIENTE
@ 02,01 GET NOME_CLIENTE
@ 03,01 GET CPF_CLIENTE
@ 04,01 GET CEP_ENTREGA
@ 05,01 GET QTD_ITEM
@ 06,01 GET VALOR_TOTAL
""",
            encoding="utf-8",
        )

        from dakota_gateway.synthetic.screen_registry import ScreenRegistry
        reg = ScreenRegistry(self.con)
        sid = reg.register_screen(
            screen_signature="CAD_CLI_TEST",
            title="Cadastro de Clientes",
            program_name="CADCLI",
        )
        from dakota_gateway.synthetic.schema import FieldSchema, ScreenSchema
        reg.register_fields_from_schema(sid, ScreenSchema(
            screen_signature="CAD_CLI_TEST", title="Cadastro de Clientes",
            fields=[
                FieldSchema(name="nome", datatype="person_name", required=True),
                FieldSchema(name="cpf", datatype="cpf", unique=True),
                FieldSchema(name="telefone", datatype="phone"),
            ],
        ))

        # Registrar entidade
        self.con.execute(
            "INSERT INTO source_entities (name, storage_type, source, created_at) VALUES (?,?,?,?)",
            ("CLIENTES", "isam", "test.prg", "2026-01-01"),
        )
        eid = self.con.execute("SELECT last_insert_rowid()").fetchone()[0]
        self.con.execute(
            "INSERT INTO source_entity_fields (entity_id, field_name, datatype) VALUES (?,?,?)",
            (eid, "NOME", "person_name"),
        )

        placeholder_id = reg.register_screen(
            screen_signature="SCR0000",
            title="Tela 0",
            program_name="PROG000",
        )
        self.assertTrue(placeholder_id > 0)

        self.con.execute(
            "INSERT INTO source_entities (name, storage_type, source, created_at) VALUES (?,?,?,?)",
            ("&(cdiresc", "unknown", "/dakota11/prg", "2026-01-01"),
        )
        self.con.commit()

    def _setup_journeys(self):
        """Simula journey infer."""
        from dakota_gateway.synthetic.journey import JourneyDefinition, JourneyStep
        from dakota_gateway.synthetic.journey_builder import JourneyBuilder
        builder = JourneyBuilder(db_connection=self.con)
        builder.save_journey(JourneyDefinition(
            journey_id="test_api_journey", name="API Test Journey", category="test",
            steps=[
                JourneyStep(step_order=0, screen_id="menu", action="navigate"),
                JourneyStep(step_order=1, screen_id="CAD_CLI_TEST", action="input",
                           input_template="{{cliente.nome}}\n{{cliente.cpf}}"),
            ],
            tags=["test"],
        ))

    # ------------------------------------------------------------------
    # CLI: screens → GET /api/synthetic/screens
    # ------------------------------------------------------------------

    def test_list_screens(self):
        from control.routes.synthetic_routes import handle_synthetic_get_route
        handler = _FakeHandler(self.db_path)
        parsed = _FakeParsedPath("/api/synthetic/screens")
        handled = handle_synthetic_get_route(handler, parsed)
        self.assertTrue(handled)
        data = json.loads(handler.wfile.data.decode("utf-8"))
        self.assertIn("screens", data)
        self.assertGreater(len(data["screens"]), 0)

    def test_get_screen_detail(self):
        from control.routes.synthetic_routes import handle_synthetic_get_route
        handler = _FakeHandler(self.db_path)
        parsed = _FakeParsedPath("/api/synthetic/screens/1")
        handled = handle_synthetic_get_route(handler, parsed)
        self.assertTrue(handled)
        data = json.loads(handler.wfile.data.decode("utf-8"))
        self.assertIn("fields", data)

    # ------------------------------------------------------------------
    # CLI: analyze-source → POST /api/synthetic/analyze-source
    # ------------------------------------------------------------------

    def test_analyze_source_endpoint(self):
        from control.routes.synthetic_routes import handle_synthetic_post_route
        handler = _FakeHandler(self.db_path, method="POST", body={"source_dir": "/tmp"})
        parsed = _FakeParsedPath("/api/synthetic/analyze-source")
        handled = handle_synthetic_post_route(handler, parsed)
        self.assertTrue(handled)
        data = json.loads(handler.wfile.data.decode("utf-8"))
        self.assertIn("screens", data)

    # ------------------------------------------------------------------
    # CLI: generate → POST /api/synthetic/generate
    # ------------------------------------------------------------------

    def test_generate_dataset(self):
        from control.routes.synthetic_routes import handle_synthetic_post_route
        handler = _FakeHandler(self.db_path, method="POST", body={
            "screen": "CAD_CLI_TEST", "quantity": 10, "seed": 42,
        })
        parsed = _FakeParsedPath("/api/synthetic/generate")
        handled = handle_synthetic_post_route(handler, parsed)
        self.assertTrue(handled)
        data = json.loads(handler.wfile.data.decode("utf-8"))
        self.assertIn("dataset_id", data)
        self.assertIn("sample", data)

    # ------------------------------------------------------------------
    # CLI: stress → POST /api/synthetic/stress
    # ------------------------------------------------------------------

    def test_run_stress(self):
        from control.routes.synthetic_routes import handle_synthetic_post_route
        handler = _FakeHandler(self.db_path, method="POST", body={
            "scenario": "test_api_journey", "concurrency": 2,
            "max_sessions": 3, "seed": 42,
        })
        parsed = _FakeParsedPath("/api/synthetic/stress")
        handled = handle_synthetic_post_route(handler, parsed)
        self.assertTrue(handled)
        data = json.loads(handler.wfile.data.decode("utf-8"))
        self.assertIn("total_sessions", data)
        self.assertGreaterEqual(data["total_sessions"], 0)

    # ------------------------------------------------------------------
    # CLI: journey list → GET /api/synthetic/journeys
    # ------------------------------------------------------------------

    def test_list_journeys(self):
        from control.routes.synthetic_routes import handle_synthetic_get_route
        handler = _FakeHandler(self.db_path)
        parsed = _FakeParsedPath("/api/synthetic/journeys")
        handled = handle_synthetic_get_route(handler, parsed)
        self.assertTrue(handled)
        data = json.loads(handler.wfile.data.decode("utf-8"))
        self.assertIn("journeys", data)

    # ------------------------------------------------------------------
    # CLI: journey show → GET /api/synthetic/journeys/<id>
    # ------------------------------------------------------------------

    def test_show_journey(self):
        from control.routes.synthetic_routes import handle_synthetic_get_route
        handler = _FakeHandler(self.db_path)
        parsed = _FakeParsedPath("/api/synthetic/journeys/test_api_journey")
        handled = handle_synthetic_get_route(handler, parsed)
        self.assertTrue(handled)
        data = json.loads(handler.wfile.data.decode("utf-8"))
        self.assertEqual(data["name"], "API Test Journey")

    # ------------------------------------------------------------------
    # CLI: journey run → POST /api/synthetic/journeys/<id>/run
    # ------------------------------------------------------------------

    def test_run_journey(self):
        from control.routes.synthetic_routes import handle_synthetic_post_route
        handler = _FakeHandler(self.db_path, method="POST", body={
            "sessions": 3, "seed": 42,
        })
        parsed = _FakeParsedPath("/api/synthetic/journeys/test_api_journey/run")
        handled = handle_synthetic_post_route(handler, parsed)
        self.assertTrue(handled)
        data = json.loads(handler.wfile.data.decode("utf-8"))
        self.assertIn("completed", data)

    # ------------------------------------------------------------------
    # CLI: journey infer-menu → POST /api/synthetic/journeys/infer-menu
    # ------------------------------------------------------------------

    def test_infer_menu_endpoint(self):
        # Criar arquivo de menu temporário
        menu_path = Path(self.tmpdir.name) / "menu.prg"
        menu_path.write_text("""*PROG: Menu Teste
DO WHILE .T.
   @ 10,10 PROMPT "Cadastros"
   @ 11,10 PROMPT "Financeiro"
   MENU TO v_opcao
ENDDO
""")

        from control.routes.synthetic_routes import handle_synthetic_post_route
        handler = _FakeHandler(self.db_path, method="POST", body={
            "menu_file": str(menu_path),
        })
        parsed = _FakeParsedPath("/api/synthetic/journeys/infer-menu")
        handled = handle_synthetic_post_route(handler, parsed)
        self.assertTrue(handled)
        data = json.loads(handler.wfile.data.decode("utf-8"))
        self.assertIn("journey_id", data)

    # ------------------------------------------------------------------
    # Status / Entities / Datasets / Error patterns
    # ------------------------------------------------------------------

    def test_status(self):
        from control.routes.synthetic_routes import handle_synthetic_get_route
        handler = _FakeHandler(self.db_path)
        parsed = _FakeParsedPath("/api/synthetic/status")
        handled = handle_synthetic_get_route(handler, parsed)
        self.assertTrue(handled)
        data = json.loads(handler.wfile.data.decode("utf-8"))
        self.assertGreater(data["screens"], 0)

    def test_list_entities(self):
        from control.routes.synthetic_routes import handle_synthetic_get_route
        handler = _FakeHandler(self.db_path)
        parsed = _FakeParsedPath("/api/synthetic/entities")
        handled = handle_synthetic_get_route(handler, parsed)
        self.assertTrue(handled)
        data = json.loads(handler.wfile.data.decode("utf-8"))
        self.assertIn("entities", data)
        names = [entity["name"] for entity in data["entities"]]
        self.assertIn("CLIENTES", names)
        self.assertNotIn("&(cdiresc", names)

    def test_list_datasets(self):
        from control.routes.synthetic_routes import handle_synthetic_get_route
        handler = _FakeHandler(self.db_path)
        parsed = _FakeParsedPath("/api/synthetic/datasets")
        handled = handle_synthetic_get_route(handler, parsed)
        self.assertTrue(handled)
        data = json.loads(handler.wfile.data.decode("utf-8"))
        self.assertIn("datasets", data)

    def test_error_patterns(self):
        from control.routes.synthetic_routes import handle_synthetic_get_route
        handler = _FakeHandler(self.db_path)
        parsed = _FakeParsedPath("/api/synthetic/error-patterns")
        handled = handle_synthetic_get_route(handler, parsed)
        self.assertTrue(handled)
        data = json.loads(handler.wfile.data.decode("utf-8"))
        self.assertIn("patterns", data)
        self.assertGreater(len(data["patterns"]), 0)

    def test_screen_diff(self):
        from control.routes.synthetic_routes import handle_synthetic_get_route
        handler = _FakeHandler(self.db_path)
        parsed = _FakeParsedPath("/api/synthetic/diff?expected=+--+MENU+--+&observed=+--+ERRO+--+")
        handled = handle_synthetic_get_route(handler, parsed)
        self.assertTrue(handled)
        data = json.loads(handler.wfile.data.decode("utf-8"))
        self.assertIn("similarity", data)

    def test_verify_journey(self):
        from control.routes.synthetic_routes import handle_synthetic_get_route
        handler = _FakeHandler(self.db_path)
        parsed = _FakeParsedPath("/api/synthetic/journeys/test_api_journey/verify?sessions=2&seed=42")
        handled = handle_synthetic_get_route(handler, parsed)
        self.assertTrue(handled)
        data = json.loads(handler.wfile.data.decode("utf-8"))
        self.assertIn("sessions", data)

    def test_list_screens_hides_placeholder_rows(self):
        from control.routes.synthetic_routes import handle_synthetic_get_route
        handler = _FakeHandler(self.db_path)
        parsed = _FakeParsedPath("/api/synthetic/screens")
        handled = handle_synthetic_get_route(handler, parsed)
        self.assertTrue(handled)
        data = json.loads(handler.wfile.data.decode("utf-8"))
        titles = [screen["title"] for screen in data["screens"]]
        self.assertIn("Cadastro de Clientes", titles)
        self.assertNotIn("Tela 0", titles)

    def test_journey_report_json(self):
        from control.routes.synthetic_routes import handle_synthetic_get_route
        handler = _FakeHandler(self.db_path)
        parsed = _FakeParsedPath("/api/synthetic/journeys/test_api_journey/report?sessions=2&seed=42&format=json")
        handled = handle_synthetic_get_route(handler, parsed)
        self.assertTrue(handled)

    def test_generate_dataset_missing_screen(self):
        from control.routes.synthetic_routes import handle_synthetic_post_route
        handler = _FakeHandler(self.db_path, method="POST", body={
            "screen": "INEXISTENTE", "quantity": 5,
        })
        parsed = _FakeParsedPath("/api/synthetic/generate")
        handled = handle_synthetic_post_route(handler, parsed)
        self.assertTrue(handled)
        self.assertEqual(handler.status_code, 404)

    def test_infer_data_plans(self):
        from control.routes.synthetic_routes import handle_synthetic_post_route
        handler = _FakeHandler(self.db_path, method="POST", body={
            "source_dir": str(self.source_dir),
        })
        parsed = _FakeParsedPath("/api/synthetic/data/plans")
        handled = handle_synthetic_post_route(handler, parsed)
        self.assertTrue(handled)
        data = json.loads(handler.wfile.data.decode("utf-8"))
        self.assertIn("plans", data)
        self.assertGreater(len(data["plans"]), 0)
        self.assertIn("screen", data["plans"][0])

    def test_preflight_data_plan(self):
        from control.routes.synthetic_routes import handle_synthetic_post_route
        plan_handler = _FakeHandler(self.db_path, method="POST", body={
            "source_dir": str(self.source_dir),
        })
        handled = handle_synthetic_post_route(
            plan_handler,
            _FakeParsedPath("/api/synthetic/data/plans"),
        )
        self.assertTrue(handled)
        plans = json.loads(plan_handler.wfile.data.decode("utf-8"))["plans"]
        plan_id = plans[0]["plan_id"]

        preflight_handler = _FakeHandler(self.db_path, method="POST", body={
            "source_dir": str(self.source_dir),
            "plan_id": plan_id,
            "sample_size": 3,
            "seed": 7,
        })
        parsed = _FakeParsedPath("/api/synthetic/data/preflight")
        handled = handle_synthetic_post_route(preflight_handler, parsed)
        self.assertTrue(handled)
        data = json.loads(preflight_handler.wfile.data.decode("utf-8"))
        self.assertIn("preflight", data)
        self.assertEqual(data["preflight"]["sample_size"], 3)
        self.assertIn("records", data["preflight"])

    def test_generate_bulk_for_data_plan(self):
        from control.routes.synthetic_routes import handle_synthetic_get_route, handle_synthetic_post_route
        plan_handler = _FakeHandler(self.db_path, method="POST", body={
            "source_dir": str(self.source_dir),
        })
        handled = handle_synthetic_post_route(
            plan_handler,
            _FakeParsedPath("/api/synthetic/data/plans"),
        )
        self.assertTrue(handled)
        plan_id = json.loads(plan_handler.wfile.data.decode("utf-8"))["plans"][0]["plan_id"]

        bulk_handler = _FakeHandler(self.db_path, method="POST", body={
            "source_dir": str(self.source_dir),
            "plan_id": plan_id,
            "quantity": 12,
            "sample_size": 4,
            "seed": 11,
            "strict_preflight": True,
        })
        parsed = _FakeParsedPath("/api/synthetic/data/generate-bulk")
        handled = handle_synthetic_post_route(bulk_handler, parsed)
        self.assertTrue(handled)
        data = json.loads(bulk_handler.wfile.data.decode("utf-8"))
        self.assertIn("preflight", data)
        self.assertFalse(data["blocked"])
        self.assertIn("dataset_id", data)
        self.assertEqual(data["dataset"]["quantity"], 12)

        list_handler = _FakeHandler(self.db_path)
        handled = handle_synthetic_get_route(list_handler, _FakeParsedPath("/api/synthetic/datasets"))
        self.assertTrue(handled)
        listed = json.loads(list_handler.wfile.data.decode("utf-8"))["datasets"]
        self.assertTrue(any(ds["id"] == data["dataset_id"] for ds in listed))

    def test_synthesize_capture_endpoint_generates_artifacts(self):
        from control.routes.capture_routes import handle_capture_post_route

        capture_log_dir = Path(self.tmpdir.name) / "captures" / "cap-001"
        capture_log_dir.mkdir(parents=True)
        events = [
            {
                "type": "checkpoint",
                "session_id": "sess-001",
                "screen_sig": "SIG_CLIENTES",
                "screen_sample": "Cadastro de Clientes",
                "seq_global": 1,
                "norm_len": 400,
            },
            {"type": "bytes", "session_id": "sess-001", "key_text": "123.456.789-09", "seq_global": 2},
            {"type": "bytes", "session_id": "sess-001", "key_text": "JOAO TESTE", "seq_global": 3},
            {"type": "bytes", "session_id": "sess-001", "key_text": "{KEY:F10}", "seq_global": 4},
        ]
        (capture_log_dir / "audit-20260709.part001.jsonl").write_text(
            "\n".join(json.dumps(event) for event in events),
            encoding="utf-8",
        )

        self.con.execute(
            "INSERT INTO users(id, username, password_hash, role, created_at_ms) VALUES (?,?,?,?,?)",
            (1, "admin", "hash", "admin", 1),
        )
        self.con.execute(
            """
            INSERT INTO capture_sessions(
                session_uuid, status, created_by, created_by_username,
                started_at_ms, environment_json, gateway_state_snapshot_json, log_dir, notes
            ) VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (
                "cap-001",
                "finished",
                1,
                "admin",
                123,
                "{}",
                "{}",
                str(capture_log_dir),
                "",
            ),
        )
        self.con.commit()
        capture_id = self.con.execute("SELECT id FROM capture_sessions WHERE session_uuid=?", ("cap-001",)).fetchone()[0]

        handler = _FakeHandler(self.db_path, method="POST", body={
            "source_dir": str(self.source_dir),
            "samples": 3,
            "seed": 42,
            "name": "api_capture_synth",
        })
        parsed = _FakeParsedPath(f"/api/captures/{capture_id}/synthesize")
        handled = handle_capture_post_route(
            handler,
            parsed,
            handler._body,
            now_ms_fn=lambda: 456,
            log_dir_base=str(Path(self.tmpdir.name) / "captures"),
        )

        self.assertTrue(handled)
        self.assertEqual(handler.status_code, 200)
        data = json.loads(handler.wfile.data.decode("utf-8"))
        self.assertTrue(data["ok"])
        self.assertEqual(data["generated_sessions"], 3)
        self.assertIn("artifacts", data)
        self.assertTrue(Path(data["artifacts"]["template"]).exists())
        self.assertTrue(Path(data["artifacts"]["dataset"]).exists())
        self.assertTrue(Path(data["artifacts"]["sessions_dir"]).is_dir())
        self.assertEqual(data["validation"]["total_sessions"], 3)

    def _make_finished_capture(self):
        """Cria captura finalizada com JSONL mínimo; retorna o capture_id."""
        capture_log_dir = Path(self.tmpdir.name) / "captures" / "cap-fallback"
        capture_log_dir.mkdir(parents=True, exist_ok=True)
        (capture_log_dir / "audit-20260709.part001.jsonl").write_text(
            json.dumps({"type": "bytes", "session_id": "sess-001", "key_text": "X", "seq_global": 1}),
            encoding="utf-8",
        )
        self.con.execute(
            "INSERT INTO users(id, username, password_hash, role, created_at_ms) VALUES (?,?,?,?,?)",
            (99, "admin2", "hash", "admin", 1),
        )
        self.con.execute(
            """
            INSERT INTO capture_sessions(
                session_uuid, status, created_by, created_by_username,
                started_at_ms, environment_json, gateway_state_snapshot_json, log_dir, notes
            ) VALUES (?,?,?,?,?,?,?,?,?)
            """,
            ("cap-fallback", "finished", 99, "admin2", 123, "{}", "{}", str(capture_log_dir), ""),
        )
        self.con.commit()
        return self.con.execute(
            "SELECT id FROM capture_sessions WHERE session_uuid=?", ("cap-fallback",)
        ).fetchone()[0]

    def test_synthesize_capture_sem_source_dir_usa_dakota_source_root(self):
        """Sem source_dir no payload, o endpoint usa DAKOTA_SOURCE_ROOT do servidor."""
        from control.routes.capture_routes import handle_capture_post_route

        capture_id = self._make_finished_capture()
        old_root = os.environ.get("DAKOTA_SOURCE_ROOT")
        os.environ["DAKOTA_SOURCE_ROOT"] = str(self.source_dir)
        try:
            handler = _FakeHandler(self.db_path, method="POST", body={"samples": 2, "seed": 42})
            parsed = _FakeParsedPath(f"/api/captures/{capture_id}/synthesize")
            handled = handle_capture_post_route(
                handler, parsed, handler._body,
                now_ms_fn=lambda: 456,
                log_dir_base=str(Path(self.tmpdir.name) / "captures"),
            )
        finally:
            if old_root is None:
                os.environ.pop("DAKOTA_SOURCE_ROOT", None)
            else:
                os.environ["DAKOTA_SOURCE_ROOT"] = old_root

        self.assertTrue(handled)
        self.assertEqual(handler.status_code, 200)
        data = json.loads(handler.wfile.data.decode("utf-8"))
        self.assertTrue(data["ok"])
        self.assertEqual(data["generated_sessions"], 2)

    def test_synthesize_capture_sem_source_dir_e_sem_env_retorna_400(self):
        """Sem source_dir e sem DAKOTA_SOURCE_ROOT, mantém o 400 explicativo."""
        from control.routes.capture_routes import handle_capture_post_route

        capture_id = self._make_finished_capture()
        old_root = os.environ.pop("DAKOTA_SOURCE_ROOT", None)
        try:
            handler = _FakeHandler(self.db_path, method="POST", body={"samples": 2, "seed": 42})
            parsed = _FakeParsedPath(f"/api/captures/{capture_id}/synthesize")
            handled = handle_capture_post_route(
                handler, parsed, handler._body,
                now_ms_fn=lambda: 456,
                log_dir_base=str(Path(self.tmpdir.name) / "captures"),
            )
        finally:
            if old_root is not None:
                os.environ["DAKOTA_SOURCE_ROOT"] = old_root

        self.assertTrue(handled)
        self.assertEqual(handler.status_code, 400)
        data = json.loads(handler.wfile.data.decode("utf-8"))
        self.assertIn("DAKOTA_SOURCE_ROOT", data.get("error", ""))

    def test_synthetic_replay_endpoint_cria_run(self):
        """POST /api/captures/{id}/synthetic-replay — 1 clique: sintetiza e cria a run."""
        from control.routes.capture_routes import handle_capture_post_route

        capture_log_dir = Path(self.tmpdir.name) / "captures" / "cap-replay"
        capture_log_dir.mkdir(parents=True)
        base = {
            "v": "v1",
            "ts_ms": 1000,
            "actor": "tester",
            "session_id": "sess-replay",
            "seq_session": 1,
        }
        events = [
            {**base, "type": "session_start", "seq_global": 1, "logname": "ferblo"},
            {
                **base,
                "type": "checkpoint",
                "screen_sig": "CAD_CLI_TEST",
                "screen_sample": "Cadastro de Clientes",
                "seq_global": 2,
                "norm_len": 400,
            },
            {**base, "type": "bytes", "key_text": "123.456.789-09", "seq_global": 3},
            {**base, "type": "bytes", "key_text": "JOAO TESTE", "seq_global": 4},
            {**base, "type": "bytes", "key_text": "{KEY:F10}", "seq_global": 5},
        ]
        (capture_log_dir / "audit-20260709.part001.jsonl").write_text(
            "\n".join(json.dumps(event) for event in events),
            encoding="utf-8",
        )
        self.con.execute(
            "INSERT INTO users(id, username, password_hash, role, created_at_ms) VALUES (?,?,?,?,?)",
            (1, "admin", "hash", "admin", 1),
        )
        self.con.execute(
            """
            INSERT INTO capture_sessions(
                session_uuid, status, created_by, created_by_username,
                started_at_ms, environment_json, gateway_state_snapshot_json, log_dir, notes
            ) VALUES (?,?,?,?,?,?,?,?,?)
            """,
            ("cap-replay", "finished", 1, "admin", 123, "{}", "{}", str(capture_log_dir), ""),
        )
        self.con.commit()
        capture_id = self.con.execute(
            "SELECT id FROM capture_sessions WHERE session_uuid=?", ("cap-replay",)
        ).fetchone()[0]

        handler = _FakeHandler(self.db_path, method="POST", body={
            "source_dir": str(self.source_dir),
            "seed": 42,
            # wait=1: fluxo síncrono de criação da run (o default da rota
            # passou a ser o job assíncrono — v0.9.3).
            "wait": 1,
        })
        runner = _FakeRunner()
        handler.server = _FakeServer(self.db_path, runner)
        parsed = _FakeParsedPath(f"/api/captures/{capture_id}/synthetic-replay")
        handled = handle_capture_post_route(
            handler,
            parsed,
            handler._body,
            now_ms_fn=lambda: 456,
            log_dir_base=str(Path(self.tmpdir.name) / "captures"),
        )

        self.assertTrue(handled)
        self.assertEqual(handler.status_code, 200)
        data = json.loads(handler.wfile.data.decode("utf-8"))
        self.assertTrue(data["ok"])
        self.assertGreater(data["run_id"], 0)
        self.assertEqual(data["target_user"], "ferblo")
        self.assertEqual(data["target_host"], "127.0.0.1")
        self.assertEqual(runner.started, [data["run_id"]])
        # trilha materializada e íntegra (hash-chain + HMAC do runner fake)
        from dakota_gateway.verifier import verify_log
        verify_log(data["trail_dir"], runner.hmac_key)
        # run registrada com marcador sintético
        row = self.con.execute(
            "SELECT params_json, status FROM replay_runs WHERE id=?", (data["run_id"],)
        ).fetchone()
        params = json.loads(row[0] or "{}")
        self.assertTrue(params.get("synthetic"))
        self.assertEqual(params.get("source_capture_id"), capture_id)
        # default do replay headless: xterm (TERM da captura pode travar a sessão)
        self.assertEqual(params.get("term"), "xterm")
        # rastreabilidade dos campos mantidos (âncoras + skip explícito)
        self.assertIn("key_fields_suggested", data)
        self.assertIn("skip_fields", data)

    def test_synthetic_replay_skip_fields_mantem_originais(self):
        """skip_fields mantém o valor original da captura (identidade posicional)."""
        from control.services.capture_synthesis_service import _extract_substitutions

        mappings = [{
            "screen_title": "Pedido",
            "inputs": [
                {"original": "00109829069", "placeholder": "{{arq.cpf}}", "field_name": "cpf"},
                {"original": "1", "placeholder": "{{arq.frete}}", "field_name": "frete"},
            ],
        }]
        row = {"cpf": "185.032.574-08", "frete": 104529.05}

        subs = _extract_substitutions(mappings, row)
        self.assertEqual(len(subs), 2)
        self.assertNotEqual(subs[0][0], subs[0][1])  # cpf substituído

        subs_skip = _extract_substitutions(mappings, row, skip_fields={"cpf"})
        # cpf vira identidade (preserva posição do cursor) e frete é substituído
        self.assertEqual(subs_skip[0], ("00109829069", "00109829069"))
        self.assertEqual(subs_skip[1][0], "1")
        self.assertNotEqual(subs_skip[1][0], subs_skip[1][1])

    def test_synthetic_replay_sem_source_dir_retorna_400(self):
        from control.routes.capture_routes import handle_capture_post_route

        capture_id = self._make_finished_capture()
        old_root = os.environ.pop("DAKOTA_SOURCE_ROOT", None)
        try:
            handler = _FakeHandler(self.db_path, method="POST", body={"seed": 42})
            parsed = _FakeParsedPath(f"/api/captures/{capture_id}/synthetic-replay")
            handled = handle_capture_post_route(
                handler, parsed, handler._body,
                now_ms_fn=lambda: 456,
                log_dir_base=str(Path(self.tmpdir.name) / "captures"),
            )
        finally:
            if old_root is not None:
                os.environ["DAKOTA_SOURCE_ROOT"] = old_root

        self.assertTrue(handled)
        self.assertEqual(handler.status_code, 400)


# ---------------------------------------------------------------------------
# Fake HTTP handler para testes
# ---------------------------------------------------------------------------

class _FakeParsedPath:
    def __init__(self, full_path: str):
        from urllib.parse import urlparse
        parsed = urlparse(full_path)
        self.path = parsed.path
        self.query = parsed.query


class _FakeHandler:
    def __init__(self, db_path: str, method: str = "GET", body: dict | None = None):
        self._db_path = db_path
        self._method = method
        self._body = body or {}
        self.status_code = 200
        self.response_body = ""
        self.response_headers = {}
        body_bytes = json.dumps(self._body).encode("utf-8") if self._body else b""
        self.headers = {"Content-Length": str(len(body_bytes))}
        self.rfile = _FakeRFile(body_bytes)
        self.wfile = _FakeWFile()

    def _require(self, roles=None):
        return {"id": 1, "username": "admin", "role": "admin"}

    def _db(self):
        con = connect(self._db_path)
        init_db(con)
        return con

    def _db_release(self, con):
        con.close()

    def send_response(self, code: int):
        self.status_code = code

    def send_header(self, key: str, value: str):
        self.response_headers[key] = value

    def end_headers(self):
        pass


class _FakeRunner:
    """Runner mínimo: registra start_run_async e expõe hmac_key."""

    def __init__(self):
        self.hmac_key = b"fake-runner-hmac"
        self.started = []

    def start_run_async(self, run_id: int):
        self.started.append(int(run_id))


class _FakeServer:
    def __init__(self, db_path: str, runner):
        self.db_path = db_path
        self.runner = runner


class _FakeRFile:
    def __init__(self, body_bytes: bytes):
        self._body = body_bytes

    def read(self, length: int) -> bytes:
        return self._body[:length]


class _FakeWFile:
    """Simula wfile para capturar a resposta."""
    def __init__(self):
        self.data = b""

    def write(self, data: bytes):
        self.data += data


if __name__ == "__main__":
    unittest.main()
