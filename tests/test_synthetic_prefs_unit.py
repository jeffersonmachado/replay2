#!/usr/bin/env python3
"""Testes do serviço de preferências/aprendizados da síntese por captura.

Cobre ``control.services.synthetic_prefs_service``:
- load/save de preferências (coluna ``capture_sessions.synthetic_prefs``);
- merge de ``skip_fields``: body presente (mesmo vazio) = replace explícito;
  body ausente = usa o armazenado; effective sempre inclui os campos-âncora;
- cache do ``entry_point`` versionado pela VERSION do código;
- metadados da KB do fonte (``source_kb_meta``): warning por diretório
  diferente e por fingerprint (nº de .prg / mtime máximo) alterado;
- feedback loop: sugestão de campo para ``skip_fields`` a partir das falhas
  de validação das runs sintéticas anteriores da captura.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATEWAY_DIR = ROOT / "gateway"
sys.path.insert(0, str(GATEWAY_DIR))

from dakota_gateway.state_db import connect, init_db, now_ms

from control.services import synthetic_prefs_service as prefs_svc


def _create_user(con, username: str = "admin") -> int:
    cur = con.execute(
        "INSERT INTO users(username,password_hash,role,created_at_ms) VALUES(?,?,?,?)",
        (username, "x", "admin", now_ms()),
    )
    return int(cur.lastrowid)


def _create_capture(con, user_id: int, log_dir: str) -> int:
    cur = con.execute(
        "INSERT INTO capture_sessions(session_uuid,status,created_by,created_by_username,started_at_ms,log_dir)"
        " VALUES(?,?,?,?,?,?)",
        (f"uuid-{Path(log_dir).name}", "finished", user_id, "admin", now_ms(), log_dir),
    )
    return int(cur.lastrowid)


def _create_synthetic_run(con, capture_id: int, log_dir: str,
                          extra_params: dict | None = None) -> int:
    params = {"synthetic": True, "source_capture_id": capture_id}
    params.update(extra_params or {})
    cur = con.execute(
        "INSERT INTO replay_runs(log_dir,target_host,target_user,target_command,mode,params_json,run_fingerprint,status,created_at_ms,created_by)"
        " VALUES(?,?,?,?,?,?,?,?,?,?)",
        (
            log_dir, "127.0.0.1", "ferblo", "", "strict-global",
            json.dumps(params),
            f"fp-{log_dir}-{now_ms()}-{capture_id}-{len(json.dumps(params))}", "success", now_ms(), 1,
        ),
    )
    return int(cur.lastrowid)


def _add_failure(con, run_id: int, seq_global: int, observed_screen: str,
                 failure_type: str = "screen_divergence") -> None:
    con.execute(
        "INSERT INTO replay_failures(run_id,ts_ms,session_id,seq_global,seq_session,event_type,"
        "failure_type,severity,expected_value,observed_value,message,evidence_json)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            run_id, now_ms(), "sess-1", seq_global, seq_global, "deterministic_input",
            failure_type, "medium", "sig-esperada", "sig-observada",
            "checkpoint não estabilizou",
            json.dumps({"observed_screen": observed_screen}),
        ),
    )


def _det_event(seq: int, key: str) -> dict:
    return {
        "type": "deterministic_input",
        "seq_global": seq,
        "session_id": "sess-1",
        "key_b64": base64.b64encode(key.encode("utf-8")).decode("ascii"),
    }


class PrefsStorageTests(unittest.TestCase):
    """load_prefs/save_prefs na coluna synthetic_prefs."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        base = Path(self.tmpdir.name)
        self.con = connect(str(base / "test.db"))
        init_db(self.con)
        self.user_id = _create_user(self.con)
        self.capture_id = _create_capture(self.con, self.user_id, str(base / "cap1"))

    def tearDown(self):
        self.con.close()
        self.tmpdir.cleanup()

    def test_load_sem_prefs_retorna_vazio(self):
        self.assertEqual(prefs_svc.load_prefs(self.con, self.capture_id), {})

    def test_save_e_load_roundtrip(self):
        saved = prefs_svc.save_prefs(self.con, self.capture_id, {"skip_fields": ["cpf", "modelo"]})
        self.assertEqual(saved["skip_fields"], ["cpf", "modelo"])
        self.assertIn("updated_at_ms", saved)
        loaded = prefs_svc.load_prefs(self.con, self.capture_id)
        self.assertEqual(loaded["skip_fields"], ["cpf", "modelo"])

    def test_save_preserva_chaves_existentes(self):
        prefs_svc.save_prefs(self.con, self.capture_id, {"entry_point": {"start_seq": 42}})
        prefs_svc.save_prefs(self.con, self.capture_id, {"skip_fields": ["cpf"]})
        loaded = prefs_svc.load_prefs(self.con, self.capture_id)
        self.assertEqual(loaded["entry_point"], {"start_seq": 42})
        self.assertEqual(loaded["skip_fields"], ["cpf"])

    def test_json_corrompido_retorna_vazio(self):
        self.con.execute(
            "UPDATE capture_sessions SET synthetic_prefs='{lixo' WHERE id=?",
            (self.capture_id,),
        )
        self.assertEqual(prefs_svc.load_prefs(self.con, self.capture_id), {})


