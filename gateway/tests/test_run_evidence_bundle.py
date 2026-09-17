"""Testes do pacote de evidência verificável de runs (export + verify).

Cobre: geração do manifesto com hashes corretos, verificador VALID/INVALID
(pacote adulterado, arquivo removido, falha inventada no resumo), sanitização
de segredos e exportação de run sem trilhas observadas (lacuna explícita).
"""
from __future__ import annotations

import hashlib
import json
import sys
import tarfile
import time
from pathlib import Path

import pytest

GATEWAY_DIR = str(Path(__file__).resolve().parents[1])
if GATEWAY_DIR not in sys.path:
    sys.path.insert(0, GATEWAY_DIR)

from dakota_gateway.audit_writer import AuditWriter, b64, write_manifest
from dakota_gateway.evidence_bundle import (
    BUNDLE_FORMAT,
    sanitize_params,
    verify_evidence_bundle,
)
from dakota_gateway.schema import AuditEvent
from dakota_gateway.state_db import connect, init_db

from control.services.run_evidence_service import export_run_evidence

HMAC_KEY = b"chave-teste-evidence-bundle-32b!"
SECRET_PASSWORD = "S3cr3tValue-NaoPodeVazar"
SECRET_TOKEN = "tok-NaoPodeVazar-987654321"


def _now_ms() -> int:
    return int(time.time() * 1000)


def _make_db(tmp_path: Path, *, with_secret_params: bool = True) -> tuple:
    """Banco temporário com usuário, run finalizada, falhas e host metrics."""
    db_path = str(tmp_path / "replay.db")
    con = connect(db_path)
    init_db(con)
    con.execute(
        "INSERT INTO users(username,password_hash,role,created_at_ms) VALUES(?,?,?,?)",
        ("oper", "hash-falso", "operator", _now_ms()),
    )
    started = _now_ms() - 89300
    finished = _now_ms()
    params = {
        "execution_policy": "adaptive",
        "environment": "homologacao-aix",
        "credential_ref": "env:DAKOTA_TARGET_PASSWORD",
    }
    if with_secret_params:
        params["password"] = SECRET_PASSWORD
        params["nested"] = {"token": SECRET_TOKEN, "hmac_key": "outro-segredo"}
    con.execute(
        """
        INSERT INTO replay_runs(
          created_at_ms, created_by, log_dir, target_host, target_user,
          target_command, mode, params_json, metrics_json, run_fingerprint,
          status, started_at_ms, finished_at_ms, observed_dir
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            started - 1000,
            1,
            str(tmp_path / "capture"),
            "10.5.8.25",
            "results",
            "dbrt est361",
            "strict-global",
            json.dumps(params, ensure_ascii=False),
            json.dumps({"adaptive": {"sessions": 2}}),
            f"fp-{started}",
            "success",
            started,
            finished,
            str(tmp_path / "observed"),
        ),
    )
    run_id = int(con.execute("SELECT last_insert_rowid()").fetchone()[0])
    con.execute(
        """
        INSERT INTO replay_failures(
          run_id, ts_ms, session_id, seq_global, seq_session, flow_name,
          event_type, failure_type, severity, expected_value, observed_value,
          message, evidence_json
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            run_id,
            started + 1000,
            "s1",
            10,
            4,
            "pedido",
            "checkpoint",
            "screen_divergence",
            "medium",
            "TELA ESPERADA",
            "TELA OBSERVADA",
            "checkpoint divergiu",
            json.dumps({"screen": "evidencia"}),
        ),
    )
    for offset in (0, 30000, 60000):
        con.execute(
            "INSERT INTO host_metrics(ts_ms,cpu_pct,load1,mem_used_mb) VALUES(?,?,?,?)",
            (started + offset, 42.5, 1.5, 2048.0),
        )
    return con, run_id, started, finished


