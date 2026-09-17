"""Pacote de evidência verificável de uma run real (export + verify).

Gera um diretório autocontido (ou tar.gz) com todos os dados brutos de uma
run — sessões, falhas estruturadas, métricas de host e trilhas observadas
assinadas — mais um ``manifest.json`` com sha256 individual de cada arquivo e
sha256 do conjunto, permitindo que terceiros recalculem e confiram conclusões
(ex.: "run 96 no AIX levou 89,3 s") sem acesso ao banco original.

Sanitização (o que NUNCA entra no pacote):
- senhas, tokens, cookies, chaves HMAC e qualquer valor de parâmetro cuja
  chave indique segredo (ver ``_SECRET_KEY_MARKERS``) — substituídos por um
  placeholder explícito;
- ``credential_ref`` é mantido porque é apenas uma REFERÊNCIA (ex.:
  ``env:VAR``), nunca o segredo em si;
- connection profiles são exportados só como referência (id/nome), sem
  material de credencial;
- as trilhas observadas carregam apenas a SAÍDA do destino (dir="out") —
  inputs/teclas não são gravados pelo ObservedTrailRecorder; o conteúdo bruto
  é preservado byte a byte porque qualquer alteração quebraria a hash-chain.

Verificação independente (``verify_evidence_bundle``): re-confere sha256 de
cada arquivo contra o manifesto, o sha256 do conjunto, roda ``verify_log`` em
cada trilha incluída e cruza o resumo (run.json) com os dados brutos
(failures.json). Sem a chave HMAC, hash-chain/sequências são verificados e o
campo ``hmac`` volta "not_verified" — registrado de forma explícita em
``notes``, nunca silencioso.
"""
from __future__ import annotations

import hashlib
import json
import platform
import shutil
import socket
import tarfile
import tempfile
import time
from pathlib import Path

from .state_db import query_all, query_one
from .verifier import VerificationError, verify_log

BUNDLE_FORMAT = "dakota-run-evidence/v1"

# Marcadores de chave sensível (case-insensitive, substring). Qualquer chave
# de params/estruturas que contenha um destes marcadores tem o valor omitido.
_SECRET_KEY_MARKERS = (
    "password",
    "passwd",
    "senha",
    "secret",
    "token",
    "cookie",
    "hmac",
    "private_key",
    "api_key",
    "apikey",
)

_SECRET_PLACEHOLDER = "<omitido:valor-sensivel>"

# Itens omitidos documentados no manifesto (transparência da sanitização).
OMISSIONS = [
    "valores de params cujo nome indica segredo (password/token/secret/hmac/cookie/...) — substituídos por placeholder",
    "chave HMAC da trilha — nunca exportada; verificação de HMAC exige --hmac-key-file no verificador",
    "connection profiles: exportados apenas como referência (credential_ref, ex.: env:VAR), sem material de credencial",
    "credenciais de destino resolvidas em runtime (senha efetiva do SSH) não existem no banco e não são exportadas",
]


def sanitize_params(params) -> object:
    """Remove valores sensíveis de um dict de params (recursivo).

    ``credential_ref`` é preservado: é uma referência (ex.: ``env:VAR``),
    não o segredo. Qualquer outra chave com marcador sensível vira
    placeholder explícito.
    """
    if isinstance(params, dict):
        clean = {}
        for key, value in params.items():
            key_lower = str(key).lower()
            if key_lower == "credential_ref":
                clean[key] = value
            elif any(marker in key_lower for marker in _SECRET_KEY_MARKERS):
                clean[key] = _SECRET_PLACEHOLDER
            else:
                clean[key] = sanitize_params(value)
        return clean
    if isinstance(params, list):
        return [sanitize_params(item) for item in params]
    return params


def _sha256_file(path: Path) -> str:
    sha = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            sha.update(chunk)
    return sha.hexdigest()


def _write_json(path: Path, payload) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _load_params(run) -> dict:
    try:
        params = json.loads(run["params_json"] or "{}")
    except (TypeError, ValueError):
        params = {}
    return params if isinstance(params, dict) else {}