class ResolveSkipFieldsTests(unittest.TestCase):
    """Semântica do merge: body presente = replace; ausente = usa stored."""

    def test_body_presente_substitui_o_armazenado(self):
        result = prefs_svc.resolve_skip_fields(
            {"skip_fields": ["cpf", "frete"]}, ["modelo"], ["chave_auto"]
        )
        self.assertEqual(result["stored"], ["modelo"])
        self.assertTrue(result["persist"])
        self.assertEqual(result["effective"], ["chave_auto", "modelo"])

    def test_body_vazio_limpa_o_armazenado(self):
        result = prefs_svc.resolve_skip_fields({"skip_fields": ["cpf"]}, [], ["cpf"])
        self.assertEqual(result["stored"], [])
        self.assertTrue(result["persist"])
        self.assertEqual(result["effective"], ["cpf"])

    def test_body_ausente_usa_o_armazenado_sem_persistir(self):
        result = prefs_svc.resolve_skip_fields(
            {"skip_fields": ["cpf"]}, None, ["chave_auto"]
        )
        self.assertEqual(result["stored"], ["cpf"])
        self.assertFalse(result["persist"])
        self.assertEqual(result["effective"], ["chave_auto", "cpf"])

    def test_body_ausente_sem_nada_armazenado(self):
        result = prefs_svc.resolve_skip_fields({}, None, ["cpf"])
        self.assertEqual(result["stored"], [])
        self.assertFalse(result["persist"])
        self.assertEqual(result["effective"], ["cpf"])

    def test_duplicados_e_bracos_normalizados(self):
        result = prefs_svc.resolve_skip_fields({}, ["  CPF ", "cpf", ""], [])
        self.assertEqual(result["stored"], ["CPF"])


class EntryPointCacheTests(unittest.TestCase):
    """Cache do entry_point versionado pela VERSION do código."""

    def test_cache_valido_na_mesma_versao(self):
        version = prefs_svc.current_version()
        prefs = {"entry_point": {"start_seq": 42, "preamble": []},
                 "entry_point_version": version}
        cached, entry = prefs_svc.entry_point_from_prefs(prefs)
        self.assertTrue(cached)
        self.assertEqual(entry["start_seq"], 42)

    def test_cache_de_outra_versao_invalida(self):
        prefs = {"entry_point": {"start_seq": 42}, "entry_point_version": "0.0.1"}
        cached, entry = prefs_svc.entry_point_from_prefs(prefs)
        self.assertFalse(cached)
        self.assertIsNone(entry)

    def test_sem_cache(self):
        cached, entry = prefs_svc.entry_point_from_prefs({})
        self.assertFalse(cached)
        self.assertIsNone(entry)

    def test_cache_de_ausencia_de_preambulo(self):
        # Detecção que não achou preâmbulo (entry=None) também é cacheada.
        version = prefs_svc.current_version()
        prefs = {"entry_point": None, "entry_point_version": version}
        cached, entry = prefs_svc.entry_point_from_prefs(prefs)
        self.assertTrue(cached)
        self.assertIsNone(entry)

    def test_current_version_le_arquivo_version(self):
        esperado = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        self.assertEqual(prefs_svc.current_version(), esperado)


