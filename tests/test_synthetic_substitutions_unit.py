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
    _dataset_lookup,
    _extract_substitutions,
    _first_session_dataset_row,
    _format_synthetic_value,
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

    def test_origem_formulario_e_grade_com_tabela(self):
        """Todo campo sai com origin: "formulario" por default; célula de
        dbedit sai "grade" com a tabela real que alimenta a grade."""
        mappings = [dict(_screen_mappings()[0])]
        mappings[0]["inputs"] = list(mappings[0]["inputs"]) + [
            {"original": "g2511", "placeholder": "{{arq.modelo}}",
             "field_name": "modelo", "method": "by_grid_column",
             "is_grid": True, "grid_source": "est361"},
            {"original": "2", "placeholder": None, "field_name": None,
             "method": "kept_layout_field", "layout_field": "parcelas",
             "is_grid": True, "grid_source": "est366"},
        ]
        row = dict(_DATASET_ROW, modelo="c6182")
        screens = _build_depara_screens(mappings, row, set())
        fields = {f["field"]: f for f in screens[0]["fields"]}
        self.assertEqual(fields["cpf"]["origin"], "formulario")
        self.assertEqual(fields["cpf"]["grid_source"], "")
        self.assertEqual(fields["modelo"]["origin"], "grade")
        self.assertEqual(fields["modelo"]["grid_source"], "est361")
        pres = {p["field"]: p for p in screens[0]["preserved"]}
        self.assertEqual(pres["parcelas"]["origin"], "grade")
        self.assertEqual(pres["parcelas"]["grid_source"], "est366")


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


class TestDatasetRowMultiEntidade(unittest.TestCase):
    """Dataset multi-entidade (tela com grades dbedit de tabelas distintas):
    a "1ª sessão" mescla o 1º registro de CADA entidade, com chave
    prefixada (est361.modelo) e bare — espelha o session_data do
    journey_synthesizer."""

    def _write_dataset(self, path: Path, records: list[dict]):
        path.write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records),
            encoding="utf-8")

    def test_mescla_primeiro_registro_de_cada_entidade(self):
        with tempfile.TemporaryDirectory() as td:
            ds = Path(td) / "dataset.jsonl"
            self._write_dataset(ds, [
                {"_entity": "arq", "pedido": "100", "frete": 5},
                {"_entity": "arq", "pedido": "200", "frete": 9},  # 2ª amostra: fora
                {"_entity": "est361", "modelo": "G2511", "qtd": 3},
                {"_entity": "est366", "parcelas": 2, "valor": 100.5},
            ])
            row = _first_session_dataset_row(ds)
        self.assertEqual(row["arq.pedido"], "100")
        self.assertEqual(row["est361.modelo"], "G2511")
        self.assertEqual(row["est366.parcelas"], 2)
        self.assertEqual(row["modelo"], "G2511")   # bare também presente
        self.assertNotIn("200", row.values())       # só a 1ª amostra
        self.assertNotIn("_entity", row)

    def test_dataset_inexistente_retorna_vazio(self):
        self.assertEqual(_first_session_dataset_row("/nao/existe.jsonl"), {})

    def test_lookup_prefere_chave_da_entidade_do_input(self):
        row = {"arq.codigo": 10, "est366.codigo": 99, "codigo": 10}
        found, val = _dataset_lookup(row, "est366", "codigo")
        self.assertTrue(found)
        self.assertEqual(val, 99)

    def test_lookup_cai_no_bare_sem_prefixo(self):
        row = {"frete": 5}
        found, val = _dataset_lookup(row, "arq", "frete")
        self.assertTrue(found)
        self.assertEqual(val, 5)
        self.assertEqual(_dataset_lookup(row, "arq", "x"), (False, None))

    def test_substitutions_usam_entidade_do_input(self):
        """Mesmo campo em duas entidades: a substituição usa o valor da
        entidade do input (grade), não o bare da entidade da tela."""
        screen_mappings = [{
            "entity_name": "arq",
            "inputs": [
                {"original": "07", "placeholder": "{{arq.codigo}}",
                 "field_name": "codigo", "entity_name": "arq",
                 "method": "by_cursor_position"},
                {"original": "03", "placeholder": "{{est366.codigo}}",
                 "field_name": "codigo", "entity_name": "est366",
                 "method": "by_grid_source", "is_grid": True,
                 "grid_source": "est366"},
            ],
        }]
        dataset_row = {"arq.codigo": 70, "est366.codigo": 30, "codigo": 70}
        subs = _extract_substitutions(screen_mappings, dataset_row)
        self.assertIn(("07", "70"), subs)
        self.assertIn(("03", "30"), subs)

    def test_depara_usa_entidade_do_input(self):
        screen_mappings = [{
            "entity_name": "arq", "operation": "read",
            "inputs": [
                {"original": "03", "placeholder": "{{est366.codigo}}",
                 "field_name": "codigo", "entity_name": "est366",
                 "method": "by_grid_source", "is_grid": True,
                 "grid_source": "est366"},
            ],
        }]
        dataset_row = {"arq.codigo": 70, "est366.codigo": 30, "codigo": 70}
        screens = _build_depara_screens(screen_mappings, dataset_row, set())
        self.assertEqual(screens[0]["fields"][0]["synthetic"], "30")


