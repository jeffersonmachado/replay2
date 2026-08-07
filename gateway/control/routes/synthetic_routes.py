"""API completa de synthetic para o dashboard web.

Cobre todos os comandos CLI:
  analyze-source, screens, generate, stress,
  journey (delegado para journey_routes),
  error-patterns (delegado para journey_routes),
  diff (delegado para journey_routes), report

Acoplamento HTTP fino: serializadores, helpers de domínio e builders de
payload vivem em ``control.services.synthetic_plan_service``.
"""
from __future__ import annotations

import uuid
from urllib.parse import parse_qs
from control.routes.route_helpers import parse_int, write_json
from control.routes.journey_routes import handle_journey_get_route, handle_journey_post_route
from control.server_support import read_json, validate_source_path
from control.services.synthetic_plan_service import (
    analyze_source_payload,
    create_pipeline_run,
    dataset_detail_payload,
    generate_dataset_payload,
    launch_pipeline_async,
    list_entities_payload,
    list_entity_tests_payload,
    list_screens_payload,
    load_roteiro,
    persist_generated_dataset,
    pipeline_status_payload,
    resolve_plan,
    run_simulated_benchmark,
    run_stress_payload,
    screen_detail_payload,
    serialize_dataset,
    serialize_plan,
    serialize_preflight,
    status_payload,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require_source_path(handler, source_dir: str) -> bool:
    """Valida source_dir/menu_file sob DAKOTA_SOURCE_ROOT; responde e retorna False se inválido."""
    allowed, status, message = validate_source_path(source_dir)
    if not allowed:
        write_json(handler, status, {"error": message})
        return False
    return True


def _resolve_plan_or_404(handler, body: dict):
    """Resolve o plano do body; responde 404 e retorna (None, None) se não existir."""
    synthesizer, plan, plans = resolve_plan(
        body.get("source_dir", ""),
        body.get("plan_id", ""),
        screen_filter=body.get("screen_filter", ""),
        entity_filter=body.get("entity_filter", ""),
    )
    if not plan:
        write_json(handler, 404, {
            "error": "plan not found",
            "available_plan_ids": [candidate.plan_id for candidate in plans],
        })
        return None, None
    return synthesizer, plan

# ---------------------------------------------------------------------------
# GET routes
# ---------------------------------------------------------------------------

def handle_synthetic_get_route(handler, parsed_path) -> bool:
    path = parsed_path.path
    qs = parse_qs(parsed_path.query or "")

    # Guard estrutural: todos os GETs de synthetic exigem sessão autenticada.
    user = handler._require()
    if not user:
        return True

    # --- Pipeline status (polling) ---
    if path.startswith("/api/synthetic/pipeline/") and path.endswith("/status"):
        run_id = path.split("/")[4]
        con = handler._db()
        try:
            payload = pipeline_status_payload(con, run_id)
        finally:
            handler._db_release(con)
        if payload is None:
            write_json(handler, 404, {"error": "run not found"})
            return True
        write_json(handler, 200, payload)
        return True

    # --- Screens ---
    if path == "/api/synthetic/screens":
        con = handler._db()
        try:
            result = list_screens_payload(con)
        finally:
            handler._db_release(con)
        write_json(handler, 200, {"screens": result})
        return True

    if path.startswith("/api/synthetic/screens/") and path.count("/") == 4:
        try:
            screen_id = int(path.split("/")[4])
        except (ValueError, IndexError):
            write_json(handler, 404, {"error": "screen not found"})
            return True
        con = handler._db()
        try:
            result = screen_detail_payload(con, screen_id)
        finally:
            handler._db_release(con)
        if not result:
            write_json(handler, 404, {"error": "screen not found"})
            return True
        write_json(handler, 200, result)
        return True

    # --- Datasets ---
    if path == "/api/synthetic/datasets":
        con = handler._db()
        try:
            rows = con.execute(
                "SELECT id, name, screen_id, entity_name, quantity, seed, created_at FROM synthetic_datasets ORDER BY id DESC LIMIT 100"
            ).fetchall()
            result = [dict(r) for r in rows]
        finally:
            handler._db_release(con)
        write_json(handler, 200, {"datasets": result})
        return True

    if path.startswith("/api/synthetic/datasets/") and path.count("/") == 4:
        try:
            ds_id = int(path.split("/")[4])
        except (ValueError, IndexError):
            write_json(handler, 404, {"error": "dataset not found"})
            return True
        con = handler._db()
        try:
            result = dataset_detail_payload(con, ds_id)
        finally:
            handler._db_release(con)
        if not result:
            write_json(handler, 404, {"error": "dataset not found"})
            return True
        write_json(handler, 200, result)
        return True

    # --- Entities ---
    if path == "/api/synthetic/entities":
        con = handler._db()
        try:
            result = list_entities_payload(con)
        finally:
            handler._db_release(con)
        write_json(handler, 200, {"entities": result})
        return True

    # --- Journeys, error-patterns, diff → delegado para journey_routes ---
    if path.startswith("/api/synthetic/journeys") or path in ("/api/synthetic/error-patterns", "/api/synthetic/diff"):
        # Rewrite path prefix: /api/synthetic/journeys → /api/journeys
        rewritten = path.replace("/api/synthetic/journeys", "/api/journeys", 1)
        rewritten = rewritten.replace("/api/synthetic/error-patterns", "/api/journeys/error-patterns", 1)
        rewritten = rewritten.replace("/api/synthetic/diff", "/api/journeys/diff", 1)
        from urllib.parse import urlparse as _urlparse
        fake_parsed = _urlparse(rewritten + ("?" + (parsed_path.query or "") if parsed_path.query else ""))
        return handle_journey_get_route(handler, fake_parsed)

    # --- Roteiro de jornada (RoteiroSynthesizer) ---
    if path.startswith("/api/synthetic/roteiro/") and path.count("/") == 4:
        journey_id = path.split("/")[4]
        fmt = (qs.get("format") or ["json"])[0]
        con = handler._db()
        try:
            route = load_roteiro(con, journey_id)
            if route is None:
                write_json(handler, 404, {"error": "journey not found"})
                return True
            if fmt == "md":
                handler.send_response(200)
                handler.send_header("Content-Type", "text/markdown; charset=utf-8")
                handler.end_headers()
                handler.wfile.write(route.to_markdown().encode("utf-8"))
            else:
                write_json(handler, 200, route.to_dict())
        finally:
            handler._db_release(con)
        return True

    # --- Entity tests (CRUD validations, not business journeys) ---
    if path == "/api/synthetic/entity-tests":
        con = handler._db()
        try:
            result = list_entity_tests_payload(con)
        finally:
            handler._db_release(con)
        write_json(handler, 200, {"entity_tests": result})
        return True

    # --- Status / summary ---
    if path == "/api/synthetic/status":
        con = handler._db()
        try:
            payload = status_payload(con)
        finally:
            handler._db_release(con)
        write_json(handler, 200, payload)
        return True

    # --- Metrics ---
    if path == "/api/synthetic/metrics":
        con = handler._db()
        try:
            from dakota_gateway.synthetic.csv_exporter import MetricsCollector
            metrics = MetricsCollector.collect(con)
        finally:
            handler._db_release(con)
        write_json(handler, 200, metrics)
        return True

    return False

# ---------------------------------------------------------------------------
# POST routes
# ---------------------------------------------------------------------------

def handle_synthetic_post_route(handler, parsed_path, body: dict | None = None) -> bool:
    path = parsed_path.path
    if body is None:
        body = read_json(handler)

    # --- Analyze source ---
    if path == "/api/synthetic/analyze-source":
        user = handler._require(roles={"admin", "operator"})
        if not user:
            return True
        source_dir = body.get("source_dir", "")
        if not source_dir:
            write_json(handler, 400, {"error": "source_dir required"})
            return True
        if not _require_source_path(handler, source_dir):
            return True
        con = handler._db()
        try:
            payload = analyze_source_payload(con, source_dir)
        finally:
            handler._db_release(con)
        write_json(handler, 200, payload)
        return True

    # --- Infer generic data plans ---
    if path == "/api/synthetic/data/plans":
        user = handler._require(roles={"admin", "operator"})
        if not user:
            return True
        source_dir = body.get("source_dir", "")
        if not source_dir:
            write_json(handler, 400, {"error": "source_dir required"})
            return True
        if not _require_source_path(handler, source_dir):
            return True

        from dakota_gateway.synthetic.data_synthesizer import DataSynthesizer

        synthesizer = DataSynthesizer()
        plans = synthesizer.infer_plans(
            source_dir,
            screen_filter=body.get("screen_filter"),
            entity_filter=body.get("entity_filter"),
        )
        write_json(handler, 200, {
            "source_dir": source_dir,
            "plans": [serialize_plan(plan) for plan in plans],
        })
        return True

    # --- Validate a single inferred plan before bulk generation ---
    if path == "/api/synthetic/data/preflight":
        user = handler._require(roles={"admin", "operator"})
        if not user:
            return True
        source_dir = body.get("source_dir", "")
        plan_id = body.get("plan_id", "")
        if not source_dir or not plan_id:
            write_json(handler, 400, {"error": "source_dir and plan_id required"})
            return True
        if not _require_source_path(handler, source_dir):
            return True

        synthesizer, plan = _resolve_plan_or_404(handler, body)
        if not plan:
            return True

        preflight = synthesizer.generate_preflight(
            plan,
            sample_size=parse_int(body.get("sample_size", 5), 5, min_value=1),
            seed=parse_int(body.get("seed", 0), 0, min_value=0),
        )
        write_json(handler, 200, {
            "plan": serialize_plan(plan),
            "preflight": serialize_preflight(preflight),
        })
        return True

    # --- Generate bulk dataset for a validated plan ---
    if path == "/api/synthetic/data/generate-bulk":
        user = handler._require(roles={"admin", "operator"})
        if not user:
            return True
        source_dir = body.get("source_dir", "")
        plan_id = body.get("plan_id", "")
        if not source_dir or not plan_id:
            write_json(handler, 400, {"error": "source_dir and plan_id required"})
            return True
        if not _require_source_path(handler, source_dir):
            return True

        synthesizer, plan = _resolve_plan_or_404(handler, body)
        if not plan:
            return True

        result = synthesizer.generate_bulk(
            plan,
            quantity=parse_int(body.get("quantity", 100), 100, min_value=1),
            seed=parse_int(body.get("seed", 0), 0, min_value=0),
            sample_size=parse_int(body.get("sample_size", 5), 5, min_value=1),
            strict_preflight=bool(body.get("strict_preflight", True)),
        )
        payload = {
            "plan": serialize_plan(plan),
            "blocked": result.blocked,
            "message": result.message,
            "preflight": serialize_preflight(result.preflight) if result.preflight else None,
        }
        if result.dataset:
            con = handler._db()
            try:
                dataset_id = persist_generated_dataset(con, plan, result.dataset)
            finally:
                handler._db_release(con)
            payload["dataset"] = serialize_dataset(
                result.dataset,
                sample_size=parse_int(body.get("preview_size", 5), 5, min_value=1),
            )
            payload["dataset_id"] = dataset_id
        write_json(handler, 200, payload)
        return True

    # --- Generate dataset ---
    if path == "/api/synthetic/generate":
        user = handler._require(roles={"admin", "operator"})
        if not user:
            return True
        screen_name = body.get("screen", body.get("screen_name", ""))
        quantity = parse_int(body.get("quantity", 100), 100, min_value=1)
        seed = parse_int(body.get("seed", 0), 0, min_value=0)
        if not screen_name:
            write_json(handler, 400, {"error": "screen required"})
            return True
        con = handler._db()
        try:
            status, payload = generate_dataset_payload(con, screen_name, quantity, seed)
        finally:
            handler._db_release(con)
        write_json(handler, status, payload)
        return True

    # --- Run stress ---
    if path == "/api/synthetic/stress":
        user = handler._require(roles={"admin", "operator"})
        if not user:
            return True
        journey_id = body.get("scenario", body.get("journey_id", ""))
        concurrency = parse_int(body.get("concurrency", 10), 10, min_value=1)
        ramp_up = parse_int(body.get("ramp_up", body.get("ramp_up_seconds", 5)), 5, min_value=0)
        seed = parse_int(body.get("seed", 0), 0, min_value=0)
        max_sessions = parse_int(body.get("max_sessions", body.get("sessions", 0)), 0, min_value=0) or concurrency * 5

        if not journey_id:
            write_json(handler, 400, {"error": "scenario/journey_id required"})
            return True

        write_json(handler, 200, run_stress_payload(
            journey_id, concurrency, ramp_up, seed, max_sessions,
        ))
        return True

    # --- Stress real (X5): Synthetic → Replay real via replay_control ---
    if path == "/api/synthetic/stress/real":
        user = handler._require(roles={"admin", "operator"})
        if not user:
            return True
        from control.services.synthetic_replay_service import start_synthetic_replay_run
        con = handler._db()
        try:
            result = start_synthetic_replay_run(
                con,
                created_by=int(user["id"]),
                body=body,
                db_path=handler.server.db_path,
                hmac_key=handler.server.runner.hmac_key,
                runner=handler.server.runner,
            )
        finally:
            handler._db_release(con)
        write_json(handler, int(result.get("status_code") or 200), result.get("payload") or {})
        return True

    # --- Journey POST → delegado para journey_routes ---
    if path.startswith("/api/synthetic/journeys"):
        rewritten = path.replace("/api/synthetic/journeys", "/api/journeys", 1)
        from urllib.parse import urlparse as _urlparse
        fake_parsed = _urlparse(rewritten)
        return handle_journey_post_route(handler, fake_parsed, body)

    # --- Pipeline integrado (async com polling) ---
    if path == "/api/synthetic/pipeline":
        user = handler._require(roles={"admin", "operator"})
        if not user:
            return True
        source_dir = body.get("source_dir", "")
        if not source_dir:
            write_json(handler, 400, {"error": "source_dir required"})
            return True
        if not _require_source_path(handler, source_dir):
            return True
        sessions = parse_int(body.get("sessions", 10), 10, min_value=1)
        seed = parse_int(body.get("seed", 0), 0, min_value=0)
        save = not bool(body.get("dry_run", False))

        run_id = str(uuid.uuid4())[:8]

        # Cria registro de execucao
        con = handler._db()
        try:
            create_pipeline_run(con, run_id, source_dir)
        finally:
            handler._db_release(con)

        launch_pipeline_async(
            handler.server.db_pool, run_id, source_dir,
            sessions=sessions, seed=seed, save=save,
        )
        write_json(handler, 202, {"run_id": run_id, "status": "running"})
        return True

    # --- Benchmark ---
    if path == "/api/synthetic/benchmark":
        user = handler._require(roles={"admin", "operator"})
        if not user:
            return True
        name = body.get("name", "")
        journey_id = body.get("journey_id", "")
        envs = body.get("environments", [])
        if not name or not journey_id or not envs:
            write_json(handler, 400, {"error": "name, journey_id, environments required"})
            return True
        report = run_simulated_benchmark(
            name, journey_id, envs,
            concurrency=parse_int(body.get("concurrency", 5), 5, min_value=1),
            iterations=parse_int(body.get("iterations", 3), 3, min_value=1),
            seed=parse_int(body.get("seed", 0), 0, min_value=0),
            timeout_seconds=parse_int(body.get("timeout", 300), 300, min_value=1),
        )
        write_json(handler, 200, report)
        return True

    # --- AI Assessment ---
    if path == "/api/synthetic/assess":
        user = handler._require(roles={"admin", "operator"})
        if not user:
            return True
        from dakota_gateway.assessment import AIAssessment
        assessment = AIAssessment()
        pipeline_result = body.get("pipeline_result", {})
        source_dir = body.get("source_dir", "")
        report = assessment.assess_from_pipeline(pipeline_result, source_dir)
        write_json(handler, 200, assessment.to_dict(report))
        return True

    return False