class KbMetaTests(unittest.TestCase):
    """source_kb_meta: stamp do analyze-source e aviso de KB desatualizada."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        base = Path(self.tmpdir.name)
        self.con = connect(str(base / "test.db"))
        init_db(self.con)
        self.source_dir = base / "prg"
        self.source_dir.mkdir()
        (self.source_dir / "est361.prg").write_text("@ 1,1 say 'x'", encoding="utf-8")
        sub = self.source_dir / "est"
        sub.mkdir()
        (sub / "est330.prg").write_text("numrot = \"3.3\"", encoding="utf-8")

    def tearDown(self):
        self.con.close()
        self.tmpdir.cleanup()

    def test_save_e_load_meta(self):
        meta = prefs_svc.save_kb_meta(self.con, str(self.source_dir))
        self.assertEqual(meta["source_dir"], str(self.source_dir.resolve()))
        self.assertEqual(meta["file_count"], 2)
        self.assertGreater(meta["max_mtime_ms"], 0)
        self.assertGreater(meta["analyzed_at_ms"], 0)
        loaded = prefs_svc.load_kb_meta(self.con)
        self.assertEqual(loaded["file_count"], 2)

    def test_fingerprint_conta_apenas_prg_recursivo(self):
        (self.source_dir / "leia.txt").write_text("não conta", encoding="utf-8")
        fp = prefs_svc.fingerprint_source_dir(str(self.source_dir))
        self.assertEqual(fp["file_count"], 2)

    def test_status_sem_meta_nao_avisa(self):
        status = prefs_svc.kb_status(self.con, str(self.source_dir))
        self.assertFalse(status["stale"])
        self.assertIsNone(status["analyzed_at_ms"])
        self.assertEqual(status["warning"], "")

    def test_status_mesmo_dir_e_fingerprint_nao_avisa(self):
        prefs_svc.save_kb_meta(self.con, str(self.source_dir))
        status = prefs_svc.kb_status(self.con, str(self.source_dir))
        self.assertFalse(status["stale"])
        self.assertEqual(status["warning"], "")
        self.assertEqual(status["stored_source_dir"], str(self.source_dir.resolve()))

    def test_status_dir_diferente_avisa(self):
        prefs_svc.save_kb_meta(self.con, str(self.source_dir))
        outro = Path(self.tmpdir.name) / "outro"
        outro.mkdir()
        (outro / "est361.prg").write_text("x", encoding="utf-8")
        status = prefs_svc.kb_status(self.con, str(outro))
        self.assertTrue(status["stale"])
        self.assertIn("outro diretório", status["warning"])

    def test_status_fingerprint_mudou_avisa(self):
        prefs_svc.save_kb_meta(self.con, str(self.source_dir))
        novo = self.source_dir / "est" / "est999.prg"
        novo.write_text("novo fonte", encoding="utf-8")
        os.utime(novo, (time.time() + 5, time.time() + 5))
        status = prefs_svc.kb_status(self.con, str(self.source_dir))
        self.assertTrue(status["stale"])
        self.assertIn("desatualizada", status["warning"])


class FeedbackTests(unittest.TestCase):
    """Feedback loop: falha de validação → campo sugerido para skip_fields."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        base = Path(self.tmpdir.name)
        self.con = connect(str(base / "test.db"))
        init_db(self.con)
        self.user_id = _create_user(self.con)
        self.log_dir = base / "captures" / "cap1"
        self.log_dir.mkdir(parents=True)
        self.capture_id = _create_capture(self.con, self.user_id, str(self.log_dir))
        self.trail_dir = self.log_dir / "synthetic" / "capture-1-replay" / "trail"
        self.trail_dir.mkdir(parents=True)

    def tearDown(self):
        self.con.close()
        self.tmpdir.cleanup()

    def _write_depara(self, applied: list[dict] | None = None,
                      screens: list[dict] | None = None) -> None:
        data: dict = {"capture_id": self.capture_id, "journey_id": "j1",
                      "screens": screens or []}
        if applied is not None:
            data["applied"] = applied
        self.trail_dir.joinpath("de-para.json").write_text(
            json.dumps(data), encoding="utf-8")

    def test_sugere_campo_da_substituicao_antes_da_falha(self):
        self._write_depara(applied=[
            {"field": "modelo", "original": "g2511", "synthetic": "zz999",
             "seq_start": 20, "seq_end": 24},
            {"field": "qtd", "original": "2", "synthetic": "7",
             "seq_start": 30, "seq_end": 30},
        ])
        run_id = _create_synthetic_run(self.con, self.capture_id, str(self.trail_dir))
        _add_failure(self.con, run_id, 40, "Codigo nao cadastrado")
        sugestoes = prefs_svc.feedback_for_capture(self.con, self.capture_id)
        self.assertEqual(len(sugestoes), 1)
        self.assertEqual(sugestoes[0]["field"], "qtd")
        self.assertEqual(sugestoes[0]["run_id"], run_id)
        self.assertEqual(sugestoes[0]["failure_seq"], 40)

    def test_falha_benigna_nao_sugere(self):
        self._write_depara(applied=[
            {"field": "modelo", "original": "g2511", "synthetic": "zz999",
             "seq_start": 20, "seq_end": 24},
        ])
        run_id = _create_synthetic_run(self.con, self.capture_id, str(self.trail_dir))
        _add_failure(self.con, run_id, 40, "arq1234.pcp nao encontrado")
        self.assertEqual(prefs_svc.feedback_for_capture(self.con, self.capture_id), [])

    def test_falha_nao_divergencia_ignorada(self):
        self._write_depara(applied=[
            {"field": "modelo", "original": "g2511", "synthetic": "zz999",
             "seq_start": 20, "seq_end": 24},
        ])
        run_id = _create_synthetic_run(self.con, self.capture_id, str(self.trail_dir))
        _add_failure(self.con, run_id, 40, "Codigo nao cadastrado",
                     failure_type="timeout")
        self.assertEqual(prefs_svc.feedback_for_capture(self.con, self.capture_id), [])

    def test_fallback_sem_applied_escaneia_a_trilha(self):
        # Trilhas antigas (sem "applied" estruturado no de-para.json): casa o
        # valor sintético digitado na trilha antes do seq da falha.
        self._write_depara(screens=[{
            "entity": "est361", "display_name": "3.6.1 PEDIDO", "operation": "create",
            "fields": [
                {"field": "modelo", "original": "g2511", "synthetic": "zz999", "kept": False},
                {"field": "loja", "original": "01", "synthetic": "01", "kept": True},
            ],
        }])
        eventos = [_det_event(1, "zz999"), _det_event(2, "\r"), _det_event(3, "7")]
        self.trail_dir.joinpath("audit-000001.jsonl").write_text(
            "".join(json.dumps(ev) + "\n" for ev in eventos), encoding="utf-8")
        run_id = _create_synthetic_run(self.con, self.capture_id, str(self.trail_dir))
        _add_failure(self.con, run_id, 10, "Item inválido para esta operação")
        sugestoes = prefs_svc.feedback_for_capture(self.con, self.capture_id)
        self.assertEqual(len(sugestoes), 1)
        self.assertEqual(sugestoes[0]["field"], "modelo")

    def test_run_de_outra_captura_nao_entra(self):
        self._write_depara(applied=[
            {"field": "modelo", "original": "g2511", "synthetic": "zz999",
             "seq_start": 20, "seq_end": 24},
        ])
        outra = _create_capture(self.con, self.user_id, str(Path(self.tmpdir.name) / "cap2"))
        run_id = _create_synthetic_run(self.con, outra, str(self.trail_dir))
        _add_failure(self.con, run_id, 40, "Codigo nao cadastrado")
        self.assertEqual(prefs_svc.feedback_for_capture(self.con, self.capture_id), [])

    def test_feedback_por_run(self):
        self._write_depara(applied=[
            {"field": "modelo", "original": "g2511", "synthetic": "zz999",
             "seq_start": 20, "seq_end": 24},
        ])
        run_id = _create_synthetic_run(self.con, self.capture_id, str(self.trail_dir))
        _add_failure(self.con, run_id, 40, "Quantidade não confere com o pedido")
        sugestoes = prefs_svc.feedback_for_run(self.con, run_id)
        self.assertEqual(len(sugestoes), 1)
        self.assertEqual(sugestoes[0]["field"], "modelo")

    def test_run_nao_sintetica_sem_feedback(self):
        cur = self.con.execute(
            "INSERT INTO replay_runs(log_dir,target_host,target_user,target_command,mode,params_json,run_fingerprint,status,created_at_ms,created_by)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (str(self.trail_dir), "127.0.0.1", "ferblo", "", "strict-global",
             json.dumps({}), "fp-real", "success", now_ms(), 1),
        )
        _add_failure(self.con, int(cur.lastrowid), 40, "Codigo nao cadastrado")
        self.assertEqual(prefs_svc.feedback_for_run(self.con, int(cur.lastrowid)), [])

    def test_applied_dos_params_imune_a_sobrescrita_do_trail_dir(self):
        """O trail dir é FIXO por captura e sobrescrito a cada síntese — o
        de-para.json pode ser de outra run. O applied dos params da run é a
        fonte de verdade e deve vencer."""
        # de-para.json "atual" (de uma re-síntese posterior) aponta outro campo
        self._write_depara(applied=[
            {"field": "qtd", "original": "2", "synthetic": "9",
             "seq_start": 30, "seq_end": 30},
        ])
        run_id = _create_synthetic_run(
            self.con, self.capture_id, str(self.trail_dir),
            extra_params={"synthetic_applied": [
                {"field": "modelo", "original": "g2511", "synthetic": "zz999",
                 "seq_start": 20, "seq_end": 24},
            ]},
        )
        _add_failure(self.con, run_id, 40, "Codigo nao cadastrado")
        sugestoes = prefs_svc.feedback_for_capture(self.con, self.capture_id)
        self.assertEqual(len(sugestoes), 1)
        self.assertEqual(sugestoes[0]["field"], "modelo")

    def test_sem_applied_nos_params_cai_no_depara_json(self):
        """Compatibilidade: runs sem synthetic_applied usam o de-para.json."""
        self._write_depara(applied=[
            {"field": "modelo", "original": "g2511", "synthetic": "zz999",
             "seq_start": 20, "seq_end": 24},
        ])
        run_id = _create_synthetic_run(self.con, self.capture_id, str(self.trail_dir))
        _add_failure(self.con, run_id, 40, "Codigo nao cadastrado")
        sugestoes = prefs_svc.feedback_for_run(self.con, run_id)
        self.assertEqual(len(sugestoes), 1)
        self.assertEqual(sugestoes[0]["field"], "modelo")