def _make_observed_trail(base_dir: Path, session_id: str, *, events: int = 4) -> Path:
    """Trilha observada sintética assinada (hash-chain + HMAC + manifest)."""
    session_dir = base_dir / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    writer = AuditWriter(str(session_dir), HMAC_KEY, rotate_bytes=0)
    ts = _now_ms()
    writer.append(AuditEvent(
        v="", seq_global=0, ts_ms=ts, type="session_start", actor="replay",
        session_id=session_id, seq_session=1, rows=25, cols=80,
    ))
    for idx in range(events):
        writer.append(AuditEvent(
            v="", seq_global=0, ts_ms=ts + 100 + idx, type="bytes", actor="replay",
            session_id=session_id, seq_session=2 + idx, dir="out",
            data_b64=b64(f"saida-{idx}".encode("utf-8")), n=len(f"saida-{idx}"),
        ))
    writer.append(AuditEvent(
        v="", seq_global=0, ts_ms=ts + 900, type="session_end", actor="replay",
        session_id=session_id, seq_session=2 + events,
    ))
    writer.close()
    for jsonl in sorted(session_dir.glob("audit-*.jsonl")):
        write_manifest(str(jsonl))
    return session_dir


@pytest.fixture()
def bundle_env(tmp_path):
    con, run_id, started, finished = _make_db(tmp_path)
    observed_dir = tmp_path / "observed"
    _make_observed_trail(observed_dir, "s1")
    _make_observed_trail(observed_dir, "s2")
    yield con, run_id, started, finished, tmp_path
    con.close()


def _export(con, run_id, tmp_path) -> tuple:
    dest = tmp_path / "out" / f"run-{run_id}-evidence"
    manifest = export_run_evidence(
        con, run_id, hmac_key=HMAC_KEY, dest_dir=str(dest), version="0.9.11-test"
    )
    return Path(manifest["bundle_dir"]), manifest


def _all_bundle_bytes(bundle_dir: Path) -> bytes:
    chunks = []
    for path in sorted(bundle_dir.rglob("*")):
        if path.is_file():
            chunks.append(path.read_bytes())
    return b"\n".join(chunks)


# ── exportação ───────────────────────────────────────────────────────────────


