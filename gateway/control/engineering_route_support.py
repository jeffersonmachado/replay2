from __future__ import annotations

import json
import sqlite3
from urllib.parse import ParseResult


def _send_json(req, status: int, payload: dict | list) -> None:
    req.send_response(status)
    req.send_header("Content-Type", "application/json; charset=utf-8")
    req.end_headers()
    req.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def _send_html(req, html: str) -> None:
    req.send_response(200)
    req.send_header("Content-Type", "text/html; charset=utf-8")
    req.end_headers()
    req.wfile.write(html.encode("utf-8"))


def handle_engineering_api_get_route(req, p: ParseResult, *, db_acquire, db_release) -> bool:
    if p.path == "/api/business/rules":
        eval_data = {
            "rules_evaluated": 0,
            "gaps": [],
            "flows_coverage": [],
            "message": "Execute o Pipeline primeiro",
        }
        try:
            con = db_acquire()
            con.row_factory = sqlite3.Row
            row = con.execute("SELECT * FROM business_evals ORDER BY id DESC LIMIT 1").fetchone()
            if row:
                eval_data = {
                    "rules_evaluated": row["rules_evaluated"],
                    "rules_ok": row["rules_ok"],
                    "rules_broken": row["rules_broken"],
                    "gaps": json.loads(row["gaps_json"]) if row["gaps_json"] else [],
                    "flows_coverage": json.loads(row["flows_coverage_json"])
                    if row["flows_coverage_json"]
                    else [],
                    "recommendation": row["recommendation"],
                    "source_hash": row["source_hash"],
                    "created_at": row["created_at"],
                }
            else:
                rows = con.execute("SELECT DISTINCT entity_name FROM audit_trails").fetchall()
                if rows:
                    entities = {r["entity_name"] for r in rows}
                    from dakota_gateway.synthetic.business_rule_engine import BusinessRuleEngine

                    engine = BusinessRuleEngine()
                    eval_data = engine.evaluate(entities)
            db_release(con)
        except Exception as exc:
            eval_data = {
                "rules_evaluated": 0,
                "gaps": [],
                "flows_coverage": [],
                "message": str(exc),
            }
        _send_json(req, 200, eval_data)
        return True

    if p.path == "/api/business/gaps":
        gap_data = {"gaps": [], "open_count": 0, "total_count": 0}
        try:
            con = db_acquire()
            con.row_factory = sqlite3.Row
            rows = con.execute(
                "SELECT * FROM business_gaps ORDER BY CASE severity "
                "WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END, created_at DESC"
            ).fetchall()
            gaps_list = []
            for row in rows:
                gaps_list.append(
                    {
                        "id": row["id"],
                        "gap_id": row["gap_id"],
                        "severity": row["severity"],
                        "description": row["description"],
                        "missing_entity": row["missing_entity"],
                        "affected_flow": row["affected_flow"],
                        "impact": row["impact"],
                        "recommendation": row["recommendation"],
                        "suggested_files": json.loads(row["suggested_files"])
                        if row["suggested_files"]
                        else [],
                        "status": row["status"],
                        "resolved_at": row["resolved_at"],
                        "resolved_notes": row["resolved_notes"],
                        "created_at": row["created_at"],
                    }
                )
            gap_data["gaps"] = gaps_list
            gap_data["open_count"] = sum(1 for gap in gaps_list if gap["status"] == "open")
            gap_data["total_count"] = len(gaps_list)
            db_release(con)
        except Exception as exc:
            gap_data = {"gaps": [], "open_count": 0, "total_count": 0, "error": str(exc)}
        _send_json(req, 200, gap_data)
        return True

    if p.path == "/api/journeys/report":
        reports_data = []
        try:
            con = db_acquire()
            rows = con.execute(
                "SELECT journey_id, entity_name, generated, report_json, created_at "
                "FROM journey_reports ORDER BY created_at DESC"
            ).fetchall()
            if hasattr(rows[0], "keys") if rows else False:
                for row in rows:
                    try:
                        report = json.loads(row["report_json"]) if row["report_json"] else {}
                    except Exception:
                        report = {}
                    reports_data.append(
                        {
                            "journey_id": row["journey_id"],
                            "entity_name": row["entity_name"],
                            "generated": bool(row["generated"]),
                            "report": report,
                            "created_at": row["created_at"],
                        }
                    )
            else:
                for row in rows:
                    try:
                        report = json.loads(row[3]) if row[3] else {}
                    except Exception:
                        report = {}
                    reports_data.append(
                        {
                            "journey_id": row[0],
                            "entity_name": row[1],
                            "generated": bool(row[2]),
                            "report": report,
                            "created_at": row[4],
                        }
                    )
            db_release(con)
        except Exception:
            pass
        _send_json(req, 200, {"reports": reports_data})
        return True

    if p.path == "/api/catalog/entities":
        entities_data = []
        try:
            con = db_acquire()
            con.row_factory = lambda _c, row: row
            rows = con.execute(
                "SELECT entity_name, inference_type, final_decision, confidence, evidence_json, created_at "
                "FROM audit_trails ORDER BY id"
            ).fetchall()
            for row in rows:
                try:
                    evidence = json.loads(row[4]) if row[4] else []
                except Exception:
                    evidence = []
                entities_data.append(
                    {
                        "name": row[0],
                        "storage_type": "recital",
                        "source": "",
                        "metadata_json": json.dumps(
                            {
                                "_audit": {
                                    "entity_name": row[0],
                                    "inference_type": row[1],
                                    "final_decision": row[2],
                                    "confidence": row[3],
                                    "evidence": evidence,
                                    "timestamp": row[5],
                                }
                            },
                            ensure_ascii=False,
                        ),
                        "operations": [],
                    }
                )
            db_release(con)
        except Exception:
            pass
        _send_json(req, 200, {"entities": entities_data})
        return True

    return False


