"""Rotas da API de jornadas para o dashboard web."""
from __future__ import annotations

import json
from urllib.parse import parse_qs

from dakota_gateway.synthetic.journey_builder import JourneyBuilder
from dakota_gateway.synthetic.journey_inferencer import JourneyInferencer
from dakota_gateway.synthetic.error_detector import ErrorDetector
from control.routes.route_helpers import write_json

def handle_journey_get_route(handler, parsed_path) -> bool:
    path = parsed_path.path

    # GET /api/journeys - listar todas
    if path == "/api/journeys":
        user = handler._require()
        if not user:
            return True
        con = handler._db()
        try:
            builder = JourneyBuilder(db_connection=con)
            journeys = builder.list_journeys()
        finally:
            handler._db_release(con)
        write_json(handler, 200, {"journeys": journeys})
        return True

    # ── Rotas específicas (ANTES do padrao generico {id}) ──

    # GET /api/journeys/infer
    if path == "/api/journeys/infer":
        user = handler._require()
        if not user:
            return True
        qs = parse_qs(parsed_path.query or "")
        source_dir = qs.get("source_dir", [""])[0]
        if not source_dir:
            write_json(handler, 400, {"error": "source_dir required"})
            return True
        inferencer = JourneyInferencer()
        journeys = inferencer.infer_from_source(source_dir)
        con = handler._db()
        try:
            builder = JourneyBuilder(db_connection=con)
            saved = 0
            for j in journeys:
                try:
                    builder.save_journey(j)
                    saved += 1
                except Exception:
                    pass
        finally:
            handler._db_release(con)
        write_json(handler, 200, {
            "inferred": len(journeys), "saved": saved,
            "journeys": [{"journey_id": j.journey_id, "name": j.name, "steps": len(j.steps)} for j in journeys],
        })
        return True

    # GET /api/journeys/error-patterns
    if path == "/api/journeys/error-patterns":
        detector = ErrorDetector()
        patterns = [
            {"type": etype, "severity": sev, "description": desc}
            for _, etype, sev, desc in detector._patterns
        ]
        write_json(handler, 200, {"patterns": patterns})
        return True

    # GET /api/journeys/diff
    if path == "/api/journeys/diff":
        qs = parse_qs(parsed_path.query or "")
        expected = qs.get("expected", [""])[0]
        observed = qs.get("observed", [""])[0]
        from dakota_gateway.synthetic.screen_differ import ScreenDiffer
        diff = ScreenDiffer.diff(expected, observed)
        write_json(handler, 200, ScreenDiffer.to_json(diff))
        return True

    # ── Rotas com suffixo especifico ──

    # GET /api/journeys/{id}/verify
    if path.startswith("/api/journeys/") and path.endswith("/verify"):
        parts = path.split("/")
        if len(parts) < 5:
            write_json(handler, 404, {"error": "invalid path"})
            return True
        journey_id = parts[3]
        qs = parse_qs(parsed_path.query or "")
        session_count = int((qs.get("sessions") or ["5"])[0])
        seed = int((qs.get("seed") or ["0"])[0])
        con = handler._db()
        try:
            builder = JourneyBuilder(db_connection=con)
            journey = builder.load_journey(journey_id)
            if not journey:
                write_json(handler, 404, {"error": "journey not found"})
                return True
            jds = builder.build_journey_dataset(journey, session_count=session_count, seed=seed)
            from dakota_gateway.synthetic.journey_verifier import JourneyVerifier
            from dakota_gateway.synthetic.stress_runner import SyntheticStressRunner
            verifier = JourneyVerifier(db_connection=con)
            all_vr = []
            results = []
            for sess_idx in range(min(session_count, jds.session_count)):
                sim = SyntheticStressRunner._simulate_screens(journey, jds, sess_idx)
                vr = verifier.verify_session(journey, sess_idx, sim)
                all_vr.append(vr)
                results.append({
                    "session": sess_idx, "passed": vr.passed,
                    "steps_passed": vr.steps_passed, "steps_failed": vr.steps_failed,
                    "errors": [{"type": e.error_type, "severity": e.severity} for e in vr.errors],
                })
            analysis = verifier.analyze_errors(all_vr)
        finally:
            handler._db_release(con)
        write_json(handler, 200, {"journey_id": journey_id, "sessions": results, "analysis": analysis})
        return True

    # GET /api/journeys/{id}/report
    if path.startswith("/api/journeys/") and path.endswith("/report"):
        parts = path.split("/")
        if len(parts) < 5:
            write_json(handler, 404, {"error": "invalid path"})
            return True
        journey_id = parts[3]
        qs = parse_qs(parsed_path.query or "")
        sessions = int((qs.get("sessions") or ["5"])[0])
        seed = int((qs.get("seed") or ["0"])[0])
        fmt = (qs.get("format") or ["json"])[0]
        con = handler._db()
        try:
            builder = JourneyBuilder(db_connection=con)
            journey = builder.load_journey(journey_id)
            if not journey:
                write_json(handler, 404, {"error": "journey not found"})
                return True
            from dakota_gateway.synthetic.stress_runner import SyntheticStressRunner, SyntheticStressConfig
            config = SyntheticStressConfig(journey_id=journey_id, concurrency=2, max_sessions=sessions, seed=seed)
            runner = SyntheticStressRunner()
            stress_result = runner.run(config)
            from dakota_gateway.synthetic.homologation_report import HomologationReport
            report = HomologationReport(title=f"Relatorio: {journey.name}")
            if fmt == "html":
                html = report.generate_html(stress_result=stress_result, journey_name=journey.name)
                handler.send_response(200)
                handler.send_header("Content-Type", "text/html; charset=utf-8")
                handler.end_headers()
                handler.wfile.write(html.encode("utf-8"))
                return True
            else:
                write_json(handler, 200, report.generate_json(stress_result))
                return True
        finally:
            handler._db_release(con)

    # ── Rota generica (por ultimo) ──

    # GET /api/journeys/{id} - detalhes
    if path.startswith("/api/journeys/") and path.count("/") == 3:
        user = handler._require()
        if not user:
            return True
        journey_id = path.split("/")[3]
        con = handler._db()
        try:
            builder = JourneyBuilder(db_connection=con)
            journey = builder.load_journey(journey_id)
        finally:
            handler._db_release(con)
        if not journey:
            write_json(handler, 404, {"error": "journey not found"})
        else:
            write_json(handler, 200, journey.to_dict())
        return True

    return False