# ---------------------------------------------------------------------------
# Wiring: synthesize/start_synthetic_replay × prefs, KB meta e rotas novas
# ---------------------------------------------------------------------------

_FONTE_CADCLI = """
TITLE "Cadastro de Clientes"
@ 1,1 SAY "Nome:"
@ 1,20 GET nome
@ 2,1 SAY "CPF:"
@ 2,20 GET cpf
USE CLIENTES
APPEND BLANK
REPLACE nome WITH m.nome, cpf WITH m.cpf
"""


def _b64(texto: str) -> str:
    return base64.b64encode(texto.encode("utf-8")).decode("ascii")


class _FakeParsedPath:
    def __init__(self, full_path: str):
        from urllib.parse import urlparse
        parsed = urlparse(full_path)
        self.path = parsed.path
        self.query = parsed.query


class _FakeWFile:
    def __init__(self):
        self.data = b""

    def write(self, data: bytes):
        self.data += data


class _FakeHandler:
    def __init__(self, db_path: str, body: dict | None = None):
        self._db_path = db_path
        self._body = body or {}
        self.status_code = 200
        self.response_headers = {}
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

    def response_json(self) -> dict:
        return json.loads(self.wfile.data.decode("utf-8"))


class _FakeRunner:
    def __init__(self):
        self.hmac_key = b"fake-runner-hmac"
        self.started = []

    def start_run_async(self, run_id: int):
        self.started.append(int(run_id))


