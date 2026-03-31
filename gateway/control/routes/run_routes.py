from __future__ import annotations

import json
from urllib.parse import parse_qs

from control.services.run_service import (
    apply_run_action,
    create_run_request_payload,
    export_run_report_payload,
    get_run_comparison_payload,
    get_run_compliance_payload,
    get_run_detail_payload,
    get_run_events_payload,
    get_run_failures_payload,
    get_run_report_payload,
    list_runs_payload,
)


def _write_json(handler, status_code: int, payload: dict) -> None:
    handler.send_response(status_code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.end_headers()
    handler.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def _write_text(handler, status_code: int, *, content_type: str, content: str) -> None:
    handler.send_response(status_code)
    handler.send_header("Content-Type", content_type)
    handler.end_headers()
    handler.wfile.write(content.encode("utf-8"))


def handle_run_get_route(handler, parsed_path) -> bool:
    path = parsed_path.path
    if path == "/api/runs":
        user = handler._require()
        if not user:
            return True
        qs = parse_qs(parsed_path.query or "")
        limit = int((qs.get("limit") or ["200"])[0])
        compliance_status_filter = str((qs.get("compliance_status") or [""])[0]).strip().lower()
        con = handler._db()
        try:
            payload = list_runs_payload(con, limit=limit, compliance_status=compliance_status_filter)
        finally:
            handler._db_release(con)
        _write_json(handler, 200, payload)
        return True

    if path.startswith("/api/runs/") and path.count("/") == 3:
        user = handler._require()
        if not user:
            return True
        run_id = int(path.split("/")[3])
        con = handler._db()
        try:
            payload = get_run_detail_payload(con, run_id)
        finally:
            handler._db_release(con)
        if not payload:
            handler.send_response(404)
            handler.end_headers()
            return True
        _write_json(handler, 200, payload)
        return True

    if path.startswith("/api/runs/") and path.endswith("/compliance"):
        user = handler._require()
        if not user:
            return True
        parts = path.split("/")
        if len(parts) < 5:
            handler.send_response(404)
            handler.end_headers()
            return True
        run_id = int(parts[3])
        con = handler._db()
        try:
            payload = get_run_compliance_payload(con, run_id)
        finally:
            handler._db_release(con)
        if not payload:
            handler.send_response(404)
            handler.end_headers()
            return True
        _write_json(handler, 200, payload)
        return True

    if path.startswith("/api/runs/") and path.endswith("/report"):
        user = handler._require()
        if not user:
            return True
        parts = path.split("/")
        if len(parts) < 5:
            handler.send_response(404)
            handler.end_headers()
            return True
        run_id = int(parts[3])
        con = handler._db()
        try:
            payload = get_run_report_payload(con, run_id)
        finally:
            handler._db_release(con)
        if not payload:
            handler.send_response(404)
            handler.end_headers()
            return True
        _write_json(handler, 200, payload)
        return True

    if path.startswith("/api/runs/") and path.endswith("/report/export"):
        user = handler._require()
        if not user:
            return True
        parts = path.split("/")
        if len(parts) < 6:
            handler.send_response(404)
            handler.end_headers()
            return True
        run_id = int(parts[3])
        qs = parse_qs(parsed_path.query or "")
        fmt = str((qs.get("format") or ["md"])[0] or "md").strip().lower()
        baseline_run_id = int((qs.get("baseline_run_id") or ["0"])[0] or 0)
        con = handler._db()
        try:
            try:
                content_type, content = export_run_report_payload(con, run_id, fmt=fmt, baseline_run_id=baseline_run_id)
            except ValueError:
                handler.send_response(404)
                handler.end_headers()
                return True
        finally:
            handler._db_release(con)
        _write_text(handler, 200, content_type=content_type, content=content)
        return True

    if path.startswith("/api/runs/") and path.endswith("/compare"):
        user = handler._require()
        if not user:
            return True
        parts = path.split("/")
        if len(parts) < 5:
            handler.send_response(404)
            handler.end_headers()
            return True
        run_id = int(parts[3])
        qs = parse_qs(parsed_path.query or "")
        baseline_run_id = int((qs.get("baseline_run_id") or ["0"])[0] or 0)
        con = handler._db()
        try:
            payload = get_run_comparison_payload(con, run_id, baseline_run_id=baseline_run_id)
        finally:
            handler._db_release(con)
        if not payload:
            handler.send_response(404)
            handler.end_headers()
            return True
        _write_json(handler, 200, payload)
        return True

    if path.startswith("/api/runs/") and path.endswith("/events"):
        user = handler._require()
        if not user:
            return True
        parts = path.split("/")
        if len(parts) < 5:
            handler.send_response(404)
            handler.end_headers()
            return True
        run_id = int(parts[3])
        con = handler._db()
        try:
            payload = get_run_events_payload(con, run_id)
        finally:
            handler._db_release(con)
        _write_json(handler, 200, payload)
        return True

    if path.startswith("/api/runs/") and path.endswith("/failures"):
        user = handler._require()
        if not user:
            return True
        parts = path.split("/")
        if len(parts) < 5:
            handler.send_response(404)
            handler.end_headers()
            return True
        run_id = int(parts[3])
        con = handler._db()
        try:
            payload = get_run_failures_payload(con, run_id)
        finally:
            handler._db_release(con)
        _write_json(handler, 200, payload)
        return True

    return False


def handle_run_post_route(handler, parsed_path, body: dict) -> bool:
    path = parsed_path.path
    if path == "/api/runs":
        user = handler._require(roles={"admin", "operator"})
        if not user:
            return True
        con = handler._db()
        try:
            payload = create_run_request_payload(con, created_by=int(user["id"]), body=body)
        except ValueError as exc:
            handler.send_response(400)
            handler.send_header("Content-Type", "application/json; charset=utf-8")
            handler.end_headers()
            handler.wfile.write(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False).encode("utf-8"))
            return True
        finally:
            handler._db_release(con)
        _write_json(handler, 200, payload)
        return True

    if path.startswith("/api/runs/"):
        user = handler._require(roles={"admin", "operator"})
        if not user:
            return True
        parts = path.split("/")
        if len(parts) < 5:
            handler.send_response(404)
            handler.end_headers()
            return True
        run_id = int(parts[3])
        action = parts[4]
        con = handler._db()
        try:
            result = apply_run_action(con, run_id=run_id, action=action, body=body, actor=user)
        finally:
            handler._db_release(con)
        if result.get("status_code") == 404:
            handler.send_response(404)
            handler.end_headers()
            return True
        if result.get("start_async"):
            handler.server.runner.start_run_async(run_id)
        if result.get("status_code") == 409:
            _write_json(handler, 409, result.get("payload") or {})
            return True
        payload = result.get("payload")
        if payload is None:
            handler.send_response(200)
            handler.end_headers()
            return True
        _write_json(handler, 200, payload)
        return True

    return False
