"""Rotas REST do benchmark real (contrato §21).

Endpoints:

- ``POST /api/benchmarks`` — cria experimento (contrato imutável + manifesto);
- ``GET  /api/benchmarks`` — lista experimentos;
- ``GET  /api/benchmarks/<experiment_id>`` — detalhes (contrato/status/decisão);
- ``POST /api/benchmarks/<experiment_id>/start`` — execução supervisionada;
- ``POST /api/benchmarks/<experiment_id>/cancel`` — cancelamento cooperativo;
- ``GET  /api/benchmarks/<experiment_id>/runs`` — runs do experimento;
- ``GET  /api/benchmarks/<experiment_id>/metrics`` — amostras/agregados;
- ``GET  /api/benchmarks/<experiment_id>/comparison`` — comparação + decisão;
- ``GET  /api/benchmarks/<experiment_id>/report`` — report.json (?format=md).

As regras vivem em ``control.services.benchmark_service``; aqui só há parsing
de HTTP, autenticação e escrita da resposta.
"""
from __future__ import annotations

from urllib.parse import parse_qs

from control.routes.route_helpers import parse_int, write_json
from control.services import benchmark_service as svc


def _write_text(handler, status_code: int, *, content_type: str, content: str) -> None:
    handler.send_response(status_code)
    handler.send_header("Content-Type", content_type)
    handler.end_headers()
    handler.wfile.write(content.encode("utf-8"))


def _not_found(handler) -> None:
    handler.send_response(404)
    handler.end_headers()


def handle_benchmark_get_route(handler, parsed_path) -> bool:
    path = parsed_path.path
    if path != "/api/benchmarks" and not path.startswith("/api/benchmarks/"):
        return False
    user = handler._require()
    if not user:
        return True
    qs = parse_qs(parsed_path.query or "")
    artifacts_dir = handler.server.benchmark_artifacts_dir

    if path == "/api/benchmarks":
        con = handler._db()
        try:
            payload = svc.list_experiments_payload(con)
        finally:
            handler._db_release(con)
        write_json(handler, 200, payload)
        return True

    parts = path.split("/")
    if len(parts) not in (4, 5) or not parts[3]:
        return False
    experiment_id = parts[3]
    sub = parts[4] if len(parts) == 5 else ""

    if sub == "report":
        fmt = str((qs.get("format") or ["json"])[0] or "json").strip().lower()
        resultado = svc.report_payload(experiment_id, artifacts_dir=artifacts_dir, fmt=fmt)
        if resultado is None:
            _not_found(handler)
            return True
        content_type, content = resultado
        _write_text(handler, 200, content_type=content_type, content=content)
        return True

    con = handler._db()
    try:
        if sub == "":
            payload = svc.experiment_detail_payload(con, experiment_id,
                                                    artifacts_dir=artifacts_dir)
            if payload is None:
                _not_found(handler)
                return True
            write_json(handler, 200, payload)
            return True
        if sub == "runs":
            write_json(handler, 200, svc.list_runs_payload(con, experiment_id))
            return True
        if sub == "metrics":
            payload = svc.metrics_payload(
                con, experiment_id,
                environment_id=str((qs.get("environment_id") or [""])[0]).strip(),
                concurrency=parse_int((qs.get("concurrency") or ["0"])[0], 0, min_value=0),
                iteration=parse_int((qs.get("iteration") or ["0"])[0], 0, min_value=0),
            )
            write_json(handler, 200, payload)
            return True
        if sub == "comparison":
            payload = svc.comparison_payload(con, experiment_id,
                                             artifacts_dir=artifacts_dir)
            if payload is None:
                _not_found(handler)
                return True
            write_json(handler, 200, payload)
            return True
    finally:
        handler._db_release(con)
    return False


def handle_benchmark_post_route(handler, parsed_path, body: dict) -> bool:
    path = parsed_path.path
    if path != "/api/benchmarks" and not path.startswith("/api/benchmarks/"):
        return False
    user = handler._require(roles={"admin", "operator"})
    if not user:
        return True
    artifacts_dir = handler.server.benchmark_artifacts_dir

    if path == "/api/benchmarks":
        con = handler._db()
        try:
            try:
                payload = svc.create_experiment(con, body,
                                                artifacts_dir=artifacts_dir)
            except ValueError as exc:
                write_json(handler, 400, {"ok": False, "error": str(exc)})
                return True
        finally:
            handler._db_release(con)
        write_json(handler, 201, payload)
        return True

    parts = path.split("/")
    if len(parts) != 5 or not parts[3]:
        return False
    experiment_id, action = parts[3], parts[4]
    if action not in ("start", "cancel"):
        return False
    con = handler._db()
    try:
        if action == "start":
            status_code, payload = svc.start_experiment(
                con, handler.server.benchmark_supervisor, experiment_id,
                artifacts_dir=artifacts_dir)
        else:
            status_code, payload = svc.cancel_experiment(
                con, handler.server.benchmark_supervisor, experiment_id)
    finally:
        handler._db_release(con)
    write_json(handler, status_code, payload)
    return True