class _FakeServer:
    def __init__(self, db_path: str, runner):
        self.db_path = db_path
        self.runner = runner


class SynthesizeWiringTests(unittest.TestCase):
    """synthesize_capture: merge de skip_fields + warnings de KB."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        tmp = Path(self.tmpdir.name)
        self.db_path = str(tmp / "test.db")
        self.source_dir = tmp / "fontes"
        self.source_dir.mkdir()
        (self.source_dir / "cadcli.prg").write_text(_FONTE_CADCLI, encoding="utf-8")
        self.con = connect(self.db_path)
        init_db(self.con)
        self.user_id = _create_user(self.con)
        log_dir = tmp / "cap1"
        log_dir.mkdir()
        eventos = [
            {"type": "session_start", "session_id": "s1", "seq_global": 1},
            {"type": "deterministic_input", "session_id": "s1", "seq_global": 2,
             "screen_sig": "L=3;W=40;LBL=Cadastro de Clientes",
             "screen_sample": "Cadastro de Clientes", "norm_len": 120,
             "key_b64": _b64("JOSE DA SILVA\r")},
            {"type": "deterministic_input", "session_id": "s1", "seq_global": 3,
             "screen_sig": "L=3;W=40;LBL=Cadastro de Clientes",
             "screen_sample": "Cadastro de Clientes", "norm_len": 120,
             "key_b64": _b64("123.456.789-09\r")},
            {"type": "session_end", "session_id": "s1", "seq_global": 4},
        ]
        (log_dir / "audit-000001.jsonl").write_text(
            "\n".join(json.dumps(e) for e in eventos), encoding="utf-8")
        self.capture_id = _create_capture(self.con, self.user_id, str(log_dir))

    def tearDown(self):
        self.con.close()
        self.tmpdir.cleanup()

    def _synthesize(self, **kwargs):
        from control.services import capture_synthesis_service as synth_svc
        kwargs.setdefault("source_dir", str(self.source_dir))
        return synth_svc.synthesize_capture(
            self.con, self.capture_id,
            samples=1, seed=42, name="cap-prefs", **kwargs)

    def test_analyze_source_grava_kb_meta(self):
        from control.services import synthetic_plan_service as plan_svc
        plan_svc.analyze_source_payload(self.con, str(self.source_dir))
        meta = prefs_svc.load_kb_meta(self.con)
        self.assertIsNotNone(meta)
        self.assertEqual(meta["source_dir"], str(self.source_dir.resolve()))
        self.assertEqual(meta["file_count"], 1)

    def test_synthesize_com_skip_explicito_persiste(self):
        self._synthesize(skip_fields=["nome"])
        prefs = prefs_svc.load_prefs(self.con, self.capture_id)
        self.assertEqual(prefs["skip_fields"], ["nome"])

    def test_synthesize_sem_skip_usa_o_armazenado(self):
        prefs_svc.save_prefs(self.con, self.capture_id, {"skip_fields": ["nome"]})
        resultado = self._synthesize()
        # effective inclui o campo armazenado; stored não muda
        self.assertIn("nome", resultado["skip_fields"])
        self.assertEqual(
            prefs_svc.load_prefs(self.con, self.capture_id)["skip_fields"], ["nome"])
        # e o de→para marca o campo como mantido
        campos = {f["field"]: f
                  for sc in resultado["depara"]["screens"] for f in sc["fields"]}
        self.assertTrue(campos["nome"]["kept"])

    def test_synthesize_avisa_quando_fontes_mudaram(self):
        from control.services import synthetic_plan_service as plan_svc
        plan_svc.analyze_source_payload(self.con, str(self.source_dir))
        novo = self.source_dir / "cadnovo.prg"
        novo.write_text("@ 1,1 GET x\n", encoding="utf-8")
        os.utime(novo, (time.time() + 5, time.time() + 5))
        resultado = self._synthesize()
        self.assertTrue(resultado["kb"]["stale"])
        self.assertTrue(any("desatualizada" in w for w in resultado["warnings"]))

    def test_synthesize_avisa_kb_de_outro_diretorio(self):
        from control.services import synthetic_plan_service as plan_svc
        plan_svc.analyze_source_payload(self.con, str(self.source_dir))
        outro = Path(self.tmpdir.name) / "outros-fontes"
        outro.mkdir()
        (outro / "cadcli.prg").write_text(_FONTE_CADCLI, encoding="utf-8")
        resultado = self._synthesize(source_dir=str(outro))
        self.assertTrue(resultado["kb"]["stale"])
        self.assertTrue(any("outro diretório" in w for w in resultado["warnings"]))


class SyntheticReplayPrefsRouteTests(unittest.TestCase):
    """Rota POST synthetic-replay: merge de prefs, cache do entry_point e
    manifest de→para com substituições estruturadas (feedback loop)."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        tmp = Path(self.tmpdir.name)
        self.db_path = str(tmp / "test.db")
        self.source_dir = tmp / "fontes"
        self.source_dir.mkdir()
        (self.source_dir / "cadcli.prg").write_text(_FONTE_CADCLI, encoding="utf-8")
        self.con = connect(self.db_path)
        init_db(self.con)
        self.user_id = _create_user(self.con)
        log_dir = tmp / "cap-replay"
        log_dir.mkdir()
        base_ev = {"v": "v1", "ts_ms": 1000, "actor": "tester",
                   "session_id": "s1", "seq_session": 1}
        eventos = [
            {**base_ev, "type": "session_start", "seq_global": 1,
             "logname": "ferblo"},
            {**base_ev, "type": "deterministic_input", "seq_global": 2,
             "screen_sig": "L=3;W=40;LBL=Cadastro de Clientes",
             "screen_sample": "Cadastro de Clientes", "norm_len": 120,
             "key_b64": _b64("JOSE DA SILVA")},
            {**base_ev, "type": "deterministic_input", "seq_global": 3,
             "screen_sig": "L=3;W=40;LBL=Cadastro de Clientes",
             "screen_sample": "Cadastro de Clientes", "norm_len": 120,
             "key_b64": _b64("\r")},
            {**base_ev, "type": "deterministic_input", "seq_global": 4,
             "screen_sig": "L=3;W=40;LBL=Cadastro de Clientes",
             "screen_sample": "Cadastro de Clientes", "norm_len": 120,
             "key_b64": _b64("123.456.789-09")},
            {**base_ev, "type": "deterministic_input", "seq_global": 5,
             "screen_sig": "L=3;W=40;LBL=Cadastro de Clientes",
             "screen_sample": "Cadastro de Clientes", "norm_len": 120,
             "key_b64": _b64("\r")},
            {**base_ev, "type": "session_end", "seq_global": 6},
        ]
        (log_dir / "audit-000001.jsonl").write_text(
            "\n".join(json.dumps(e) for e in eventos), encoding="utf-8")
        self.capture_id = _create_capture(self.con, self.user_id, str(log_dir))
        self.con.commit()

    def tearDown(self):
        self.con.close()
        self.tmpdir.cleanup()

    def _post_replay(self, body: dict) -> dict:
        from control.routes.capture_routes import handle_capture_post_route
        # wait=1: estes testes exercitam o fluxo síncrono de criação da run
        # (o default da rota passou a ser o job assíncrono — v0.9.3).
        body = {**body, "wait": 1}
        handler = _FakeHandler(self.db_path, body=body)
        runner = _FakeRunner()
        handler.server = _FakeServer(self.db_path, runner)
        parsed = _FakeParsedPath(f"/api/captures/{self.capture_id}/synthetic-replay")
        handled = handle_capture_post_route(
            handler, parsed, handler._body,
            now_ms_fn=lambda: 456,
            log_dir_base=str(Path(self.tmpdir.name) / "captures"),
        )
        self.assertTrue(handled)
        self.assertEqual(handler.status_code, 200, handler.wfile.data.decode("utf-8"))
        return handler.response_json()

    def test_replay_com_skip_explicito_persiste_replace(self):
        prefs_svc.save_prefs(self.con, self.capture_id, {"skip_fields": ["cpf"]})
        self.con.commit()
        data = self._post_replay({"source_dir": str(self.source_dir),
                                  "skip_fields": ["nome"]})
        self.assertIn("nome", data["skip_fields"])
        con = connect(self.db_path)
        init_db(con)
        prefs = prefs_svc.load_prefs(con, self.capture_id)
        con.close()
        self.assertEqual(prefs["skip_fields"], ["nome"])  # replace, não merge

    def test_replay_sem_skip_no_body_usa_o_armazenado(self):
        prefs_svc.save_prefs(self.con, self.capture_id, {"skip_fields": ["nome"]})
        self.con.commit()
        data = self._post_replay({"source_dir": str(self.source_dir)})
        self.assertIn("nome", data["skip_fields"])

    def test_replay_cacheia_entry_point_com_versao(self):
        self._post_replay({"source_dir": str(self.source_dir)})
        con = connect(self.db_path)
        init_db(con)
        prefs = prefs_svc.load_prefs(con, self.capture_id)
        con.close()
        # captura sem preâmbulo: entry=None cacheado com a versão do código
        self.assertIn("entry_point", prefs)
        self.assertIsNone(prefs["entry_point"])
        self.assertEqual(prefs["entry_point_version"], prefs_svc.current_version())

    def test_replay_reusa_entry_cache_sem_detectar(self):
        self._post_replay({"source_dir": str(self.source_dir)})
        # A 1ª run fica 'queued' e o fingerprint é único nesse estado —
        # finaliza para permitir a 2ª run (a mesma trilha) no teste.
        self.con.execute("UPDATE replay_runs SET status='success'")
        self.con.commit()
        import dakota_gateway.synthetic.synthetic_trail as trail_mod
        from unittest import mock as _mock
        with _mock.patch.object(
            trail_mod, "detect_session_entry",
            side_effect=AssertionError("detecção deveria vir do cache"),
        ):
            self._post_replay({"source_dir": str(self.source_dir)})

    def test_depara_manifest_grava_applied_estruturado(self):
        data = self._post_replay({"source_dir": str(self.source_dir)})
        manifest = Path(data["trail_dir"]) / "de-para.json"
        depara = json.loads(manifest.read_text(encoding="utf-8"))
        applied = depara.get("applied")
        self.assertIsInstance(applied, list)
        self.assertTrue(applied, "de-para.json deve gravar as substituições estruturadas")
        for rec in applied:
            self.assertIn("field", rec)
            self.assertIn("seq_start", rec)
            self.assertIn("seq_end", rec)

    def test_params_da_run_carregam_synthetic_applied(self):
        """Fonte de verdade do feedback loop: os params da run (o trail dir é
        compartilhado entre runs da captura e sobrescrito a cada síntese)."""
        data = self._post_replay({"source_dir": str(self.source_dir)})
        con = connect(self.db_path)
        init_db(con)
        row = con.execute(
            "SELECT params_json FROM replay_runs WHERE id=?", (data["run_id"],)
        ).fetchone()
        con.close()
        params = json.loads(row["params_json"] or "{}")
        applied = params.get("synthetic_applied")
        self.assertIsInstance(applied, list)
        self.assertTrue(applied, "params da run devem carregar synthetic_applied")
        for rec in applied:
            self.assertIn("field", rec)
            self.assertIn("seq_start", rec)
            self.assertIn("seq_end", rec)