def test_export_gera_manifesto_com_hashes_corretos(bundle_env):
    con, run_id, started, finished, tmp_path = bundle_env
    bundle_dir, manifest = _export(con, run_id, tmp_path)

    assert manifest["format"] == BUNDLE_FORMAT
    assert manifest["run_id"] == run_id
    assert manifest["generator"]["version"] == "0.9.11-test"
    assert manifest["run"]["started_at_ms"] == started
    assert manifest["run"]["finished_at_ms"] == finished
    assert manifest["run"]["duration_ms"] == finished - started
    assert manifest["execution_policy"] == "adaptive"

    files = manifest["files"]
    assert files, "manifesto sem lista de arquivos"
    names = {item["path"] for item in files}
    assert {"run.json", "failures.json", "sessions.json", "verify-results.json"} <= names
    assert any(name.startswith("observed/s1/audit-") for name in names)
    assert any(name.startswith("observed/s2/audit-") for name in names)
    assert "manifest.json" not in names

    for item in files:
        data = (bundle_dir / item["path"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == item["sha256"]
        assert len(data) == item["bytes"]

    concat = "".join(f"{item['sha256']}  {item['path']}\n" for item in files)
    assert hashlib.sha256(concat.encode("utf-8")).hexdigest() == manifest["bundle_sha256"]


def test_export_inclui_falhas_sessoes_metricas_e_verify_results(bundle_env):
    con, run_id, _, _, tmp_path = bundle_env
    bundle_dir, manifest = _export(con, run_id, tmp_path)

    failures = json.loads((bundle_dir / "failures.json").read_text(encoding="utf-8"))
    assert len(failures["failures"]) == 1
    assert failures["failures"][0]["failure_type"] == "screen_divergence"

    sessions = json.loads((bundle_dir / "sessions.json").read_text(encoding="utf-8"))
    session_ids = {item["session_id"] for item in sessions["sessions"]}
    assert session_ids == {"s1", "s2"}
    for item in sessions["sessions"]:
        assert item["duration_ms"] >= 0
        assert item["events"] > 0
        assert item["bytes_out"] > 0

    metrics = json.loads((bundle_dir / "host-metrics.json").read_text(encoding="utf-8"))
    assert metrics["format"] == "dakota-host-metrics/v1"
    assert len(metrics["samples"]) == 3

    verify_results = json.loads((bundle_dir / "verify-results.json").read_text(encoding="utf-8"))
    assert set(verify_results["trails"].keys()) == {"s1", "s2"}
    for result in verify_results["trails"].values():
        assert result["status"] == "ok"
        assert result["hmac"] == "verified"


def test_export_sem_trilhas_observadas_registra_lacuna(tmp_path):
    con, run_id, _, _ = _make_db(tmp_path)
    con.execute("UPDATE replay_runs SET observed_dir='' WHERE id=?", (run_id,))
    bundle_dir, manifest = _export(con, run_id, tmp_path)
    con.close()

    assert "no_observed_trails" in manifest["gaps"]
    assert manifest["trails"]["observed_sessions"] == []
    result = verify_evidence_bundle(str(bundle_dir), hmac_key=HMAC_KEY)
    assert result["status"] == "VALID", result["problems"]
    assert result["hmac"] == "not_applicable"


def test_export_run_inexistente(tmp_path):
    con, _, _, _ = _make_db(tmp_path)
    with pytest.raises(ValueError):
        export_run_evidence(con, 9999, hmac_key=HMAC_KEY, dest_dir=str(tmp_path / "x"))
    con.close()


# ── sanitização ──────────────────────────────────────────────────────────────


def test_sanitize_params_omite_segredos_mas_mantem_referencias():
    params = {
        "password": SECRET_PASSWORD,
        "credential_ref": "env:DAKOTA_TARGET_PASSWORD",
        "nested": {"token": SECRET_TOKEN, "speed": 2.0},
    }
    clean = sanitize_params(params)
    assert clean["credential_ref"] == "env:DAKOTA_TARGET_PASSWORD"
    assert clean["nested"]["speed"] == 2.0
    dumped = json.dumps(clean)
    assert SECRET_PASSWORD not in dumped
    assert SECRET_TOKEN not in dumped


def test_pacote_nao_vaza_segredos(bundle_env):
    con, run_id, _, _, tmp_path = bundle_env
    bundle_dir, _ = _export(con, run_id, tmp_path)
    blob = _all_bundle_bytes(bundle_dir)
    assert SECRET_PASSWORD.encode() not in blob
    assert SECRET_TOKEN.encode() not in blob
    assert HMAC_KEY not in blob
    # credential_ref é referência (env:VAR), não segredo — deve ser mantida
    run_json = json.loads((bundle_dir / "run.json").read_text(encoding="utf-8"))
    assert run_json["params"]["credential_ref"] == "env:DAKOTA_TARGET_PASSWORD"


# ── verificador independente ─────────────────────────────────────────────────


def test_verificador_valida_pacote_integro(bundle_env):
    con, run_id, _, _, tmp_path = bundle_env
    bundle_dir, _ = _export(con, run_id, tmp_path)
    result = verify_evidence_bundle(str(bundle_dir), hmac_key=HMAC_KEY)
    assert result["status"] == "VALID", result["problems"]
    assert result["problems"] == []
    assert result["hmac"] == "verified"


def test_verificador_sem_chave_marca_hmac_not_verified(bundle_env):
    con, run_id, _, _, tmp_path = bundle_env
    bundle_dir, _ = _export(con, run_id, tmp_path)
    result = verify_evidence_bundle(str(bundle_dir))
    assert result["status"] == "VALID", result["problems"]
    assert result["hmac"] == "not_verified"
    # registro explícito, nunca silencioso
    assert any("hmac" in note.lower() for note in result["notes"])


def test_verificador_rejeita_arquivo_alterado(bundle_env):
    con, run_id, _, _, tmp_path = bundle_env
    bundle_dir, _ = _export(con, run_id, tmp_path)
    failures_path = bundle_dir / "failures.json"
    failures_path.write_text(
        failures_path.read_text(encoding="utf-8").replace("medium", "low"),
        encoding="utf-8",
    )
    result = verify_evidence_bundle(str(bundle_dir), hmac_key=HMAC_KEY)
    assert result["status"] == "INVALID"
    assert any("failures.json" in problem for problem in result["problems"])


def test_verificador_rejeita_arquivo_removido(bundle_env):
    con, run_id, _, _, tmp_path = bundle_env
    bundle_dir, _ = _export(con, run_id, tmp_path)
    (bundle_dir / "sessions.json").unlink()
    result = verify_evidence_bundle(str(bundle_dir), hmac_key=HMAC_KEY)
    assert result["status"] == "INVALID"
    assert any("sessions.json" in problem for problem in result["problems"])


def test_verificador_rejeita_falha_inventada_no_resumo(bundle_env):
    con, run_id, _, _, tmp_path = bundle_env
    bundle_dir, _ = _export(con, run_id, tmp_path)
    run_path = bundle_dir / "run.json"
    run_data = json.loads(run_path.read_text(encoding="utf-8"))
    run_data["failure_summary"]["total"] += 1
    run_data["failure_summary"]["by_type"]["timeout"] = 1
    # recalcula o hash do arquivo adulterado no manifesto (adulterações
    # consistentes passam no sha256, mas não na conferência cruzada)
    content = json.dumps(run_data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    run_path.write_text(content, encoding="utf-8")
    manifest_path = bundle_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    for item in manifest["files"]:
        if item["path"] == "run.json":
            item["sha256"] = digest
            item["bytes"] = len(content.encode("utf-8"))
    concat = "".join(f"{item['sha256']}  {item['path']}\n" for item in manifest["files"])
    manifest["bundle_sha256"] = hashlib.sha256(concat.encode("utf-8")).hexdigest()
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    result = verify_evidence_bundle(str(bundle_dir), hmac_key=HMAC_KEY)
    assert result["status"] == "INVALID"
    assert any("falha" in problem.lower() or "failure" in problem.lower() for problem in result["problems"])


def test_verificador_rejeita_trilha_adulterada(bundle_env):
    con, run_id, _, _, tmp_path = bundle_env
    bundle_dir, _ = _export(con, run_id, tmp_path)
    trail_file = next((bundle_dir / "observed" / "s1").glob("audit-*.jsonl"))
    content = trail_file.read_text(encoding="utf-8")
    trail_file.write_text(content.replace("session_start", "session_starT"), encoding="utf-8")
    result = verify_evidence_bundle(str(bundle_dir), hmac_key=HMAC_KEY)
    assert result["status"] == "INVALID"


def test_verificador_aceita_tarball(bundle_env, tmp_path):
    con, run_id, _, _, _ = bundle_env
    bundle_dir, _ = _export(con, run_id, tmp_path)
    tarball = tmp_path / "bundle.tar.gz"
    with tarfile.open(tarball, "w:gz") as tar:
        tar.add(bundle_dir, arcname=bundle_dir.name)
    result = verify_evidence_bundle(str(tarball), hmac_key=HMAC_KEY)
    assert result["status"] == "VALID", result["problems"]


def test_verificador_rejeita_pacote_sem_manifesto(tmp_path):
    vazio = tmp_path / "vazio"
    vazio.mkdir()
    (vazio / "run.json").write_text("{}", encoding="utf-8")
    result = verify_evidence_bundle(str(vazio))
    assert result["status"] == "INVALID"
    assert any("manifest" in problem.lower() for problem in result["problems"])


# ── superfície: rota HTTP e CLI ──────────────────────────────────────────────

import http.cookiejar
import importlib.util
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener

CONTROL_SERVER_PATH = Path(GATEWAY_DIR) / "control" / "server.py"
_SPEC = importlib.util.spec_from_file_location("control_server_evidence", CONTROL_SERVER_PATH)
CONTROL = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(CONTROL)


class EvidenceBundleRouteTests(unittest.TestCase):
    """GET /api/runs/{id}/evidence-bundle — download tar.gz do pacote."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        tmp_path = Path(self.tmpdir.name)
        self.db_path = str(tmp_path / "test.db")
        con = connect(self.db_path)
        init_db(con)
        import dakota_gateway.auth as auth

        con.execute(
            "INSERT INTO users(username,password_hash,role,created_at_ms) VALUES(?,?,'admin',?)",
            ("admin", auth.pbkdf2_hash_password("admin123"), _now_ms()),
        )
        con.close()
        con = connect(self.db_path)
        init_db(con)
        started = _now_ms() - 89300
        finished = _now_ms()
        con.execute(
            """
            INSERT INTO replay_runs(
              created_at_ms, created_by, log_dir, target_host, target_user,
              target_command, mode, params_json, run_fingerprint, status,
              started_at_ms, finished_at_ms, observed_dir
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                started - 1000, 1, str(tmp_path / "capture"), "10.5.8.25",
                "results", "dbrt est361", "strict-global",
                json.dumps({"execution_policy": "adaptive"}), f"fp-route-{started}",
                "success", started, finished, str(tmp_path / "observed"),
            ),
        )
        self.run_id = int(con.execute("SELECT last_insert_rowid()").fetchone()[0])
        con.close()
        _make_observed_trail(tmp_path / "observed", "s1")

        self.server = CONTROL.ControlServer(
            ("127.0.0.1", 0),
            CONTROL.Handler,
            db_path=self.db_path,
            cookie_secret=b"test_cookie_secret_32_bytes___",
            hmac_key=HMAC_KEY,
        )
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        time.sleep(0.2)
        self.opener = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self._post("/api/login", {"username": "admin", "password": "admin123"})

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmpdir.cleanup()

    def _post(self, path: str, data: dict):
        req = Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(data).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.opener.open(req, timeout=10) as resp:
            resp.read()

    def _get(self, path: str):
        req = Request(f"http://127.0.0.1:{self.port}{path}", method="GET")
        with self.opener.open(req, timeout=15) as resp:
            return resp.headers, resp.read()

    def test_rota_devolve_tarball_que_verifica_valid(self):
        headers, body = self._get(f"/api/runs/{self.run_id}/evidence-bundle")
        assert "attachment" in (headers.get("Content-Disposition") or "")
        assert body[:2] == b"\x1f\x8b", "resposta não é gzip"
        tarball = Path(self.tmpdir.name) / "download.tar.gz"
        tarball.write_bytes(body)
        result = verify_evidence_bundle(str(tarball), hmac_key=HMAC_KEY)
        assert result["status"] == "VALID", result["problems"]

    def test_rota_404_run_inexistente(self):
        req = Request(
            f"http://127.0.0.1:{self.port}/api/runs/9999/evidence-bundle", method="GET"
        )
        with pytest.raises(HTTPError) as excinfo:
            self.opener.open(req, timeout=10)
        assert excinfo.value.code == 404

    def test_rota_exige_autenticacao(self):
        anon = build_opener()
        req = Request(
            f"http://127.0.0.1:{self.port}/api/runs/{self.run_id}/evidence-bundle",
            method="GET",
        )
        with pytest.raises(HTTPError) as excinfo:
            anon.open(req, timeout=10)
        assert excinfo.value.code in (401, 403)


class EvidenceBundleCliTests(unittest.TestCase):
    """CLI: dakota-gateway runs verify-evidence --bundle <pacote>."""


def test_cli_verify_evidence_exit_codes(tmp_path, capsys):
    from dakota_gateway.cli import main as cli_main

    con, run_id, _, _ = _make_db(tmp_path)
    _make_observed_trail(tmp_path / "observed", "s1")
    dest = tmp_path / "out" / f"run-{run_id}-evidence"
    export_run_evidence(con, run_id, hmac_key=HMAC_KEY, dest_dir=str(dest))
    con.close()

    key_file = tmp_path / "hmac.key"
    key_file.write_bytes(HMAC_KEY)

    rc = cli_main(["runs", "verify-evidence", "--bundle", str(dest), "--hmac-key-file", str(key_file)])
    assert rc == 0
    saida = json.loads(capsys.readouterr().out)
    assert saida["status"] == "VALID"
    assert saida["hmac"] == "verified"

    # sem a chave: VALID mas hmac not_verified (explícito)
    rc = cli_main(["runs", "verify-evidence", "--bundle", str(dest)])
    assert rc == 0
    saida = json.loads(capsys.readouterr().out)
    assert saida["hmac"] == "not_verified"
    assert saida["notes"], "ausência da chave HMAC deve ser registrada explicitamente"

    # pacote adulterado: exit code 2
    (dest / "sessions.json").unlink()
    rc = cli_main(["runs", "verify-evidence", "--bundle", str(dest)])
    assert rc == 2
    saida = json.loads(capsys.readouterr().out)
    assert saida["status"] == "INVALID"