def _derive_session_stats(session_dir: Path) -> dict:
    """Estatísticas da sessão a partir da trilha observada (streaming)."""
    started_at_ms = 0
    ended_at_ms = 0
    events = 0
    bytes_out = 0
    bytes_in = 0
    for jsonl in sorted(session_dir.glob("audit-*.jsonl")):
        with open(jsonl, "rb") as fh:
            for raw in fh:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(ev, dict):
                    continue
                events += 1
                ev_type = str(ev.get("type") or "")
                if ev_type == "session_start":
                    started_at_ms = int(ev.get("ts_ms") or 0)
                elif ev_type == "session_end":
                    ended_at_ms = int(ev.get("ts_ms") or 0)
                elif ev_type == "bytes":
                    n = int(ev.get("n") or 0)
                    if str(ev.get("dir") or "") == "out":
                        bytes_out += n
                    else:
                        bytes_in += n
    return {
        "session_id": session_dir.name,
        "started_at_ms": started_at_ms,
        "ended_at_ms": ended_at_ms,
        "duration_ms": max(0, ended_at_ms - started_at_ms) if started_at_ms and ended_at_ms else 0,
        "events": events,
        "bytes_out": bytes_out,
        "bytes_in": bytes_in,
        "trail_dir": f"observed/{session_dir.name}",
        "has_manifest": any(session_dir.glob("audit-*.jsonl.manifest.json")),
    }


def _collect_manifest_files(bundle_dir: Path) -> list:
    files = []
    for path in sorted(bundle_dir.rglob("*")):
        if not path.is_file() or path.name == "manifest.json":
            continue
        rel = path.relative_to(bundle_dir).as_posix()
        files.append({"path": rel, "sha256": _sha256_file(path), "bytes": path.stat().st_size})
    return files


def _bundle_sha256(files: list) -> str:
    concat = "".join(f"{item['sha256']}  {item['path']}\n" for item in files)
    return hashlib.sha256(concat.encode("utf-8")).hexdigest()