class PrefsRoutesTests(unittest.TestCase):
    """Rotas GET/POST synthetic-prefs e GET synthetic-feedback."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        base = Path(self.tmpdir.name)
        self.db_path = str(base / "test.db")
        self.con = connect(self.db_path)
        init_db(self.con)
        self.user_id = _create_user(self.con)
        self.log_dir = base / "captures" / "cap1"
        self.log_dir.mkdir(parents=True)
        self.capture_id = _create_capture(self.con, self.user_id, str(self.log_dir))
        self.con.commit()

    def tearDown(self):
        self.con.close()
        self.tmpdir.cleanup()

    def test_post_synthetic_prefs_substitui_lista(self):
        from control.routes.capture_routes import handle_capture_post_route
        handler = _FakeHandler(self.db_path, body={"skip_fields": ["cpf", "nome"]})
        parsed = _FakeParsedPath(f"/api/captures/{self.capture_id}/synthetic-prefs")
        handled = handle_capture_post_route(
            handler, parsed, handler._body,
            now_ms_fn=lambda: 456, log_dir_base=str(self.log_dir.parent))
        self.assertTrue(handled)
        self.assertEqual(handler.status_code, 200)
        data = handler.response_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["prefs"]["skip_fields"], ["cpf", "nome"])
        con = connect(self.db_path)
        init_db(con)
        self.assertEqual(
            prefs_svc.load_prefs(con, self.capture_id)["skip_fields"], ["cpf", "nome"])
        con.close()

    def test_post_synthetic_prefs_body_invalido_400(self):
        from control.routes.capture_routes import handle_capture_post_route
        handler = _FakeHandler(self.db_path, body={"skip_fields": "cpf"})
        parsed = _FakeParsedPath(f"/api/captures/{self.capture_id}/synthetic-prefs")
        handled = handle_capture_post_route(
            handler, parsed, handler._body,
            now_ms_fn=lambda: 456, log_dir_base=str(self.log_dir.parent))
        self.assertTrue(handled)
        self.assertEqual(handler.status_code, 400)

    def test_get_synthetic_prefs(self):
        prefs_svc.save_prefs(self.con, self.capture_id, {"skip_fields": ["cpf"]})
        self.con.commit()
        from control.routes.capture_routes import handle_capture_get_route
        handler = _FakeHandler(self.db_path)
        parsed = _FakeParsedPath(f"/api/captures/{self.capture_id}/synthetic-prefs")
        handled = handle_capture_get_route(
            handler, parsed, read_gateway_monitor_fn=lambda *a, **k: {})
        self.assertTrue(handled)
        self.assertEqual(handler.status_code, 200)
        self.assertEqual(
            handler.response_json()["prefs"]["skip_fields"], ["cpf"])

    def test_get_synthetic_feedback(self):
        trail_dir = self.log_dir / "synthetic" / "capture-1-replay" / "trail"
        trail_dir.mkdir(parents=True)
        trail_dir.joinpath("de-para.json").write_text(json.dumps({
            "capture_id": self.capture_id,
            "applied": [{"field": "modelo", "original": "g2511",
                         "synthetic": "zz999", "seq_start": 20, "seq_end": 24}],
        }), encoding="utf-8")
        run_id = _create_synthetic_run(self.con, self.capture_id, str(trail_dir))
        _add_failure(self.con, run_id, 40, "Codigo nao cadastrado")
        self.con.commit()
        from control.routes.capture_routes import handle_capture_get_route
        handler = _FakeHandler(self.db_path)
        parsed = _FakeParsedPath(f"/api/captures/{self.capture_id}/synthetic-feedback")
        handled = handle_capture_get_route(
            handler, parsed, read_gateway_monitor_fn=lambda *a, **k: {})
        self.assertTrue(handled)
        self.assertEqual(handler.status_code, 200)
        data = handler.response_json()
        self.assertTrue(data["ok"])
        self.assertEqual(len(data["suggestions"]), 1)
        self.assertEqual(data["suggestions"][0]["field"], "modelo")


class RunDetailFeedbackTests(unittest.TestCase):
    """GET /api/runs/{id}: payload da run sintética ganha synthetic_feedback."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        base = Path(self.tmpdir.name)
        self.con = connect(str(base / "test.db"))
        init_db(self.con)
        self.user_id = _create_user(self.con)
        self.log_dir = base / "captures" / "cap1"
        self.log_dir.mkdir(parents=True)
        self.capture_id = _create_capture(self.con, self.user_id, str(self.log_dir))

    def tearDown(self):
        self.con.close()
        self.tmpdir.cleanup()

    def test_run_sintetica_ganha_feedback_e_prefs(self):
        from control.services.run_service import get_run_detail_payload
        trail_dir = self.log_dir / "synthetic" / "capture-1-replay" / "trail"
        trail_dir.mkdir(parents=True)
        trail_dir.joinpath("de-para.json").write_text(json.dumps({
            "capture_id": self.capture_id,
            "applied": [{"field": "modelo", "original": "g2511",
                         "synthetic": "zz999", "seq_start": 20, "seq_end": 24}],
        }), encoding="utf-8")
        prefs_svc.save_prefs(self.con, self.capture_id, {"skip_fields": ["cpf"]})
        run_id = _create_synthetic_run(self.con, self.capture_id, str(trail_dir))
        _add_failure(self.con, run_id, 40, "Codigo nao cadastrado")
        payload = get_run_detail_payload(self.con, run_id)
        run = payload["run"]
        self.assertEqual(len(run["synthetic_feedback"]), 1)
        self.assertEqual(run["synthetic_feedback"][0]["field"], "modelo")
        self.assertEqual(run["synthetic_stored_skip_fields"], ["cpf"])

    def test_run_real_nao_ganha_feedback(self):
        from control.services.run_service import get_run_detail_payload
        cur = self.con.execute(
            "INSERT INTO replay_runs(log_dir,target_host,target_user,target_command,mode,params_json,run_fingerprint,status,created_at_ms,created_by)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (str(self.log_dir), "127.0.0.1", "ferblo", "", "strict-global",
             json.dumps({}), "fp-real-2", "success", now_ms(), 1),
        )
        payload = get_run_detail_payload(self.con, int(cur.lastrowid))
        self.assertNotIn("synthetic_feedback", payload["run"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