def handle_engineering_page_get_route(req, p: ParseResult) -> bool:
    from control.ui_templates import render_page

    page_configs = {
        "/business-rules": {
            "template": "business-rules.html",
            "title": "Dakota Calcados | Regras de Negocio",
            "page_title": "Regras de Negocio — Visao de Processos",
            "page_description": "Validacao orientada a fluxos de negocio: gaps, dependencias e cobertura.",
            "page_kicker": "Engenharia de Validacao",
            "active_menu": "engineering",
            "active_submenu": "engineering_business_rules",
            "state_builder": req._build_business_rules_state,
        },
        "/pipeline": {
            "template": "pipeline.html",
            "title": "Dakota Calcados | Pipeline",
            "page_title": "Pipeline — Discovery → Journey → Synthetic",
            "page_description": "Pipeline integrado de analise de codigo-fonte, geracao de jornadas e dados sinteticos.",
            "page_kicker": "Engenharia de Validacao",
            "active_menu": "engineering",
            "active_submenu": "engineering_pipeline",
            "state_builder": req._build_pipeline_last_state,
        },
        "/audit": {
            "template": "audit.html",
            "title": "Dakota Calcados | Auditoria IA",
            "page_title": "Auditoria de Inferencia IA",
            "page_description": "Trilha de auditoria: como e por que cada entidade foi inferida pela IA.",
            "page_kicker": "Engenharia de Validacao",
            "active_menu": "engineering",
            "active_submenu": "engineering_audit",
            "state_builder": req._build_audit_state,
        },
        "/journeys-report": {
            "template": "journeys-report.html",
            "title": "Dakota Calcados | Relatorio de Jornadas",
            "page_title": "Relatorio de Decisoes — Jornadas",
            "page_description": "Justificativas detalhadas para cada decisao na geracao de jornadas CRUD.",
            "page_kicker": "Engenharia de Validacao",
            "active_menu": "engineering",
            "active_submenu": "engineering_journeys_report",
            "state_builder": req._build_journeys_report_state,
        },
        "/benchmark": {
            "template": "benchmark.html",
            "title": "Dakota Calcados | Benchmark",
            "page_title": "Benchmark — AIX vs Linux",
            "page_description": "Comparacao de performance entre ambientes com mesmas jornadas e massa.",
            "page_kicker": "Engenharia de Validacao",
            "active_menu": "engineering",
            "active_submenu": "engineering_benchmark",
            "state_builder": req._build_pipeline_last_state,
        },
        "/assess": {
            "template": "assess.html",
            "title": "Dakota Calcados | AI Assessment",
            "page_title": "AI Assessment",
            "page_description": "Analise inteligente: garbage collector, gargalos, riscos e recomendacoes.",
            "page_kicker": "Engenharia de Validacao",
            "active_menu": "engineering",
            "active_submenu": "engineering_assess",
            "state_builder": req._build_pipeline_last_state,
        },
    }

    page = page_configs.get(p.path)
    if page is None:
        return False

    user = req._require_page()
    if not user:
        return True

    html = render_page(
        page["template"],
        title=page["title"],
        page_title=page["page_title"],
        page_description=page["page_description"],
        page_kicker=page["page_kicker"],
        active_menu=page["active_menu"],
        active_submenu=page["active_submenu"],
        page_state=page["state_builder"](),
    )
    _send_html(req, html)
    return True
