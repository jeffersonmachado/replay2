"""API completa de synthetic para o dashboard web.

Cobre todos os comandos CLI:
  analyze-source, screens, generate, stress,
  journey (delegado para journey_routes),
  error-patterns (delegado para journey_routes),
  diff (delegado para journey_routes), report
"""
from __future__ import annotations

import json
import re
import threading
from urllib.parse import parse_qs
from control.routes.route_helpers import write_json
from control.routes.journey_routes import handle_journey_get_route, handle_journey_post_route


def _serialize_plan(plan) -> dict:
    return {
        "plan_id": plan.plan_id,
        "source_dir": plan.source_dir,
        "entity_name": plan.entity_name,
        "warnings": list(plan.warnings),
        "screen": {
            "screen_id": plan.screen.screen_id,
            "title": plan.screen.title,
            "program_name": plan.screen.program_name,
            "fields": [
                {
                    "name": field.name,
                    "datatype": field.datatype,
                    "required": field.required,
                    "unique": field.unique,
                    "lookup": field.lookup,
                    "format": field.format,
                    "min_length": field.min_length,
                    "max_length": field.max_length,
                    "min_value": field.min_value,
                    "max_value": field.max_value,
                    "choices": field.choices,
                }
                for field in plan.screen.fields
            ],
        },
    }


def _serialize_preflight(preflight) -> dict:
    return {
        "plan_id": preflight.plan_id,
        "sample_size": preflight.sample_size,
        "ok": preflight.ok,
        "total_violations": preflight.total_violations,
        "warnings": list(preflight.warnings),
        "records": [
            {
                "record_index": record.record_index,
                "passed": record.passed,
                "data": record.data,
                "violations": [
                    {
                        "field": violation.field,
                        "rule": violation.rule,
                        "value": violation.value,
                        "message": violation.message,
                    }
                    for violation in record.violations
                ],
            }
            for record in preflight.records
        ],
    }


def _serialize_dataset(dataset, *, sample_size: int = 5) -> dict:
    return {
        "name": dataset.name,
        "screen_id": dataset.screen_id,
        "entity_name": dataset.entity_name,
        "quantity": dataset.quantity,
        "seed": dataset.seed,
        "created_at": dataset.created_at,
        "sample": [record.data for record in dataset.records[:sample_size]],
    }


def _persist_generated_dataset(handler, plan, dataset) -> int:
    from dakota_gateway.synthetic.engine import SyntheticEngine
    from dakota_gateway.synthetic.screen_registry import ScreenRegistry

    con = handler._db()
    try:
        registry = ScreenRegistry(con)
        signature = plan.screen.screen_signature or plan.screen.program_name or plan.screen.screen_id or plan.screen.title
        existing = registry.get_screen_by_signature(signature)
        if existing:
            persisted_screen_id = existing.id or 0
        else:
            persisted_screen_id = registry.register_screen(
                screen_signature=signature,
                title=plan.screen.title,
                program_name=plan.screen.program_name,
            )

        if persisted_screen_id and not registry.get_fields_by_screen(persisted_screen_id):
            registry.register_fields_from_schema(persisted_screen_id, plan.screen)

        dataset.screen_id = str(persisted_screen_id or 0)
        engine = SyntheticEngine(db_connection=con)
        return engine.save_dataset(dataset)
    finally:
        handler._db_release(con)


def _resolve_plan(source_dir: str, plan_id: str, screen_filter: str = "", entity_filter: str = ""):
    from dakota_gateway.synthetic.data_synthesizer import DataSynthesizer

    synthesizer = DataSynthesizer()
    plans = synthesizer.infer_plans(
        source_dir,
        screen_filter=screen_filter or None,
        entity_filter=entity_filter or None,
    )
    for plan in plans:
        if plan.plan_id == plan_id:
            return synthesizer, plan, plans
    return synthesizer, None, plans


def _is_valid_ui_entity_name(name: str) -> bool:
    clean = str(name or "").strip()
    if len(clean) < 3:
        return False
    if not re.search(r"[A-Za-z]", clean):
        return False
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{2,31}", clean):
        return False
    if re.search(r'[&"\'()+{}[\]]', clean):
        return False
    if clean.startswith((".", ",", ";", ":", "/", "\\")):
        return False
    if clean.startswith("&(") or clean.startswith("+") or clean.endswith("+"):
        return False
    if "->" in clean or ".." in clean:
        return False
    return True


