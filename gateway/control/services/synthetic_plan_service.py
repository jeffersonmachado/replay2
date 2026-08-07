"""Regras e payloads do domínio Synthetic (dívida R2).

Concentra os serializadores de plano/dataset/preflight, os helpers de
validação de nomes e os builders de payload usados pelas rotas
``/api/synthetic/*``, mantendo ``routes/synthetic_routes.py`` como
acoplamento HTTP fino (auth, query string, ``write_json``). As funções
recebem a conexão de banco já aberta — quem chama (a rota) gerencia
aquisição/liberação via ``handler._db()``/``handler._db_release()``.
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import time as _time

# ---------------------------------------------------------------------------
# Serializadores de domínio
# ---------------------------------------------------------------------------


def serialize_plan(plan) -> dict:
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


def serialize_preflight(preflight) -> dict:
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


def serialize_dataset(dataset, *, sample_size: int = 5) -> dict:
    return {
        "name": dataset.name,
        "screen_id": dataset.screen_id,
        "entity_name": dataset.entity_name,
        "quantity": dataset.quantity,
        "seed": dataset.seed,
        "created_at": dataset.created_at,
        "sample": [record.data for record in dataset.records[:sample_size]],
    }


def persist_generated_dataset(con, plan, dataset) -> int:
    """Registra a tela do plano (se necessário) e persiste o dataset gerado."""
    from dakota_gateway.synthetic.engine import SyntheticEngine
    from dakota_gateway.synthetic.screen_registry import ScreenRegistry

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


# ---------------------------------------------------------------------------
# Helpers de domínio
# ---------------------------------------------------------------------------


def resolve_plan(source_dir: str, plan_id: str, screen_filter: str = "", entity_filter: str = ""):
    """Infere os planos do source_dir e localiza o plano pedido.

    Retorna ``(synthesizer, plan, plans)``; ``plan`` é None quando o
    ``plan_id`` não existe entre os planos inferidos.
    """
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


def is_valid_ui_entity_name(name: str) -> bool:
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


def is_placeholder_screen(title: str, program_name: str, field_count: int) -> bool:
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
# Payloads das rotas GET
# ---------------------------------------------------------------------------


def pipeline_status_payload(con, run_id: str) -> dict | None:
    """Payload de status de um run do pipeline; None se o run não existe."""
    row = con.execute(
        "SELECT run_id, status, phase, step, progress_pct, entities_found, screens_found, journeys_found, datasets_found, result_json, error_message, started_at, finished_at FROM pipeline_runs WHERE run_id=?",
        (run_id,),
    ).fetchone()
    if not row:
        return None
    return {
        "run_id": row[0], "status": row[1], "phase": row[2], "step": row[3],
        "progress_pct": row[4], "entities_found": row[5], "screens_found": row[6],
        "journeys_found": row[7], "datasets_found": row[8],
        "result": json.loads(row[9]) if row[9] else None,
        "error_message": row[10], "started_at": row[11], "finished_at": row[12],
    }


def list_screens_payload(con) -> list:
    """Lista telas registradas (sem placeholders) com seus campos."""
    from dakota_gateway.synthetic.screen_registry import ScreenRegistry

    reg = ScreenRegistry(con)
    result = []
    for s in reg.list_screens():
        fields = reg.get_fields_by_screen(s.id)
        if is_placeholder_screen(s.title, s.program_name, len(fields)):
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
    return result


def screen_detail_payload(con, screen_id: int) -> dict | None:
    """Schema completo de uma tela; None se não existir."""
    from dakota_gateway.synthetic.screen_registry import ScreenRegistry

    reg = ScreenRegistry(con)
    schema = reg.get_screen_schema(screen_id)
    if not schema:
        return None
    return {
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


def dataset_detail_payload(con, ds_id: int) -> dict | None:
    """Detalhe de um dataset com amostra de registros; None se não existir."""
    from dakota_gateway.synthetic.engine import SyntheticEngine

    engine = SyntheticEngine(db_connection=con)
    dataset = engine.load_dataset(ds_id)
    if not dataset:
        return None
    return {
        "id": ds_id, "name": dataset.name,
        "screen_id": dataset.screen_id, "entity_name": dataset.entity_name,
        "quantity": dataset.quantity, "seed": dataset.seed,
        "created_at": dataset.created_at,
        "records_sample": [
            r.data for r in (dataset.records[:5] if dataset.records else [])
        ],
    }


def list_entities_payload(con) -> list:
    """Lista entidades de origem com nomes válidos para UI e ao menos 1 campo."""
    rows = con.execute(
        "SELECT id, name, storage_type, source, created_at FROM source_entities ORDER BY name"
    ).fetchall()
    result = []
    for r in rows:
        fields = con.execute(
            "SELECT field_name, datatype, required, unique_flag FROM source_entity_fields WHERE entity_id=?",
            (r["id"],),
        ).fetchall()
        if not is_valid_ui_entity_name(r["name"]) or not fields:
            continue
        result.append({
            "id": r["id"], "name": r["name"],
            "storage_type": r["storage_type"], "source": r["source"],
            "created_at": r["created_at"],
            "fields": [dict(f) for f in fields],
        })
    return result


def list_entity_tests_payload(con) -> list:
    """Lista testes de entidade (validações CRUD) com tags já separadas."""
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
    return result


def status_payload(con) -> dict:
    """Resumo de contagens do Synthetic (telas/entidades válidas para UI)."""
    from dakota_gateway.synthetic.screen_registry import ScreenRegistry

    reg = ScreenRegistry(con)
    screens_count = 0
    for screen in reg.list_screens():
        field_count = len(reg.get_fields_by_screen(screen.id))
        if is_placeholder_screen(screen.title, screen.program_name, field_count):
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
        if is_valid_ui_entity_name(row["name"]) and int(row["field_count"] or 0) > 0:
            entities_count += 1
    datasets_count = con.execute("SELECT COUNT(*) as c FROM synthetic_datasets").fetchone()["c"]
    journeys_count = con.execute("SELECT COUNT(*) as c FROM journeys").fetchone()["c"]
    entity_tests_count = con.execute("SELECT COUNT(*) as c FROM entity_tests").fetchone()["c"]
    return {
        "screens": screens_count,
        "entities": entities_count,
        "datasets": datasets_count,
        "journeys": journeys_count,
        "entity_tests": entity_tests_count,
    }


def load_roteiro(con, journey_id: str):
    """Sintetiza o roteiro da jornada (RoteiroSynthesizer); None se não existir."""
    con.row_factory = sqlite3.Row
    from dakota_gateway.synthetic.journey_builder import JourneyBuilder
    from dakota_gateway.synthetic.roteiro_synthesizer import RoteiroSynthesizer

    builder = JourneyBuilder(db_connection=con)
    journey = builder.load_journey(journey_id)
    if not journey:
        return None
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
    return synth.synthesize(journey=journey, reference_route=reference)


# ---------------------------------------------------------------------------
# Payloads das rotas POST
# ---------------------------------------------------------------------------


def analyze_source_payload(con, source_dir: str) -> dict:
    """Analisa o código-fonte, registra telas/entidades e resume o resultado."""
    from dakota_gateway.synthetic.engine import SyntheticEngine

    engine = SyntheticEngine(db_connection=con)
    result = engine.analyze_source(source_dir)
    engine.register_screens(result)
    parser = engine.inferencer._parser
    entities, _ = parser.parse_all() if parser else ([], [])
    engine.save_entities(entities)
    # Persiste os bindings tela→entidade (knowledge base): sem isto, cada
    # consumidor (synthesize, relatórios) re-parseava o fonte inteiro.
    bindings = parser.screen_entity_bindings() if parser else []
    engine.save_bindings(bindings)
    return {
        "screens": len(result.screens), "entities": len(entities),
        "bindings": len(bindings),
        "screens_detail": [
            {"title": s.title, "program": s.program_name, "fields": len(s.fields)}
            for s in result.screens
        ],
        "entities_detail": [
            {"name": e.name, "storage_type": e.storage_type, "fields": len(e.fields)}
            for e in entities
        ],
    }


def generate_dataset_payload(con, screen_name: str, quantity: int, seed: int) -> tuple[int, dict]:
    """Gera e persiste dataset para a tela; retorna ``(status_code, payload)``."""
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
        return 404, {"error": f"screen '{screen_name}' not found"}

    dataset = engine.generate_dataset_by_screen_id(screen.id, quantity=quantity, seed=seed)
    if not dataset:
        return 500, {"error": "generation failed"}

    ds_id = engine.save_dataset(dataset)
    return 200, {
        "dataset_id": ds_id,
        "name": dataset.name,
        "quantity": dataset.quantity,
        "screen_id": str(dataset.screen_id),
        "sample": [r.data for r in (dataset.records[:3] if dataset.records else [])],
    }


def run_stress_payload(journey_id: str, concurrency: int, ramp_up: int,
                       seed: int, max_sessions: int) -> dict:
    """Executa o stress sintético e monta o payload com relatório de homologação."""
    from dakota_gateway.synthetic.stress_runner import SyntheticStressRunner, SyntheticStressConfig

    config = SyntheticStressConfig(
        journey_id=journey_id, concurrency=concurrency,
        ramp_up_seconds=ramp_up, seed=seed, max_sessions=max_sessions,
    )
    runner = SyntheticStressRunner()
    result = runner.run(config)

    from dakota_gateway.synthetic.homologation_report import HomologationReport
    report = HomologationReport(title=f"Stress: {journey_id}")

    return {
        "status": "completed",
        "simulation": result.simulation,
        "total_sessions": result.total_sessions,
        "completed": result.completed,
        "failed": result.failed,
        "errors": result.errors,
        "duration_ms": result.duration_ms,
        "duration_sec": round(result.duration_ms / 1000, 1),
        "analysis": result.aggregate_verification,
        "report": report.generate_json(result),
    }


def create_pipeline_run(con, run_id: str, source_dir: str) -> None:
    """Cria o registro de execução do pipeline (status running)."""
    con.execute(
        """INSERT INTO pipeline_runs (run_id, source_dir, status, phase, step, progress_pct, started_at, created_at)
           VALUES (?, ?, 'running', 'discovery', 'iniciando...', 0, ?, ?)""",
        (run_id, source_dir, _time.strftime("%Y-%m-%dT%H:%M:%S"), _time.strftime("%Y-%m-%dT%H:%M:%S")),
    )
    con.commit()


def launch_pipeline_async(db_pool, run_id: str, source_dir: str, *,
                          sessions: int, seed: int, save: bool) -> None:
    """Dispara o pipeline integrado em thread daemon, atualizando pipeline_runs."""

    def _progress_callback(phase: str, step: str, pct: int, extra: dict):
        try:
            c = db_pool.acquire()
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
            db_pool.release(c)
        except Exception:
            pass

    def _run_async():
        try:
            from dakota_gateway.source_analyzer.audit import set_db_pool
            set_db_pool(db_pool)
            from dakota_gateway.synthetic.integrated_pipeline import IntegratedPipeline

            c = db_pool.acquire()
            pipeline = IntegratedPipeline(db_connection=c)
            result = pipeline.run_and_report(
                source_dir,
                save_to_db=save,
                session_count=sessions,
                seed=seed,
                progress_callback=_progress_callback,
            )
            db_pool.release(c)

            # Marca como completo
            c2 = db_pool.acquire()
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
                 json.dumps(result, ensure_ascii=False),
                 _time.strftime("%Y-%m-%dT%H:%M:%S"),
                 run_id),
            )
            c2.commit()
            db_pool.release(c2)
        except Exception as e:
            try:
                c = db_pool.acquire()
                c.execute(
                    "UPDATE pipeline_runs SET status='failed', error_message=?, finished_at=? WHERE run_id=?",
                    (str(e), _time.strftime("%Y-%m-%dT%H:%M:%S"), run_id),
                )
                c.commit()
                db_pool.release(c)
            except Exception:
                pass

    threading.Thread(target=_run_async, daemon=True).start()


def run_simulated_benchmark(name: str, journey_id: str, environments: list, *,
                            concurrency: int, iterations: int, seed: int,
                            timeout_seconds: int) -> dict:
    """Executa o benchmark sintético legado (simulação determinística por seed).

    A resposta SEMPRE carrega o selo simulation=true em destaque e NUNCA
    inclui recomendação de migração — números sintéticos não sustentam
    decisão. O caminho oficial é o benchmark real (POST /api/benchmarks, §21).
    """
    from dakota_gateway.benchmark import BenchmarkOrchestrator, BenchmarkConfig

    config = BenchmarkConfig(
        benchmark_id=f"bench-{int(_time.time())}",
        name=name,
        journey_id=journey_id,
        environments=environments,
        concurrency=concurrency,
        iterations=iterations,
        seed=seed,
        timeout_seconds=timeout_seconds,
    )
    orch = BenchmarkOrchestrator()
    report = orch.run_and_report(config)
    report["simulation"] = True
    report.pop("recommendation", None)
    for comp in report.get("comparisons") or []:
        if isinstance(comp, dict):
            comp.pop("recommendations", None)
            comp.pop("recommendation", None)
    report["simulation_notice"] = (
        "SIMULACAO deterministica por seed: os numeros NAO sao medicao "
        "real e NAO sustentam decisao de migracao. Use o benchmark real "
        "(POST /api/benchmarks)."
    )
    return report