class TestFormatSyntheticValueDecimals(unittest.TestCase):
    """Float sintético preserva o Nº de casas decimais do original.

    Regressão da run 40 (captura 62): o valor da grade de pagamento foi gerado
    como "763,05" (2 casas) sobre o original "229,9" (1 casa). O GET do
    Recital com PICTURE de 2 casas NÃO comita "229,9" no ENTER (falta o último
    dígito — a grade fica pendente até o "+" forçar), mas comita "763,05"
    direto — a grade fechou um ESC antes, a sessão saiu da tela do pedido no
    meio da sequência de ESCs e o pedido nunca foi finalizado."""

    def test_preserva_uma_casa_decimal_do_original(self):
        self.assertEqual(_format_synthetic_value("229,9", 763.05), "763,0")

    def test_preserva_duas_casas_decimais(self):
        self.assertEqual(_format_synthetic_value("10,50", 763.05), "763,05")

    def test_original_sem_separador_mantem_duas_casas(self):
        # Defensivo: float sem decimal no original (ex.: campo money digitado
        # como inteiro) conserva o comportamento anterior (2 casas).
        self.assertEqual(_format_synthetic_value("15", 763.05), "763,05")

    def test_substituicao_decimal_segue_shape_do_original(self):
        screen_mappings = [{
            "entity_name": "arq",
            "inputs": [
                {"original": "229,9", "placeholder": "{{est366.valor}}",
                 "field_name": "valor", "entity_name": "est366",
                 "method": "by_grid_column", "is_grid": True,
                 "grid_source": "est366"},
            ],
        }]
        dataset_row = {"est366.valor": 763.05, "valor": 763.05}
        subs = _extract_substitutions(screen_mappings, dataset_row)
        self.assertIn(("229,9", "763,0"), subs)