def build_run_evidence_bundle(
    con,
    run_id: int,
    dest_dir: str,
    *,
    hmac_key: bytes = b"",
    version: str = "",
    host_metrics_export: dict | None = None,
) -> dict:
    """Materializa o pacote de evidência da run em ``dest_dir`` e retorna o manifesto.

    ``host_metrics_export`` é o payload de ``host_metrics_service.build_export``
    (a separação evita que dakota_gateway importe o control plane). Sem trilhas
    observadas ou sem métricas, o pacote ainda é gerado e a lacuna é registrada
    explicitamente em ``manifest["gaps"]``.
    """
    run = query_one(con, "SELECT * FROM replay_runs WHERE id=?", (int(run_id),))
    if not run:
        raise ValueError("run inexistente")

    bundle_dir = Path(dest_dir)
    if bundle_dir.exists() and any(bundle_dir.iterdir()):
        raise ValueError(f"dest_dir não está vazio: {dest_dir}")
    bundle_dir.mkdir(parents=True, exist_ok=True)

    gaps: list[str] = []
    params = _load_params(run)
    clean_params = sanitize_params(params)
    execution_policy = str(params.get("execution_policy") or "conservative")

    # ── trilhas observadas (cópia byte a byte — a hash-chain depende disso) ──
    observed_sessions: list[str] = []
    sessions_stats: list[dict] = []
    verify_trails: dict[str, dict] = {}
    observed_dir = str(run["observed_dir"] or "").strip()
    if observed_dir and Path(observed_dir).is_dir():
        for entry in sorted(Path(observed_dir).iterdir()):
            if not entry.is_dir() or not any(entry.glob("audit-*.jsonl")):
                continue
            dest_session = bundle_dir / "observed" / entry.name
            dest_session.mkdir(parents=True, exist_ok=True)
            for src in sorted(entry.iterdir()):
                if src.is_file():
                    shutil.copyfile(src, dest_session / src.name)
            observed_sessions.append(entry.name)
            sessions_stats.append(_derive_session_stats(dest_session))
            try:
                summary = verify_log(
                    str(dest_session), hmac_key, allow_no_hmac=True
                )
                verify_trails[entry.name] = {
                    "status": "ok",
                    "hmac": summary["hmac"],
                    "events": summary["events"],
                    "files": summary["files"],
                }
            except VerificationError as exc:
                verify_trails[entry.name] = {
                    "status": "error",
                    "hmac": "not_verified",
                    "detail": str(exc),
                }
                gaps.append(f"trail_verify_failed:{entry.name}")
    else:
        gaps.append("no_observed_trails")

    # ── falhas estruturadas completas (sem limite de linhas) ──
    failure_rows = query_all(
        con,
        """
        SELECT id, ts_ms, session_id, seq_global, seq_session, flow_name,
               event_type, failure_type, severity, expected_value,
               observed_value, message, evidence_json
        FROM replay_failures WHERE run_id=? ORDER BY id
        """,
        (int(run_id),),
    )
    failures = []
    for row in failure_rows:
        item = dict(row)
        try:
            item["evidence"] = json.loads(item.pop("evidence_json") or "{}")
        except Exception:
            item["evidence"] = {"raw": item.pop("evidence_json", None)}
        failures.append(item)

    failure_summary = {"total": len(failures), "by_type": {}, "by_severity": {}}
    for item in failures:
        ftype = str(item.get("failure_type") or "unknown")
        sev = str(item.get("severity") or "unknown")
        failure_summary["by_type"][ftype] = failure_summary["by_type"].get(ftype, 0) + 1
        failure_summary["by_severity"][sev] = failure_summary["by_severity"].get(sev, 0) + 1

    # ── eventos da run (timeline operacional) ──
    event_rows = query_all(
        con,
        "SELECT id, ts_ms, kind, message, data_json FROM replay_run_events WHERE run_id=? ORDER BY id",
        (int(run_id),),
    )
    run_events = [dict(row) for row in event_rows]

    # ── métricas de host do intervalo da run ──
    if host_metrics_export is None:
        gaps.append("host_metrics_unavailable")

    started_at_ms = int(run["started_at_ms"] or 0)
    finished_at_ms = int(run["finished_at_ms"] or 0)
    run_payload = {
        "id": int(run["id"]),
        "status": run["status"],
        "mode": run["mode"],
        "log_dir": run["log_dir"],
        "target_host": run["target_host"],
        "target_user": run["target_user"],
        "target_command": run["target_command"],
        "created_at_ms": int(run["created_at_ms"] or 0),
        "started_at_ms": started_at_ms,
        "finished_at_ms": finished_at_ms,
        "duration_ms": max(0, finished_at_ms - started_at_ms) if started_at_ms and finished_at_ms else 0,
        "verify_ok": run["verify_ok"],
        "verify_error": run["verify_error"],
        "entry_mode": run["entry_mode"],
        "via_gateway": bool(run["via_gateway"]),
        "compliance_status": run["compliance_status"],
        "parent_run_id": run["parent_run_id"],
        "metrics": _load_metrics(run),
    }

    _write_json(bundle_dir / "run.json", {
        "run": run_payload,
        "params": clean_params,
        "failure_summary": failure_summary,
        "run_events": run_events,
    })
    _write_json(bundle_dir / "failures.json", {"failures": failures})
    _write_json(bundle_dir / "sessions.json", {
        "source": "observed_trails" if observed_sessions else "none",
        "sessions": sessions_stats,
    })
    if host_metrics_export is not None:
        _write_json(bundle_dir / "host-metrics.json", host_metrics_export)
    _write_json(bundle_dir / "verify-results.json", {
        "generated_at_ms": int(time.time() * 1000),
        "hmac_key_available": bool(hmac_key),
        "trails": verify_trails,
    })

    files = _collect_manifest_files(bundle_dir)
    manifest = {
        "format": BUNDLE_FORMAT,
        "generator": {
            "tool": "dakota-replay2",
            "version": version,
            "python": platform.python_version(),
        },
        "run_id": int(run_id),
        "created_at_ms": int(time.time() * 1000),
        "run": {
            "status": run_payload["status"],
            "mode": run_payload["mode"],
            "target_host": run_payload["target_host"],
            "started_at_ms": started_at_ms,
            "finished_at_ms": finished_at_ms,
            "duration_ms": run_payload["duration_ms"],
        },
        "execution_policy": execution_policy,
        "environment": {
            "env": str(params.get("environment") or params.get("target_environment") or ""),
            "host": socket.gethostname(),
            "platform": platform.system().lower(),
        },
        "params": clean_params,
        "trails": {
            "observed_sessions": observed_sessions,
            "hmac_verified_at_export": bool(hmac_key) and bool(observed_sessions),
        },
        "files": files,
        "bundle_sha256": _bundle_sha256(files),
        "omissions": list(OMISSIONS),
        "gaps": gaps,
    }
    _write_json(bundle_dir / "manifest.json", manifest)
    return manifest