def handle_journey_post_route(handler, parsed_path, body: dict | None = None) -> bool:
    path = parsed_path.path

    # POST /api/journeys/infer - inferir e salvar
    if path == "/api/journeys/infer":
        user = handler._require()
        if not user:
            return True
        if body is None:
            content_len = int(handler.headers.get("Content-Length", 0))
            body = json.loads(handler.rfile.read(content_len)) if content_len else {}
        source_dir = body.get("source_dir", "")
        if not source_dir:
            write_json(handler, 400, {"error": "source_dir required"})
            return True

        inferencer = JourneyInferencer()
        journeys = inferencer.infer_from_source(source_dir)
        con = handler._db()
        try:
            builder = JourneyBuilder(db_connection=con)
            saved = 0
            for j in journeys:
                try:
                    builder.save_journey(j)
                    saved += 1
                except Exception:
                    pass
        finally:
            handler._db_release(con)

        write_json(handler, 200, {"inferred": len(journeys), "saved": saved})
        return True

    # POST /api/journeys/infer-menu — inferir jornada de arquivo de menu
    if path == "/api/journeys/infer-menu":
        user = handler._require()
        if not user:
            return True
        if body is None:
            content_len = int(handler.headers.get("Content-Length", 0))
            body = json.loads(handler.rfile.read(content_len)) if content_len else {}
        menu_file = body.get("menu_file", "")
        if not menu_file:
            write_json(handler, 400, {"error": "menu_file required"})
            return True
        inferencer = JourneyInferencer()
        journey = inferencer.infer_from_menus(menu_file)
        if not journey:
            write_json(handler, 400, {"error": "could not infer journey from menu"})
            return True
        con = handler._db()
        try:
            builder = JourneyBuilder(db_connection=con)
            jid = builder.save_journey(journey)
        finally:
            handler._db_release(con)
        write_json(handler, 200, {"journey_id": journey.journey_id, "db_id": jid, "steps": len(journey.steps)})
        return True

    # POST /api/journeys/{id}/run — executar jornada
    if path.startswith("/api/journeys/") and path.endswith("/run"):
        parts = path.split("/")
        if len(parts) < 5:
            write_json(handler, 404, {"error": "invalid path"})
            return True
        journey_id = parts[3]
        if body is None:
            content_len = int(handler.headers.get("Content-Length", 0))
            body = json.loads(handler.rfile.read(content_len)) if content_len else {}
        sessions = int(body.get("sessions", 10))
        seed = int(body.get("seed", 0))
        con = handler._db()
        try:
            from dakota_gateway.synthetic.stress_runner import SyntheticStressRunner, SyntheticStressConfig
            builder = JourneyBuilder(db_connection=con)
            journey = builder.load_journey(journey_id)
            if not journey:
                write_json(handler, 404, {"error": "journey not found"})
                return True
            config = SyntheticStressConfig(
                journey_id=journey_id, concurrency=body.get("concurrency", 5),
                max_sessions=sessions, seed=seed,
            )
            runner = SyntheticStressRunner()
            stress_result = runner.run(config)
            jds = builder.build_journey_dataset(journey, session_count=sessions, seed=seed)
            sample_scripts = []
            for sess_idx in range(min(3, sessions)):
                script = builder.generate_replay_script(journey, jds, session_index=sess_idx)
                sample_scripts.append(script[:500])
        finally:
            handler._db_release(con)
        write_json(handler, 200, {
            "journey_id": journey_id,
            "journey_name": journey.name,
            "sessions": sessions,
            "completed": stress_result.completed,
            "failed": stress_result.failed,
            "analysis": stress_result.aggregate_verification,
            "sample_scripts": sample_scripts,
        })
        return True

    return False