class TestPaymentTotalOverrides(unittest.TestCase):
    """Valor da grade de pagamento é igualado ao total sintético do pedido.

    Regressão da run 41 (captura 62): o pedido só grava quando a soma da
    grade de pagamento (est366) é igual ao total do pedido — pagamento
    parcial ("229,9" com qtd 2 = total 459,80) dispara o aviso "Valor do
    pedido difere..." e a sequência de ESCs abandona a tela sem gravar; a
    captura só persistiu no 2º passe (pagamento "459,8" = total), mas a run
    saiu do ERP no fim do 1º passe e nunca chegou lá. Com qtd 2→7, o total
    sintético é 459,8 × 3,5 = 1609,3 — todos os pagamentos viram "1609,3",
    confirmando a inclusão já no 1º passe.
    """

    @staticmethod
    def _mappings():
        return [{
            "entity_name": "arq",
            "inputs": [
                {"original": "2", "placeholder": "{{est361.qtd}}",
                 "field_name": "qtd", "entity_name": "est361",
                 "method": "by_grid_source", "is_grid": True,
                 "grid_source": "est361"},
                {"original": "229,9", "placeholder": "{{est366.valor}}",
                 "field_name": "valor", "entity_name": "est366",
                 "method": "by_grid_source", "is_grid": True,
                 "grid_source": "est366"},
            ],
        }, {
            "entity_name": "arq",
            "inputs": [
                {"original": "2", "placeholder": "{{est361.qtd}}",
                 "field_name": "qtd", "entity_name": "est361",
                 "method": "by_grid_source", "is_grid": True,
                 "grid_source": "est361"},
                {"original": "459,8", "placeholder": "{{est366.valor}}",
                 "field_name": "valor", "entity_name": "est366",
                 "method": "by_grid_source", "is_grid": True,
                 "grid_source": "est366"},
            ],
        }]

    def test_pagamentos_viram_total_sintetico(self):
        row = {"est361.qtd": 7, "est366.valor": 763.05, "qtd": 7, "valor": 763.05}
        subs = _extract_substitutions(self._mappings(), row)
        self.assertIn(("2", "7"), subs)
        self.assertIn(("229,9", "1609,3"), subs)
        self.assertIn(("459,8", "1609,3"), subs)
        self.assertNotIn(("229,9", "763,0"), subs)

    def test_depara_marca_ajuste_ao_total(self):
        row = {"est361.qtd": 7, "est366.valor": 763.05, "qtd": 7, "valor": 763.05}
        screens = _build_depara_screens(self._mappings(), row, set())
        valores = [
            f for s in screens for f in s["fields"] if f["field"] == "valor"
        ]
        self.assertEqual(len(valores), 2)
        for f in valores:
            self.assertEqual(f["synthetic"], "1609,3")
            self.assertEqual(f["note"], "ajustado ao total do pedido")

    def test_sem_qtd_nao_ajusta(self):
        mappings = [self._mappings()[1]]  # só a tela do pagamento
        row = {"est366.valor": 763.05, "valor": 763.05}
        subs = _extract_substitutions(mappings, row)
        self.assertIn(("459,8", "763,0"), subs)

    def test_qtd_igual_mantem_total_original(self):
        row = {"est361.qtd": 2, "est366.valor": 459.8, "qtd": 2, "valor": 459.8}
        subs = _extract_substitutions(self._mappings(), row)
        # qtd 2→2 (ratio 1): total = 459,8 — o 1º passe (229,9) também vira
        # o total e confirma a inclusão sem aviso.
        self.assertIn(("229,9", "459,8"), subs)
        self.assertNotIn(("459,8", "459,8"), subs)  # já igual: sem par

    def test_valor_em_skip_nao_ajusta(self):
        row = {"est361.qtd": 7, "est366.valor": 763.05, "qtd": 7, "valor": 763.05}
        subs = _extract_substitutions(self._mappings(), row, skip_fields={"valor"})
        self.assertIn(("229,9", "229,9"), subs)
        self.assertIn(("459,8", "459,8"), subs)

    def test_valor_de_formulario_nao_ajusta(self):
        mappings = [{
            "entity_name": "arq",
            "inputs": [
                {"original": "2", "placeholder": "{{est361.qtd}}",
                 "field_name": "qtd", "entity_name": "est361",
                 "method": "by_grid_source", "is_grid": True,
                 "grid_source": "est361"},
                {"original": "10,0", "placeholder": "{{arq.valor}}",
                 "field_name": "valor", "entity_name": "arq",
                 "method": "by_cursor_position"},
            ],
        }]
        row = {"est361.qtd": 7, "arq.valor": 42.5, "qtd": 7, "valor": 42.5}
        subs = _extract_substitutions(mappings, row)
        self.assertIn(("10,0", "42,5"), subs)


if __name__ == "__main__":
    unittest.main(verbosity=2)