def _load_metrics(run) -> dict:
    try:
        metrics = json.loads(run["metrics_json"] or "{}")
    except (TypeError, ValueError):
        metrics = {}
    return metrics if isinstance(metrics, dict) else {}


def _verify_trail_dir(session_dir: Path, hmac_key: bytes | None) -> dict:
    try:
        summary = verify_log(
            str(session_dir), hmac_key or b"", allow_no_hmac=True
        )
        return {"status": "ok", "hmac": summary["hmac"], "events": summary["events"], "detail": ""}
    except VerificationError as exc:
        return {"status": "error", "hmac": "not_verified", "events": 0, "detail": str(exc)}


def verify_evidence_bundle(bundle_path: str, *, hmac_key: bytes | None = None) -> dict:
    """Verifica um pacote de evidência SÓ com o conteúdo do pacote.

    Aceita diretório ou tar.gz. Retorna veredito estruturado::

        {"status": "VALID"|"INVALID", "problems": [...], "notes": [...],
         "hmac": "verified"|"not_verified"|"not_applicable",
         "checks": {...}}

    Sem ``hmac_key`` as trilhas têm hash-chain/sequências verificadas e o
    HMAC é marcado "not_verified" em ``notes`` — nunca silencioso.
    """
    problems: list[str] = []
    notes: list[str] = []
    checks: dict[str, object] = {}

    path = Path(bundle_path)
    if not path.exists():
        return {
            "status": "INVALID",
            "problems": [f"pacote não encontrado: {bundle_path}"],
            "notes": [],
            "hmac": "not_applicable",
            "checks": {},
        }
    if path.is_file():
        # tar.gz exportado pela rota HTTP: extrai em diretório temporário.
        with tempfile.TemporaryDirectory(prefix="dakota-evidence-") as tmp:
            try:
                with tarfile.open(path, "r:gz") as tar:
                    tar.extractall(tmp, filter="data")
            except (tarfile.TarError, OSError) as exc:
                return {
                    "status": "INVALID",
                    "problems": [f"tarball ilegível: {exc}"],
                    "notes": [],
                    "hmac": "not_applicable",
                    "checks": {},
                }
            roots = [entry for entry in Path(tmp).iterdir()]
            if len(roots) == 1 and roots[0].is_dir():
                return verify_evidence_bundle(str(roots[0]), hmac_key=hmac_key)
            return verify_evidence_bundle(tmp, hmac_key=hmac_key)

    manifest_path = path / "manifest.json"
    if not manifest_path.is_file():
        return {
            "status": "INVALID",
            "problems": ["manifest.json ausente no pacote"],
            "notes": [],
            "hmac": "not_applicable",
            "checks": {},
        }
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        return {
            "status": "INVALID",
            "problems": [f"manifest.json inválido: {exc}"],
            "notes": [],
            "hmac": "not_applicable",
            "checks": {},
        }
    if manifest.get("format") != BUNDLE_FORMAT:
        problems.append(f"formato do manifesto desconhecido: {manifest.get('format')!r}")

    # ── 1) sha256 de cada arquivo listado vs manifesto ──
    listed = manifest.get("files") or []
    listed_paths = set()
    for item in listed:
        rel = str(item.get("path") or "")
        listed_paths.add(rel)
        file_path = path / rel
        if not file_path.is_file():
            problems.append(f"arquivo listado no manifesto ausente: {rel}")
            continue
        expected_bytes = item.get("bytes")
        if expected_bytes is not None and file_path.stat().st_size != int(expected_bytes):
            problems.append(f"tamanho diverge do manifesto: {rel}")
        if _sha256_file(file_path) != str(item.get("sha256") or ""):
            problems.append(f"sha256 diverge do manifesto: {rel}")
    checks["files_ok"] = not any("manifesto" in p for p in problems)

    # arquivos extras não listados (suspeito: conteúdo injetado no pacote)
    for extra in sorted(path.rglob("*")):
        if extra.is_file():
            rel = extra.relative_to(path).as_posix()
            if rel != "manifest.json" and rel not in listed_paths:
                problems.append(f"arquivo não listado no manifesto: {rel}")

    # ── 2) sha256 do conjunto ──
    if listed and _bundle_sha256(listed) != str(manifest.get("bundle_sha256") or ""):
        problems.append("bundle_sha256 do conjunto diverge do manifesto")

    # ── 3) consistência run_id/timestamps ──
    run_data = {}
    run_json_path = path / "run.json"
    if run_json_path.is_file():
        try:
            run_data = json.loads(run_json_path.read_text(encoding="utf-8"))
        except ValueError as exc:
            problems.append(f"run.json inválido: {exc}")
    run_info = run_data.get("run") or {}
    if run_info and int(run_info.get("id") or 0) != int(manifest.get("run_id") or 0):
        problems.append(
            f"run_id diverge: manifesto={manifest.get('run_id')} run.json={run_info.get('id')}"
        )
    started = int(run_info.get("started_at_ms") or 0)
    finished = int(run_info.get("finished_at_ms") or 0)
    if started and finished and finished < started:
        problems.append(f"timestamps inconsistentes: finished_at_ms < started_at_ms ({finished} < {started})")

    # ── 4) consistência resumo × dados brutos (falha inventada/a menos) ──
    failures_raw = []
    failures_path = path / "failures.json"
    if failures_path.is_file():
        try:
            failures_raw = (json.loads(failures_path.read_text(encoding="utf-8")) or {}).get("failures") or []
        except ValueError as exc:
            problems.append(f"failures.json inválido: {exc}")
    summary = run_data.get("failure_summary") or {}
    if "total" in summary and int(summary.get("total") or 0) != len(failures_raw):
        problems.append(
            f"resumo de falhas diverge dos dados brutos: summary.total={summary.get('total')} failures.json={len(failures_raw)}"
        )
    by_type = summary.get("by_type") or {}
    real_by_type: dict[str, int] = {}
    for failure in failures_raw:
        ftype = str(failure.get("failure_type") or "unknown")
        real_by_type[ftype] = real_by_type.get(ftype, 0) + 1
    if by_type and by_type != real_by_type:
        problems.append(f"resumo by_type diverge dos dados brutos: {by_type} != {real_by_type}")
    checks["summary_consistent"] = not any("resumo" in p for p in problems)

    # ── 5) verify_log em cada trilha incluída ──
    hmac_state = "not_applicable"
    trails_info = manifest.get("trails") or {}
    observed_sessions = trails_info.get("observed_sessions") or []
    gaps = manifest.get("gaps") or []
    if not observed_sessions and "no_observed_trails" not in gaps:
        problems.append("manifesto não lista trilhas observadas mas também não registra a lacuna no_observed_trails")
    trail_results: dict[str, dict] = {}
    for session_id in observed_sessions:
        session_dir = path / "observed" / str(session_id)
        if not session_dir.is_dir():
            problems.append(f"trilha da sessão ausente no pacote: observed/{session_id}")
            continue
        result = _verify_trail_dir(session_dir, hmac_key)
        trail_results[str(session_id)] = result
        if result["status"] != "ok":
            problems.append(f"trilha observed/{session_id} não verifica: {result['detail']}")
    if observed_sessions:
        hmac_state = "verified" if hmac_key else "not_verified"
        if not hmac_key:
            notes.append(
                "hmac não verificado (chave ausente): hash-chain e sequências das trilhas foram conferidos; "
                "para verificar o HMAC, rode com --hmac-key-file"
            )
    checks["trails"] = trail_results

    # ── 6) verify-results.json gravado na exportação bate com a re-verificação ──
    recorded_path = path / "verify-results.json"
    if recorded_path.is_file() and observed_sessions:
        try:
            recorded = (json.loads(recorded_path.read_text(encoding="utf-8")) or {}).get("trails") or {}
        except ValueError as exc:
            recorded = {}
            problems.append(f"verify-results.json inválido: {exc}")
        for session_id, result in trail_results.items():
            recorded_status = str((recorded.get(session_id) or {}).get("status") or "")
            if recorded_status and recorded_status != result["status"]:
                problems.append(
                    f"verify-results.json registra status={recorded_status} para observed/{session_id}, "
                    f"mas a re-verificação deu {result['status']}"
                )
        if hmac_key and any(
            str((recorded.get(sid) or {}).get("hmac") or "") == "not_verified" for sid in trail_results
        ):
            notes.append("verify-results.json indica que a exportação NÃO conferiu o HMAC (chave ausente na origem)")

    return {
        "status": "VALID" if not problems else "INVALID",
        "problems": problems,
        "notes": notes,
        "hmac": hmac_state,
        "checks": checks,
        "run_id": manifest.get("run_id"),
        "format": manifest.get("format"),
    }