def _is_placeholder_screen(title: str, program_name: str, field_count: int) -> bool:
    title_clean = str(title or "").strip().lower()
    program_clean = str(program_name or "").strip().lower()
    if field_count > 0:
        return False
    if re.fullmatch(r"tela\s+\d+", title_clean) and re.fullmatch(r"prog\d+", program_clean):
        return True
    if re.fullmatch(r"scr\d+", title_clean) or re.fullmatch(r"prog\d+", title_clean):
        return True
    return False

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_body(handler) -> dict:
    content_len = int(handler.headers.get("Content-Length", 0))
    if content_len:
        return json.loads(handler.rfile.read(content_len))
    return {}

# ---------------------------------------------------------------------------
# GET routes
# ---------------------------------------------------------------------------

def handle_synthetic_get_route(handler, parsed_path) -> bool:
    path = parsed_path.path
    qs = parse_qs(parsed_path.query or "")

    # --- Pipeline status (polling) ---
    if path.startswith("/api/synthetic/pipeline/") and path.endswith("/status"):
        run_id = path.split("/")[4]
        con = handler._db()
        try:
            row = con.execute(
                "SELECT run_id, status, phase, step, progress_pct, entities_found, screens_found, journeys_found, datasets_found, result_json, error_message, started_at, finished_at FROM pipeline_runs WHERE run_id=?",
                (run_id,),
            ).fetchone()
            if not row:
                write_json(handler, 404, {"error": "run not found"})
                return True
            import json as _json
            write_json(handler, 200, {
                "run_id": row[0], "status": row[1], "phase": row[2], "step": row[3],
                "progress_pct": row[4], "entities_found": row[5], "screens_found": row[6],
                "journeys_found": row[7], "datasets_found": row[8],
                "result": _json.loads(row[9]) if row[9] else None,
                "error_message": row[10], "started_at": row[11], "finished_at": row[12],
            })
        finally:
            handler._db_release(con)
        return True

    # --- Screens ---
    if path == "/api/synthetic/screens":
        con = handler._db()
        try:
            from dakota_gateway.synthetic.screen_registry import ScreenRegistry
            reg = ScreenRegistry(con)
            screens = reg.list_screens()
            result = []
            for s in screens:
                fields = reg.get_fields_by_screen(s.id)
                if _is_placeholder_screen(s.title, s.program_name, len(fields)):
                    continue
                result.append({
                    "id": s.id,
                    "signature": s.screen_signature,
                    "title": s.title,
                    "program": s.program_name,
                    "created_at": s.created_at,
                    "fields": [
                        {"name": f.field_name, "datatype": f.datatype, "required": f.required}
                        for f in fields
                    ],
                })
        finally:
            handler._db_release(con)
        write_json(handler, 200, {"screens": result})
        return True

    if path.startswith("/api/synthetic/screens/") and path.count("/") == 4:
        screen_id = int(path.split("/")[4])
        con = handler._db()
        try:
            from dakota_gateway.synthetic.screen_registry import ScreenRegistry
            reg = ScreenRegistry(con)
            schema = reg.get_screen_schema(screen_id)
            if not schema:
                write_json(handler, 404, {"error": "screen not found"})
                return True
            result = {
                "screen_id": schema.screen_id,
                "signature": schema.screen_signature,
                "title": schema.title,
                "program": schema.program_name,
                "fields": [
                    {"name": f.name, "datatype": f.datatype, "required": f.required,
                     "unique": f.unique, "lookup": f.lookup, "format": f.format}
                    for f in schema.fields
                ],
            }
        finally:
            handler._db_release(con)
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
        ds_id = int(path.split("/")[4])
        con = handler._db()
        try:
            from dakota_gateway.synthetic.engine import SyntheticEngine
            engine = SyntheticEngine(db_connection=con)
            dataset = engine.load_dataset(ds_id)
            if not dataset:
                write_json(handler, 404, {"error": "dataset not found"})
                return True
            result = {
                "id": ds_id, "name": dataset.name,
                "screen_id": dataset.screen_id, "entity_name": dataset.entity_name,
                "quantity": dataset.quantity, "seed": dataset.seed,
                "created_at": dataset.created_at,
                "records_sample": [
                    r.data for r in (dataset.records[:5] if dataset.records else [])
                ],
            }
        finally:
            handler._db_release(con)
        write_json(handler, 200, result)
        return True

    # --- Entities ---
    if path == "/api/synthetic/entities":
        con = handler._db()
        try:
            rows = con.execute(
                "SELECT id, name, storage_type, source, created_at FROM source_entities ORDER BY name"
            ).fetchall()
            result = []
            for r in rows:
                fields = con.execute(
                    "SELECT field_name, datatype, required, unique_flag FROM source_entity_fields WHERE entity_id=?",
                    (r["id"],),
                ).fetchall()
                if not _is_valid_ui_entity_name(r["name"]) or not fields:
                    continue
                result.append({
                    "id": r["id"], "name": r["name"],
                    "storage_type": r["storage_type"], "source": r["source"],
                    "created_at": r["created_at"],
                    "fields": [dict(f) for f in fields],
                })
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
        fmt = (parse_qs(parsed_path.query or "").get("format") or ["json"])[0]
        con = handler._db()
        try:
            con.row_factory = __import__('sqlite3').Row
            from dakota_gateway.synthetic.journey_builder import JourneyBuilder
            from dakota_gateway.synthetic.roteiro_synthesizer import RoteiroSynthesizer
            builder = JourneyBuilder(db_connection=con)
            journey = builder.load_journey(journey_id)
            if not journey:
                write_json(handler, 404, {"error": "journey not found"})
                return True
            synth = RoteiroSynthesizer(db_connection=con)
            ref_row = con.execute(
                "SELECT name, source, phases_json FROM reference_routes WHERE journey_id=?",
                (journey_id,)
            ).fetchone()
            reference = None
            if ref_row:
                reference = {
                    "name": ref_row["name"],
                    "source": ref_row["source"],
                    "phases": json.loads(ref_row["phases_json"]),
                }
            route = synth.synthesize(journey=journey, reference_route=reference)
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
            rows = con.execute(
                "SELECT id, entity_name, name, description, tags_csv, created_at FROM entity_tests ORDER BY entity_name"
            ).fetchall()
            result = []
            for r in rows:
                result.append({
                    "id": r["id"],
                    "entity_name": r["entity_name"],
                    "name": r["name"],
                    "description": r["description"],
                    "tags": r["tags_csv"].split(",") if r["tags_csv"] else [],
                    "created_at": r["created_at"],
                })
        finally:
            handler._db_release(con)
        write_json(handler, 200, {"entity_tests": result})
        return True

    # --- Status / summary ---
    if path == "/api/synthetic/status":
        con = handler._db()
        try:
            from dakota_gateway.synthetic.screen_registry import ScreenRegistry

            reg = ScreenRegistry(con)
            screens_count = 0
            for screen in reg.list_screens():
                field_count = len(reg.get_fields_by_screen(screen.id))
                if _is_placeholder_screen(screen.title, screen.program_name, field_count):
                    continue
                screens_count += 1

            entities_count = 0
            rows = con.execute(
                """SELECT se.name, COUNT(sef.id) AS field_count
                   FROM source_entities se
                   LEFT JOIN source_entity_fields sef ON sef.entity_id = se.id
                   GROUP BY se.id, se.name"""
            ).fetchall()
            for row in rows:
                if _is_valid_ui_entity_name(row["name"]) and int(row["field_count"] or 0) > 0:
                    entities_count += 1
            datasets_count = con.execute("SELECT COUNT(*) as c FROM synthetic_datasets").fetchone()["c"]
            journeys_count = con.execute("SELECT COUNT(*) as c FROM journeys").fetchone()["c"]
            entity_tests_count = con.execute("SELECT COUNT(*) as c FROM entity_tests").fetchone()["c"]
        finally:
            handler._db_release(con)
        write_json(handler, 200, {
            "screens": screens_count,
            "entities": entities_count,
            "datasets": datasets_count,
            "journeys": journeys_count,
            "entity_tests": entity_tests_count,
        })
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
        body = _read_body(handler)

    # --- Analyze source ---
    if path == "/api/synthetic/analyze-source":
        user = handler._require()
        if not user:
            return True
        source_dir = body.get("source_dir", "")
        if not source_dir:
            write_json(handler, 400, {"error": "source_dir required"})
            return True
        con = handler._db()
        try:
            from dakota_gateway.synthetic.engine import SyntheticEngine
            engine = SyntheticEngine(db_connection=con)
            result = engine.analyze_source(source_dir)
            engine.register_screens(result)
            entities, _ = (engine.inferencer._parser.parse_all()
                           if engine.inferencer._parser else ([], []))
            engine.save_entities(entities)
        finally:
            handler._db_release(con)
        write_json(handler, 200, {
            "screens": len(result.screens), "entities": len(entities),
            "screens_detail": [
                {"title": s.title, "program": s.program_name, "fields": len(s.fields)}
                for s in result.screens
            ],
            "entities_detail": [
                {"name": e.name, "storage_type": e.storage_type, "fields": len(e.fields)}
                for e in entities
            ],
        })
        return True

    # --- Infer generic data plans ---
    if path == "/api/synthetic/data/plans":
        user = handler._require()
        if not user:
            return True
        source_dir = body.get("source_dir", "")
        if not source_dir:
            write_json(handler, 400, {"error": "source_dir required"})
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
            "plans": [_serialize_plan(plan) for plan in plans],
        })
        return True

    # --- Validate a single inferred plan before bulk generation ---
    if path == "/api/synthetic/data/preflight":
        user = handler._require()
        if not user:
            return True
        source_dir = body.get("source_dir", "")
        plan_id = body.get("plan_id", "")
        if not source_dir or not plan_id:
            write_json(handler, 400, {"error": "source_dir and plan_id required"})
            return True

        synthesizer, plan, plans = _resolve_plan(
            source_dir,
            plan_id,
            screen_filter=body.get("screen_filter", ""),
            entity_filter=body.get("entity_filter", ""),
        )
        if not plan:
            write_json(handler, 404, {
                "error": "plan not found",
                "available_plan_ids": [candidate.plan_id for candidate in plans],
            })
            return True

        preflight = synthesizer.generate_preflight(
            plan,
            sample_size=int(body.get("sample_size", 5)),
            seed=int(body.get("seed", 0)),
        )
        write_json(handler, 200, {
            "plan": _serialize_plan(plan),
            "preflight": _serialize_preflight(preflight),
        })
        return True

    # --- Generate bulk dataset for a validated plan ---
    if path == "/api/synthetic/data/generate-bulk":
        user = handler._require()
        if not user:
            return True
        source_dir = body.get("source_dir", "")
        plan_id = body.get("plan_id", "")
        if not source_dir or not plan_id:
            write_json(handler, 400, {"error": "source_dir and plan_id required"})
            return True

        synthesizer, plan, plans = _resolve_plan(
            source_dir,
            plan_id,
            screen_filter=body.get("screen_filter", ""),
            entity_filter=body.get("entity_filter", ""),
        )
        if not plan:
            write_json(handler, 404, {
                "error": "plan not found",
                "available_plan_ids": [candidate.plan_id for candidate in plans],
            })
            return True

        result = synthesizer.generate_bulk(
            plan,
            quantity=int(body.get("quantity", 100)),
            seed=int(body.get("seed", 0)),
            sample_size=int(body.get("sample_size", 5)),
            strict_preflight=bool(body.get("strict_preflight", True)),
        )
        payload = {
            "plan": _serialize_plan(plan),
            "blocked": result.blocked,
            "message": result.message,
            "preflight": _serialize_preflight(result.preflight) if result.preflight else None,
        }
        if result.dataset:
            dataset_id = _persist_generated_dataset(handler, plan, result.dataset)
            payload["dataset"] = _serialize_dataset(
                result.dataset,
                sample_size=int(body.get("preview_size", 5)),
            )
            payload["dataset_id"] = dataset_id
        write_json(handler, 200, payload)
        return True

    # --- Generate dataset ---
    if path == "/api/synthetic/generate":
        user = handler._require()
        if not user:
            return True
        screen_name = body.get("screen", body.get("screen_name", ""))
        quantity = int(body.get("quantity", 100))
        seed = int(body.get("seed", 0))
        if not screen_name:
            write_json(handler, 400, {"error": "screen required"})
            return True
        con = handler._db()
        try:
            from dakota_gateway.synthetic.engine import SyntheticEngine
            from dakota_gateway.synthetic.screen_registry import ScreenRegistry
            engine = SyntheticEngine(db_connection=con)
            reg = ScreenRegistry(con)

            # Buscar screen por nome ou signature
            screen = reg.get_screen_by_signature(screen_name)
            if not screen:
                row = con.execute(
                    "SELECT id FROM screens WHERE title LIKE ? OR program_name LIKE ? LIMIT 1",
                    (f"%{screen_name}%", f"%{screen_name}%"),
                ).fetchone()
                if row:
                    screen = reg.get_screen_by_id(row["id"])
            if not screen:
                write_json(handler, 404, {"error": f"screen '{screen_name}' not found"})
                return True

            dataset = engine.generate_dataset_by_screen_id(screen.id, quantity=quantity, seed=seed)
            if not dataset:
                write_json(handler, 500, {"error": "generation failed"})
                return True

            ds_id = engine.save_dataset(dataset)
        finally:
            handler._db_release(con)
        write_json(handler, 200, {
            "dataset_id": ds_id,
            "name": dataset.name,
            "quantity": dataset.quantity,
            "screen_id": str(dataset.screen_id),
            "sample": [r.data for r in (dataset.records[:3] if dataset.records else [])],
        })
        return True

    # --- Run stress ---
    if path == "/api/synthetic/stress":
        user = handler._require()
        if not user:
            return True
        journey_id = body.get("scenario", body.get("journey_id", ""))
        concurrency = int(body.get("concurrency", 10))
        ramp_up = int(body.get("ramp_up", body.get("ramp_up_seconds", 5)))
        seed = int(body.get("seed", 0))
        max_sessions = int(body.get("max_sessions", body.get("sessions", 0)) or concurrency * 5)

        if not journey_id:
            write_json(handler, 400, {"error": "scenario/journey_id required"})
            return True

        from dakota_gateway.synthetic.stress_runner import SyntheticStressRunner, SyntheticStressConfig
        config = SyntheticStressConfig(
            journey_id=journey_id, concurrency=concurrency,
            ramp_up_seconds=ramp_up, seed=seed, max_sessions=max_sessions,
        )
        runner = SyntheticStressRunner()
        result = runner.run(config)

        from dakota_gateway.synthetic.homologation_report import HomologationReport
        report = HomologationReport(title=f"Stress: {journey_id}")

        write_json(handler, 200, {
            "status": "completed",
            "total_sessions": result.total_sessions,
            "completed": result.completed,
            "failed": result.failed,
            "errors": result.errors,
            "duration_ms": result.duration_ms,
            "duration_sec": round(result.duration_ms / 1000, 1),
            "analysis": result.aggregate_verification,
            "report": report.generate_json(result),
        })
        return True

    # --- Journey POST → delegado para journey_routes ---
    if path.startswith("/api/synthetic/journeys"):
        rewritten = path.replace("/api/synthetic/journeys", "/api/journeys", 1)
        from urllib.parse import urlparse as _urlparse
        fake_parsed = _urlparse(rewritten)
        return handle_journey_post_route(handler, fake_parsed, body)

    # --- Pipeline integrado (async com polling) ---
    if path == "/api/synthetic/pipeline":
        user = handler._require()
        if not user:
            return True
        source_dir = body.get("source_dir", "")
        if not source_dir:
            write_json(handler, 400, {"error": "source_dir required"})
            return True
        sessions = int(body.get("sessions", 10))
        seed = int(body.get("seed", 0))
        save = not bool(body.get("dry_run", False))

        import uuid, threading, time as _time
        run_id = str(uuid.uuid4())[:8]

        # Cria registro de execucao
        con = handler._db()
        try:
            con.execute(
                """INSERT INTO pipeline_runs (run_id, source_dir, status, phase, step, progress_pct, started_at, created_at)
                   VALUES (?, ?, 'running', 'discovery', 'iniciando...', 0, ?, ?)""",
                (run_id, source_dir, _time.strftime("%Y-%m-%dT%H:%M:%S"), _time.strftime("%Y-%m-%dT%H:%M:%S")),
            )
            con.commit()
        finally:
            handler._db_release(con)

        def _progress_callback(phase: str, step: str, pct: int, extra: dict):
            try:
                c = handler.server.db_pool.acquire()
                c.execute(
                    """UPDATE pipeline_runs SET phase=?, step=?, progress_pct=?,
                       entities_found=COALESCE(?, entities_found),
                       screens_found=COALESCE(?, screens_found),
                       journeys_found=COALESCE(?, journeys_found),
                       datasets_found=COALESCE(?, datasets_found)
                       WHERE run_id=?""",
                    (phase, step, pct,
                     extra.get("entities"), extra.get("screens"),
                     extra.get("journeys"), extra.get("datasets"),
                     run_id),
                )
                c.commit()
                handler.server.db_pool.release(c)
            except Exception:
                pass

        def _run_async():
            try:
                from dakota_gateway.source_analyzer.audit import set_db_pool
                set_db_pool(handler.server.db_pool)
                from dakota_gateway.synthetic.integrated_pipeline import IntegratedPipeline

                c = handler.server.db_pool.acquire()
                pipeline = IntegratedPipeline(db_connection=c)
                result = pipeline.run_and_report(
                    source_dir,
                    save_to_db=save,
                    session_count=sessions,
                    seed=seed,
                    progress_callback=_progress_callback,
                )
                handler.server.db_pool.release(c)

                # Marca como completo
                c2 = handler.server.db_pool.acquire()
                import json as _json
                c2.execute(
                    """UPDATE pipeline_runs SET status='completed', phase='completed',
                       progress_pct=100, step='concluido',
                       entities_found=?, screens_found=?, journeys_found=?, datasets_found=?,
                       result_json=?, finished_at=?
                       WHERE run_id=?""",
                    (result.get("discovery", {}).get("entities", 0),
                     result.get("discovery", {}).get("screens", 0),
                     result.get("journeys", {}).get("generated", 0),
                     result.get("synthetic", {}).get("datasets_generated", 0),
                     _json.dumps(result, ensure_ascii=False),
                     _time.strftime("%Y-%m-%dT%H:%M:%S"),
                     run_id),
                )
                c2.commit()
                handler.server.db_pool.release(c2)
            except Exception as e:
                try:
                    c = handler.server.db_pool.acquire()
                    c.execute(
                        "UPDATE pipeline_runs SET status='failed', error_message=?, finished_at=? WHERE run_id=?",
                        (str(e), _time.strftime("%Y-%m-%dT%H:%M:%S"), run_id),
                    )
                    c.commit()
                    handler.server.db_pool.release(c)
                except Exception:
                    pass

        threading.Thread(target=_run_async, daemon=True).start()
        write_json(handler, 202, {"run_id": run_id, "status": "running"})
        return True

    # --- Benchmark ---
    if path == "/api/synthetic/benchmark":
        user = handler._require()
        if not user:
            return True
        name = body.get("name", "")
        journey_id = body.get("journey_id", "")
        envs = body.get("environments", [])
        if not name or not journey_id or not envs:
            write_json(handler, 400, {"error": "name, journey_id, environments required"})
            return True
        from dakota_gateway.benchmark import BenchmarkOrchestrator, BenchmarkConfig
        config = BenchmarkConfig(
            benchmark_id=f"bench-{int(__import__('time').time())}",
            name=name,
            journey_id=journey_id,
            environments=envs,
            concurrency=int(body.get("concurrency", 5)),
            iterations=int(body.get("iterations", 3)),
            seed=int(body.get("seed", 0)),
            timeout_seconds=int(body.get("timeout", 300)),
        )
        orch = BenchmarkOrchestrator()
        report = orch.run_and_report(config)
        write_json(handler, 200, report)
        return True

    # --- AI Assessment ---
    if path == "/api/synthetic/assess":
        user = handler._require()
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
