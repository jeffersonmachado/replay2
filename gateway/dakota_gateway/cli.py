from __future__ import annotations

import argparse
import json
import os
import sys
import time

from .cli_commands.catalog import (
    handle_profiles,
    handle_targets,
    register_profiles_parser,
    register_targets_parser,
)
from .cli_commands.runtime import handle_runtime_command, register_runtime_parsers
from .compliance import derive_gateway_route_from_capture, evaluate_run_compliance


def _read_key(path: str) -> bytes:
    with open(path, "rb") as f:
        key = f.read().strip()
    if not key:
        raise SystemExit("hmac key file vazio")
    return key


def _handle_journey(ns, con) -> int:
    """Dispatch para comandos de jornada."""
    import json
    from pathlib import Path
    from .synthetic.journey_inferencer import JourneyInferencer
    from .synthetic.journey_builder import JourneyBuilder

    builder = JourneyBuilder(db_connection=con)

    if ns.journey_cmd == "infer":
        inferencer = JourneyInferencer()
        journeys = inferencer.infer_from_source(ns.source_dir)

        # Também tenta inferir de menus
        menu_dir = ns.source_dir
        import os
        for root, _, files in os.walk(menu_dir):
            for f in files:
                if f.lower().startswith("menu") and f.endswith(".prg"):
                    menu_path = os.path.join(root, f)
                    menu_journey = inferencer.infer_from_menus(menu_path)
                    if menu_journey:
                        journeys.append(menu_journey)

        saved = 0
        for j in journeys:
            try:
                builder.save_journey(j)
                saved += 1
            except Exception:
                pass

        print(json.dumps({
            "journeys_inferred": len(journeys),
            "journeys_saved": saved,
            "journeys_detail": [
                {"journey_id": j.journey_id, "name": j.name, "steps": len(j.steps), "category": j.category}
                for j in journeys
            ],
        }, ensure_ascii=False, indent=2))
        return 0

    elif ns.journey_cmd == "infer-menu":
        inferencer = JourneyInferencer()
        journey = inferencer.infer_from_menus(ns.menu_file)
        if not journey:
            print("Erro: não foi possível inferir jornada do menu", file=sys.stderr)
            return 2
        jid = builder.save_journey(journey)
        print(json.dumps({"journey_id": journey.journey_id, "db_id": jid, "steps": len(journey.steps)}, ensure_ascii=False, indent=2))
        return 0

    elif ns.journey_cmd == "template":
        from .synthetic.journey_synthesizer import JourneySynthesizer
        syn = JourneySynthesizer()
        template = syn.from_capture(
            capture_path=Path(ns.capture),
            source_dir=Path(ns.source_dir),
            name=ns.name or None,
        )
        out_path = Path(ns.out) if ns.out else Path(ns.capture).with_suffix(".template.json")
        syn.save_template(template, out_path)
        print(json.dumps({
            "journey_id": template.journey_id,
            "name": template.name,
            "entities_involved": template.entities_involved,
            "steps": len(template.steps),
            "template_path": str(out_path),
        }, ensure_ascii=False, indent=2))
        return 0

    elif ns.journey_cmd == "synthesize":
        from .synthetic.journey_synthesizer import JourneySynthesizer
        syn = JourneySynthesizer()

        if ns.template:
            template = syn.load_template(Path(ns.template))
        elif ns.capture and ns.source_dir:
            template = syn.from_capture(
                capture_path=Path(ns.capture),
                source_dir=Path(ns.source_dir),
                name=ns.name or None,
            )
        else:
            print("Erro: informe --template ou --capture + --source-dir", file=sys.stderr)
            return 2

        result = syn.synthesize(
            template=template,
            samples=ns.samples,
            out_dir=Path(ns.out),
            seed=ns.seed,
        )
        print(json.dumps({
            "journey_id": result.journey_id,
            "name": result.name,
            "samples": result.samples,
            "generated_sessions": result.generated_sessions,
            "entities_involved": result.entities_involved,
            "mapped_inputs": result.mapped_inputs,
            "command_inputs": result.command_inputs,
            "unmapped_inputs": result.unmapped_inputs,
            "template_path": result.template_path,
            "dataset_path": result.dataset_path,
            "sessions_dir": result.sessions_dir,
            "report_path": result.report_path,
            "warnings": result.warnings,
        }, ensure_ascii=False, indent=2))
        return 0

    elif ns.journey_cmd == "stress":
        from .synthetic.journey_synthesizer import JourneySynthesizer
        syn = JourneySynthesizer()
        result = syn.simulate_stress(
            sessions_dir=Path(ns.sessions_dir),
            concurrency=ns.concurrency,
        )
        output = json.dumps(result, ensure_ascii=False, indent=2)
        if ns.out:
            Path(ns.out).write_text(output, encoding="utf-8")
            sys.stderr.write(f"Relatorio salvo: {ns.out}\n")
        else:
            print(output)
        return 0

    elif ns.journey_cmd == "validate":
        from .synthetic.journey_synthesizer import JourneySynthesizer
        syn = JourneySynthesizer()
        template = syn.load_template(Path(ns.template))
        result = syn.validate_sessions(
            sessions_dir=Path(ns.sessions_dir),
            template=template,
        )
        output = json.dumps(result, ensure_ascii=False, indent=2)
        if ns.out:
            Path(ns.out).write_text(output, encoding="utf-8")
            sys.stderr.write(f"Relatorio salvo: {ns.out}\n")
        else:
            print(output)
        return 0

    elif ns.journey_cmd == "replay":
        from .synthetic.dry_run_replay import DryRunReplay
        runner = DryRunReplay(input_delay_ms=ns.delay_ms)
        output = runner.replay_to_json(Path(ns.sessions_dir))
        if ns.out:
            Path(ns.out).write_text(output, encoding="utf-8")
            sys.stderr.write(f"Relatorio salvo: {ns.out}\n")
        else:
            print(output)
        return 0

    elif ns.journey_cmd == "list":
        journeys = builder.list_journeys()
        for j in journeys:
            print(json.dumps(j, ensure_ascii=False))
        return 0

    elif ns.journey_cmd == "show":
        journey = builder.load_journey(ns.journey_id)
        if not journey:
            print(f"Erro: jornada '{ns.journey_id}' não encontrada", file=sys.stderr)
            return 2
        print(json.dumps(journey.to_dict(), ensure_ascii=False, indent=2))
        return 0

    elif ns.journey_cmd == "run":
        journey = builder.load_journey(ns.journey_id)
        if not journey:
            print(f"Erro: jornada '{ns.journey_id}' não encontrada", file=sys.stderr)
            return 2

        jds = builder.build_journey_dataset(journey, session_count=ns.sessions, seed=ns.seed)

        if ns.output_script:
            all_scripts = []
            for sess_idx in range(min(ns.sessions, jds.session_count)):
                script = builder.generate_replay_script(journey, jds, session_index=sess_idx)
                all_scripts.append(script)

            with open(ns.output_script, "w", encoding="utf-8") as f:
                f.write(f"# Jornada: {journey.name}\n")
                f.write(f"# Sessões: {ns.sessions} | Seed: {ns.seed}\n")
                f.write(f"# Gerado em: {__import__('datetime').datetime.now().isoformat()}\n")
                f.write("\n=== SESSIONS ===\n\n")
                for i, script in enumerate(all_scripts):
                    f.write(f"# ===== SESSÃO {i+1} =====\n")
                    f.write(script)
                    f.write("\n")

        # Mostrar resumo
        sample_session = builder.generate_replay_script(journey, jds, session_index=0) if jds.session_count > 0 else ""

        print(json.dumps({
            "journey_id": journey.journey_id,
            "journey_name": journey.name,
            "sessions": jds.session_count,
            "seed": jds.seed,
            "steps": len(journey.steps),
            "sample_session_0": sample_session[:500] if sample_session else "",
        }, ensure_ascii=False, indent=2))
        return 0

    return 1


def _handle_schedule(ns, con) -> int:
    """Dispatch para comandos de schedule."""
    import json
    from .synthetic.scheduler import Scheduler, ScheduleConfig

    scheduler = Scheduler(db_path=ns.db or "")

    if ns.schedule_cmd == "add":
        import uuid
        config = ScheduleConfig(
            schedule_id=str(uuid.uuid4())[:8],
            journey_id=ns.journey_id,
            name=ns.name,
            interval_hours=ns.interval_hours,
            session_count=ns.sessions,
        )
        sid = scheduler.add_schedule(config)
        print(json.dumps({"schedule_id": sid, "name": ns.name}, ensure_ascii=False))
        return 0

    elif ns.schedule_cmd == "list":
        schedules = scheduler.list_schedules()
        for s in schedules:
            print(json.dumps(s, ensure_ascii=False, default=str))
        return 0

    elif ns.schedule_cmd == "run":
        result = scheduler.run_schedule(ns.schedule_id)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    elif ns.schedule_cmd == "history":
        history = scheduler.get_run_history(ns.schedule_id)
        for h in history:
            print(json.dumps(h, ensure_ascii=False, default=str))
        return 0

    elif ns.schedule_cmd == "regression":
        regression = scheduler.check_regression(ns.schedule_id, ns.run_id)
        if regression:
            print(json.dumps({
                "is_regression": regression.is_regression,
                "delta_pct": regression.delta_pct,
                "current_rate": regression.success_rate_current,
                "previous_rate": regression.success_rate_previous,
                "summary": regression.summary,
                "new_errors": regression.new_error_types,
                "resolved_errors": regression.resolved_error_types,
            }, ensure_ascii=False, indent=2))
        else:
            print(json.dumps({"error": "no previous run to compare"}))
        return 0

    return 1


def _handle_quickstart(ns, con) -> int:
    """Pipeline completo: analyze → infer → stress → report → junit."""
    import json, time, sys as _sys, os
    from .synthetic.engine import SyntheticEngine
    from .synthetic.journey_inferencer import JourneyInferencer
    from .synthetic.journey_builder import JourneyBuilder
    from .synthetic.stress_runner import SyntheticStressRunner, SyntheticStressConfig
    from .synthetic.homologation_report import HomologationReport
    from .synthetic.junit_exporter import JUnitExporter

    report_dir = ns.report_dir or "/tmp/replay-quickstart"
    os.makedirs(report_dir, exist_ok=True)
    total_start = time.time()

    results = {"steps": [], "modules": {}}

    # Step 1: Analyze
    _sys.stderr.write("[1/5] Analisando código-fonte...\n")
    engine = SyntheticEngine(db_connection=con)
    analyze_result = engine.analyze_source(ns.source_dir)
    engine.register_screens(analyze_result)
    entities, _ = engine.inferencer._parser.parse_all() if engine.inferencer._parser else ([], [])
    engine.save_entities(entities)
    results["steps"].append({"step": "analyze", "screens": len(analyze_result.screens), "entities": len(entities)})
    _sys.stderr.write(f"       {len(analyze_result.screens)} telas, {len(entities)} entidades\n")

    # Step 2: Infer journeys
    _sys.stderr.write("[2/5] Inferindo jornadas...\n")
    inferencer = JourneyInferencer()
    journeys = inferencer.infer_from_source(ns.source_dir)
    builder = JourneyBuilder(db_connection=con)
    saved = 0
    for j in journeys:
        try:
            builder.save_journey(j)
            saved += 1
        except Exception:
            pass
    results["steps"].append({"step": "infer", "journeys": len(journeys), "saved": saved})
    _sys.stderr.write(f"       {len(journeys)} jornadas, {saved} salvas\n")

    # Step 3: Stress top 5 modules
    _sys.stderr.write("[3/5] Executando stress...\n")
    top_journeys = sorted(journeys, key=lambda j: len(j.steps), reverse=True)[:5]

    for j in top_journeys:
        _sys.stderr.write(f"       {j.name}...")
        config = SyntheticStressConfig(
            journey_id=j.journey_id,
            concurrency=ns.concurrency,
            max_sessions=ns.sessions,
            seed=42,
            db_path=ns.db or "",
        )
        runner = SyntheticStressRunner(db_path=ns.db or "")
        stress_result = runner.run(config)
        results["modules"][j.journey_id] = {
            "name": j.name, "sessions": stress_result.total_sessions,
            "completed": stress_result.completed, "failed": stress_result.failed,
            "duration_ms": stress_result.duration_ms,
        }
        _sys.stderr.write(f" {stress_result.completed}/{stress_result.total_sessions} OK\n")

    # Step 4: Reports
    _sys.stderr.write("[4/5] Gerando relatórios...\n")
    for j in top_journeys:
        config = SyntheticStressConfig(journey_id=j.journey_id, concurrency=2, max_sessions=5, seed=42, db_path=ns.db or "")
        runner = SyntheticStressRunner(db_path=ns.db or "")
        stress_result = runner.run(config)
        report = HomologationReport(title=f"Quickstart: {j.name}")
        html_path = os.path.join(report_dir, f"{j.journey_id}.html")
        json_path = os.path.join(report_dir, f"{j.journey_id}.json")
        with open(html_path, "w") as f:
            f.write(report.generate_html(stress_result=stress_result, journey_name=j.name))
        with open(json_path, "w") as f:
            json.dump(report.generate_json(stress_result), f, ensure_ascii=False, indent=2)

    # Step 5: JUnit
    _sys.stderr.write("[5/5] Exportando JUnit XML...\n")
    all_results = []
    for j in top_journeys:
        config = SyntheticStressConfig(journey_id=j.journey_id, concurrency=1, max_sessions=5, seed=42, db_path=ns.db or "")
        runner = SyntheticStressRunner(db_path=ns.db or "")
        all_results.append((j, runner.run(config)))

    xml_path = os.path.join(report_dir, "junit.xml")
    if all_results:
        from .synthetic.stress_runner import StressRunResult
        combined = StressRunResult(
            total_sessions=sum(r.total_sessions for _, r in all_results),
            completed=sum(r.completed for _, r in all_results),
            failed=sum(r.failed for _, r in all_results),
            errors=sum(r.errors for _, r in all_results),
            duration_ms=sum(r.duration_ms for _, r in all_results),
        )
        xml_content = JUnitExporter.export(combined, journey_name="quickstart", threshold_pct=80.0)
        JUnitExporter.save_xml(xml_content, xml_path)

    total_time = round(time.time() - total_start, 1)
    results["total_time_sec"] = total_time
    results["report_dir"] = report_dir
    results["artifacts"] = {
        "html": f"{report_dir}/*.html",
        "json": f"{report_dir}/*.json",
        "junit": xml_path,
    }

    _sys.stderr.write(f"\nPronto! {total_time}s | Relatórios: {report_dir}/\n")
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


def _handle_synthetic(ns) -> int:
    """Dispatch para comandos synthetic."""
    from .state_db import connect as _connect, default_db_path as _default_db_path, init_db as _init_db
    from .synthetic.engine import SyntheticEngine
    from .synthetic.inferencer import SyntheticInferencer

    db = ns.db or _default_db_path()
    con = _connect(db)
    _init_db(con)
    try:
        engine = SyntheticEngine(db_connection=con)

        if ns.synthetic_cmd == "analyze-source":
            result = engine.analyze_source(ns.source_dir)
            engine.register_screens(result)

            entities, _ = engine.inferencer._parser.parse_all() if engine.inferencer._parser else ([], [])
            engine.save_entities(entities)

            print(json.dumps({
                "screens": len(result.screens),
                "schemas": len(result.schemas),
                "entities": len(entities),
                "screens_detail": [
                    {"title": s.title, "program": s.program_name, "fields": len(s.fields)}
                    for s in result.screens
                ],
                "entities_detail": [
                    {"name": e.name, "storage_type": e.storage_type, "fields": len(e.fields)}
                    for e in entities
                ],
            }, ensure_ascii=False, indent=2))
            return 0

        elif ns.synthetic_cmd == "screens":
            if not engine.screen_registry:
                print("Erro: screen_registry nao configurado", file=sys.stderr)
                return 2
            screens = engine.screen_registry.list_screens()
            for s in screens:
                fields = engine.screen_registry.get_fields_by_screen(s.id)
                print(json.dumps({
                    "id": s.id,
                    "signature": s.screen_signature,
                    "title": s.title,
                    "program": s.program_name,
                    "fields": [
                        {"name": f.field_name, "datatype": f.datatype, "required": f.required}
                        for f in fields
                    ],
                }, ensure_ascii=False))
            return 0

        elif ns.synthetic_cmd == "generate":
            # Buscar tela por nome ou signature
            if not engine.screen_registry:
                print("Erro: screen_registry nao configurado", file=sys.stderr)
                return 2
            screen = engine.screen_registry.get_screen_by_signature(ns.screen)
            if not screen:
                # Tentar buscar por título (LIKE)
                row = con.execute(
                    "SELECT id FROM screens WHERE title LIKE ? OR program_name LIKE ? LIMIT 1",
                    (f"%{ns.screen}%", f"%{ns.screen}%"),
                ).fetchone()
                if row:
                    screen = engine.screen_registry.get_screen_by_id(row["id"])
            if not screen:
                print(f"Erro: tela '{ns.screen}' nao encontrada", file=sys.stderr)
                return 2

            dataset = engine.generate_dataset_by_screen_id(
                screen.id, quantity=ns.quantity, seed=ns.seed
            )
            if not dataset:
                print("Erro: falha ao gerar dataset", file=sys.stderr)
                return 2

            ds_id = engine.save_dataset(dataset)
            print(json.dumps({
                "dataset_id": ds_id,
                "name": dataset.name,
                "quantity": dataset.quantity,
                "sample": dataset.records[:3] if dataset.records else [],
            }, ensure_ascii=False, indent=2, default=str))
            return 0

        elif ns.synthetic_cmd == "stress":
            from .synthetic.stress_runner import SyntheticStressRunner, SyntheticStressConfig
            from .synthetic.homologation_report import HomologationReport

            config = SyntheticStressConfig(
                journey_id=ns.scenario,
                dataset_name=ns.dataset,
                concurrency=ns.concurrency,
                ramp_up_seconds=ns.ramp_up,
                db_path=ns.db or "",
            )

            runner = SyntheticStressRunner(db_path=ns.db or "")

            if ns.progress:
                import sys as _sys
                def progress(current, total):
                    pct = int(current / max(1, total) * 100)
                    bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
                    _sys.stderr.write(f"\r[{bar}] {current}/{total} sessões ({pct}%)")
                    _sys.stderr.flush()
                config_dict = vars(config)
                config_dict['on_progress'] = progress

            result = runner.run(config)

            if ns.progress:
                import sys as _sys
                _sys.stderr.write("\n")

            output = {
                "status": "completed",
                "total_sessions": result.total_sessions,
                "completed": result.completed,
                "failed": result.failed,
                "errors": result.errors,
                "duration_ms": result.duration_ms,
            }

            if result.aggregate_verification:
                output["analysis"] = result.aggregate_verification

            print(json.dumps(output, ensure_ascii=False, indent=2))

            # Gerar relatório se solicitado
            if ns.report_html or ns.report_json:
                from .synthetic.homologation_report import HomologationReport
                report = HomologationReport(title=f"Stress: {ns.scenario}")

                if ns.report_html:
                    html = report.generate_html(stress_result=result, journey_name=ns.scenario)
                    with open(ns.report_html, "w", encoding="utf-8") as f:
                        f.write(html)
                    import sys as _sys
                    _sys.stderr.write(f"Relatório HTML salvo: {ns.report_html}\n")

                if ns.report_json:
                    json_report = report.generate_json(result)
                    with open(ns.report_json, "w", encoding="utf-8") as f:
                        json.dump(json_report, f, ensure_ascii=False, indent=2)
                    import sys as _sys
                    _sys.stderr.write(f"Relatório JSON salvo: {ns.report_json}\n")

            return 0

        elif ns.synthetic_cmd == "journey":
            return _handle_journey(ns, con)

        elif ns.synthetic_cmd == "schedule":
            return _handle_schedule(ns, con)

        elif ns.synthetic_cmd == "record":
            from .synthetic.session_recorder import SessionRecorder
            recorder = SessionRecorder()
            session = recorder.from_jsonl(ns.from_jsonl)
            if not session:
                print("Erro: não foi possível ler captura", file=sys.stderr)
                return 2
            journey = recorder.to_journey(session)
            from .synthetic.journey_builder import JourneyBuilder
            builder = JourneyBuilder(db_connection=con)
            jid = builder.save_journey(journey)
            print(json.dumps({
                "journey_id": journey.journey_id, "db_id": jid,
                "name": journey.name, "steps": len(journey.steps),
                "source": session.source,
            }, ensure_ascii=False, indent=2))
            return 0

        elif ns.synthetic_cmd == "explore":
            from .synthetic.screen_explorer import ScreenExplorer
            explorer = ScreenExplorer()
            result = explorer.explore_from_source(ns.source_dir)
            print(json.dumps({
                "total_screens": result.total_screens,
                "screens": [
                    {"screen_id": s.screen_id, "title": s.title,
                     "fields": len(s.fields_detected), "menu_options": len(s.menu_options)}
                    for s in result.screens[:30]
                ],
            }, ensure_ascii=False, indent=2))
            return 0

        elif ns.synthetic_cmd == "export-junit":
            from .synthetic.stress_runner import SyntheticStressRunner, SyntheticStressConfig
            from .synthetic.junit_exporter import JUnitExporter
            config = SyntheticStressConfig(
                journey_id=ns.journey_id, max_sessions=ns.sessions, seed=42, db_path=ns.db or "",
            )
            runner = SyntheticStressRunner(db_path=ns.db or "")
            result = runner.run(config)
            xml_content = JUnitExporter.export(result, journey_name=ns.journey_id, threshold_pct=ns.threshold_pct)
            JUnitExporter.save_xml(xml_content, ns.output)
            import sys as _sys
            _sys.stderr.write(f"JUnit XML salvo: {ns.output}\n")
            print(json.dumps({"output": ns.output, "tests": result.total_sessions, "failures": result.failed}, ensure_ascii=False))
            return 0

        elif ns.synthetic_cmd == "quickstart":
            return _handle_quickstart(ns, con)

        elif ns.synthetic_cmd == "pipeline":
            from .synthetic.integrated_pipeline import IntegratedPipeline
            pipeline = IntegratedPipeline(db_connection=con)
            result = pipeline.run_and_report(
                ns.source_dir,
                save_to_db=not ns.no_save,
                session_count=ns.sessions,
                seed=ns.seed,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0

        elif ns.synthetic_cmd == "benchmark":
            from .benchmark import BenchmarkOrchestrator, BenchmarkConfig
            envs = json.loads(ns.envs)
            config = BenchmarkConfig(
                benchmark_id=f"bench-{int(time.time())}",
                name=ns.name,
                journey_id=ns.journey_id,
                environments=envs,
                concurrency=ns.concurrency,
                iterations=ns.iterations,
                seed=ns.seed,
                timeout_seconds=ns.timeout,
            )
            orch = BenchmarkOrchestrator(db_path=ns.db or "")
            report = orch.run_and_report(config)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0

        elif ns.synthetic_cmd == "assess":
            from .assessment import AIAssessment
            assessment = AIAssessment(db_connection=con)
            pipeline_file = ns.pipeline_file
            if pipeline_file:
                with open(pipeline_file) as f:
                    pipeline_result = json.load(f)
            else:
                pipeline_result = {}
            report = assessment.assess_from_pipeline(pipeline_result, ns.source_dir or "")
            print(json.dumps(assessment.to_dict(report), ensure_ascii=False, indent=2))
            return 0

        elif ns.synthetic_cmd == "diff-quickstart":
            from .synthetic.csv_exporter import QuickstartDiffer
            with open(ns.before) as f:
                before_data = f.read()
            with open(ns.after) as f:
                after_data = f.read()
            diff = QuickstartDiffer.diff(before_data, after_data)
            print(json.dumps({
                "screens_before": diff.screens_before, "screens_after": diff.screens_after,
                "screens_new": diff.screens_new, "screens_removed": diff.screens_removed,
                "entities_new": diff.entities_new, "entities_removed": diff.entities_removed,
                "summary": diff.summary,
            }, ensure_ascii=False, indent=2))
            return 0

        elif ns.synthetic_cmd == "export-csv":
            from .synthetic.csv_exporter import CSVExporter
            from .synthetic.engine import SyntheticEngine
            engine = SyntheticEngine(db_connection=con)
            dataset = engine.load_dataset(ns.dataset_id)
            if not dataset:
                print(f"Erro: dataset {ns.dataset_id} não encontrado", file=sys.stderr)
                return 2
            csv_content = CSVExporter.export_dataset(dataset)
            CSVExporter.save_csv(csv_content, ns.output)
            import sys as _sys
            _sys.stderr.write(f"CSV salvo: {ns.output} ({len(dataset.records)} registros)\n")
            print(json.dumps({"output": ns.output, "records": len(dataset.records)}))
            return 0

        elif ns.synthetic_cmd == "watch":
            from .synthetic.csv_exporter import WatchMode
            import sys as _sys

            def on_change(files):
                import datetime as _dt
                _sys.stderr.write(f"[{_dt.datetime.now().strftime('%H:%M:%S')}] {len(files)} arquivos alterados\n")
                for f in files[:5]:
                    _sys.stderr.write(f"  {f}\n")

            _sys.stderr.write(f"Monitorando {ns.source_dir}... (Ctrl+C para parar)\n")
            watcher = WatchMode(ns.source_dir, on_change)
            try:
                watcher.start()
                import time
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                watcher.stop()
                _sys.stderr.write("\nMonitoramento encerrado.\n")
            return 0

        elif ns.synthetic_cmd == "metrics":
            from .synthetic.csv_exporter import MetricsCollector
            metrics = MetricsCollector.collect(con)
            print(json.dumps(metrics, ensure_ascii=False, indent=2))
            return 0

        elif ns.synthetic_cmd == "knowledge-base":
            return _handle_knowledge_base(ns)

    finally:
        con.close()


def _handle_knowledge_base(ns) -> int:
    """Comando knowledge-base: P2-A Synthetic Knowledge Base completo."""
    from pathlib import Path
    from .source_analyzer.parser import SourceParser
    from .synthetic.business_dataset_planner import BusinessDatasetPlanner
    from .synthetic.synthetic_evidence_report import SyntheticEvidenceReportBuilder
    from .synthetic.data_synthesizer import DataSynthesizer
    from .synthetic.journey_mix import JourneyMixBuilder

    source_dir = ns.source_dir
    if not Path(source_dir).exists():
        print(f"Erro: diretório '{source_dir}' não encontrado", file=sys.stderr)
        return 2

    # ── Discovery ──
    sys.stderr.write(f"Analisando {source_dir}...\n")
    parser = SourceParser(source_dir)
    entities, screens = parser.parse_all()
    sys.stderr.write(f"  Entidades: {len(entities)}  Telas: {len(screens)}\n")

    if not entities:
        print(json.dumps({"error": "nenhuma entidade encontrada", "source_dir": source_dir}))
        return 0

    # ── Knowledge Base ──
    sys.stderr.write("Construindo base de conhecimento P2-A...\n")
    bindings = parser.screen_entity_bindings()
    catalog = parser.program_catalog()
    rels = parser.relationships()
    graph = parser.business_dependency_graph()

    planner = BusinessDatasetPlanner()
    graph_summary = planner.plan_summary(graph)

    sys.stderr.write(f"  Bindings: {len(bindings)}  Programas: {len(catalog._entries)}  "
                     f"Módulos: {len(catalog._by_module)}\n")
    sys.stderr.write(f"  Grafo: {graph.total_entities} entidades, "
                     f"raízes={graph.roots}, folhas={graph.leaves}, profundidade={graph.max_depth}\n")

    # ── Evidence Report ──
    sys.stderr.write("Gerando relatório de evidências...\n")
    evidence_builder = SyntheticEvidenceReportBuilder()
    evidence = evidence_builder.build(
        entities=entities,
        screens_count=len(screens),
        bindings=bindings,
        relationships=rels,
        dependency_graph=graph,
        program_catalog=catalog,
        source_files_count=len(parser._collect_source_files()),
    )

    # ── Synthetic Samples ──
    samples = []
    if ns.samples > 0 and graph.plans:
        sys.stderr.write(f"Gerando {ns.samples} amostras por entidade...\n")
        synthesizer = DataSynthesizer()
        for plan in graph.plans[:10]:  # Limite de 10 entidades
            entity = next((e for e in entities if e.name.upper() == plan.entity_name.upper()), None)
            if not entity:
                continue
            try:
                # Gera via infer_plans
                plans = synthesizer.infer_plans(source_dir, entity_filter=plan.entity_name)
                if plans:
                    result = synthesizer.generate_bulk(
                        plans[0], quantity=ns.samples, seed=42,
                        sample_size=min(ns.samples, 3), strict_preflight=False,
                    )
                    if result.dataset and result.dataset.records:
                        samples.append({
                            "entity": plan.entity_name,
                            "generated": result.generated_count,
                            "sample_records": [r.data for r in result.dataset.records],
                        })
            except Exception as e:
                samples.append({"entity": plan.entity_name, "error": str(e)})

    # ── Journey Mix ──
    mix_builder = JourneyMixBuilder()
    mix_config = JourneyMixBuilder.lojas_basico()
    mix_schedule = mix_builder.build_schedule(mix_config, total_sessions=100)

    # ── Capture Knowledge (--captures-dir) ──
    capture_report: dict = {}
    captures_dir = getattr(ns, "captures_dir", "") or ""
    if captures_dir and Path(captures_dir).exists():
        sys.stderr.write(f"Parametrizando capturas em {captures_dir}...\n")
        try:
            from .synthetic.capture_parametrizer import CaptureParametrizer
            from .synthetic.capture_knowledge_integrator import CaptureKnowledgeIntegrator

            cp = CaptureParametrizer()
            templates = cp.analyze_capture_dir(captures_dir)
            capture_report["total_captures"] = len(templates)
            enriched_list = []
            warnings_list = []

            if templates:
                integrator = CaptureKnowledgeIntegrator()
                for tmpl in templates[:20]:  # Limite 20 capturas
                    try:
                        enriched = integrator.enrich_template(tmpl, entities, bindings)
                        # Extrai screen_mappings detalhado
                        screen_maps = []
                        for sm in enriched.screen_mappings:
                            screen_info = {
                                "screen_signature": sm.screen_signature,
                                "entity_name": sm.entity_name,
                                "operation": sm.operation,
                                "binding_confidence": sm.binding_confidence,
                                "total_inputs": sm.total_inputs,
                                "mapped_inputs": sm.mapped_count,
                                "unmapped_inputs": sm.unmapped_count,
                                "command_inputs": sm.command_count,
                                "matched_fields": sm.matched_fields,
                                "inputs": [
                                    {
                                        "input_index": mi.input_index,
                                        "original_value": mi.original_value,
                                        "original_type": mi.original_type,
                                        "field_name": mi.field_name or None,
                                        "method": mi.method,
                                        "placeholder": mi.placeholder or None,
                                        "confidence": mi.confidence,
                                        "evidence": mi.evidence,
                                    }
                                    for mi in sm.mapped_inputs
                                ],
                            }
                            screen_maps.append(screen_info)
                        enriched_list.append({
                            "capture_source": enriched.capture_source,
                            "session_id": enriched.session_id,
                            "total_inputs": enriched.total_inputs,
                            "mapped_inputs": enriched.mapped_inputs,
                            "unmapped_inputs": enriched.unmapped_inputs,
                            "command_inputs": enriched.command_inputs,
                            "entities_involved": enriched.entities_involved,
                            "screen_mappings": screen_maps,
                        })
                    except Exception as exc:
                        warnings_list.append(f"{tmpl.capture_source}: {exc}")

            capture_report["enriched_captures"] = len(enriched_list)
            capture_report["enriched_list"] = enriched_list[:10]
            capture_report["captures"] = enriched_list[:10]
            if warnings_list:
                capture_report["warnings"] = warnings_list
        except Exception as exc:
            capture_report["error"] = str(exc)
    elif captures_dir:
        capture_report["warning"] = f"diretorio de capturas nao encontrado: {captures_dir}"

    # ── Output ──
    report = {
        "pipeline": "P2-A Synthetic Knowledge Base",
        "source_dir": source_dir,
        "summary": {
            "entities": len(entities),
            "screens": len(screens),
            "screen_entity_bindings": len(bindings),
            "programs_cataloged": len(catalog._entries),
            "modules_detected": len(catalog._by_module),
            "relationships": len(rels.relationships),
            "dependency_graph": graph_summary,
        },
        "bindings": {
            "total": len(bindings),
            "high_confidence": len([b for b in bindings if b.confidence >= 0.75]),
            "medium_confidence": len([b for b in bindings if 0.4 <= b.confidence < 0.75]),
            "low_confidence": len([b for b in bindings if b.confidence < 0.4]),
            "details": [
                {"screen": b.screen_title or b.program_name, "entity": b.entity_name,
                 "operation": b.operation, "confidence": b.confidence}
                for b in bindings[:30]
            ],
        },
        "evidence_report": json.loads(evidence_builder.to_json(evidence)),
        "synthetic_samples": samples,
        "journey_mix": {
            "config": mix_config.name,
            "total_sessions": mix_schedule.total_sessions,
            "distribution": mix_schedule.journey_distribution,
        },
        "capture_knowledge": capture_report,
    }

    output = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    if ns.output:
        Path(ns.output).write_text(output, encoding="utf-8")
        sys.stderr.write(f"Relatório salvo: {ns.output}\n")
    else:
        print(output)

    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="dakota-gateway")
    sub = ap.add_subparsers(dest="cmd", required=True)

    register_runtime_parsers(sub)

    # Control-plane ops
    ap_user = sub.add_parser("user", help="Gerencia usuários do dashboard/control plane")
    ap_user.add_argument("--db", default="")
    ap_user_sub = ap_user.add_subparsers(dest="user_cmd", required=True)
    ap_user_add = ap_user_sub.add_parser("add", help="Cria usuário")
    ap_user_add.add_argument("--username", required=True)
    ap_user_add.add_argument("--password", required=True)
    ap_user_add.add_argument("--role", choices=["admin", "operator", "viewer"], required=True)

    ap_runs = sub.add_parser("runs", help="Opera replay runs (SQLite)")
    ap_runs.add_argument("--db", default="")
    ap_runs.add_argument("--hmac-key-file", required=False, default="")
    ap_runs_sub = ap_runs.add_subparsers(dest="runs_cmd", required=True)
    ap_runs_create = ap_runs_sub.add_parser("create")
    ap_runs_create.add_argument("--created-by", required=True, help="username")
    ap_runs_create.add_argument("--log-dir", required=True)
    ap_runs_create.add_argument("--target-host", default="")
    ap_runs_create.add_argument("--target-user", default="")
    ap_runs_create.add_argument("--target-command", default="")
    ap_runs_create.add_argument("--target-env-id", type=int, default=0)
    ap_runs_create.add_argument("--connection-profile-id", type=int, default=0)
    ap_runs_create.add_argument("--mode", choices=["strict-global", "parallel-sessions"], default="strict-global")
    # load-test options (used when mode=parallel-sessions)
    ap_runs_create.add_argument("--concurrency", type=int, default=0)
    ap_runs_create.add_argument("--ramp-up-per-sec", type=float, default=1.0)
    ap_runs_create.add_argument("--speed", type=float, default=1.0)
    ap_runs_create.add_argument("--jitter-ms", type=int, default=0)
    ap_runs_create.add_argument("--on-checkpoint-mismatch", choices=["continue", "fail-fast"], default="continue")
    ap_runs_create.add_argument("--target-user-pool", default="", help="csv: user1,user2,... (opcional)")
    ap_runs_create.add_argument("--replay-from-seq-global", type=int, default=0)
    ap_runs_create.add_argument("--replay-to-seq-global", type=int, default=0)
    ap_runs_create.add_argument("--replay-session-id", default="")
    ap_runs_create.add_argument("--replay-from-checkpoint-sig", default="")
    ap_runs_create.add_argument("--input-mode", choices=["raw", "deterministic"], default="raw")
    ap_runs_create.add_argument("--on-deterministic-mismatch", choices=["fail-fast", "skip", "send-anyway"], default="fail-fast")
    ap_runs_create.add_argument("--match-mode", choices=["strict", "contains", "regex", "fuzzy"], default="strict")
    ap_runs_create.add_argument("--match-threshold", type=float, default=0.92)
    ap_runs_create.add_argument("--match-ignore-case", action="store_true")

    register_targets_parser(sub)
    register_profiles_parser(sub)

    # Synthetic data generation & source analysis
    ap_synthetic = sub.add_parser("synthetic", help="Geração de massa sintética e análise de fonte")
    ap_synthetic.add_argument("--db", default="")
    ap_synthetic_sub = ap_synthetic.add_subparsers(dest="synthetic_cmd", required=True)

    ap_analyze = ap_synthetic_sub.add_parser("analyze-source", help="Analisar código-fonte do sistema alvo")
    ap_analyze.add_argument("--source-dir", required=True, help="Diretório com código-fonte legado")

    ap_screens = ap_synthetic_sub.add_parser("screens", help="Listar telas descobertas")

    ap_generate = ap_synthetic_sub.add_parser("generate", help="Gerar dataset sintético")
    ap_generate.add_argument("--screen", required=True, help="Nome ou signature da tela")
    ap_generate.add_argument("--quantity", type=int, default=100, help="Quantidade de registros")
    ap_generate.add_argument("--seed", type=int, default=0, help="Seed para reprodutibilidade")

    ap_stress = ap_synthetic_sub.add_parser("stress", help="Executar stress sintético")
    ap_stress.add_argument("--scenario", required=True, help="Nome do cenário/tela")
    ap_stress.add_argument("--dataset", default="", help="Nome ou ID do dataset (opcional)")
    ap_stress.add_argument("--concurrency", type=int, default=10, help="Sessões concorrentes")
    ap_stress.add_argument("--ramp-up", type=int, default=5, help="Ramp-up em segundos")
    ap_stress.add_argument("--progress", action="store_true", help="Mostrar barra de progresso")
    ap_stress.add_argument("--report-html", default="", help="Gerar relatório HTML")
    ap_stress.add_argument("--report-json", default="", help="Gerar relatório JSON")

    # Journey commands
    ap_journey = ap_synthetic_sub.add_parser("journey", help="Gerenciar jornadas (sequências de telas)")
    ap_journey_sub = ap_journey.add_subparsers(dest="journey_cmd", required=True)

    ap_journey_infer = ap_journey_sub.add_parser("infer", help="Inferir jornadas do código-fonte")
    ap_journey_infer.add_argument("--source-dir", required=True, help="Diretório com código-fonte")

    ap_journey_infer_menu = ap_journey_sub.add_parser("infer-menu", help="Inferir jornada de arquivo de menu")
    ap_journey_infer_menu.add_argument("--menu-file", required=True, help="Arquivo de menu (.prg)")

    # Journey — Capture-to-Synthetic
    ap_journey_template = ap_journey_sub.add_parser("template", help="Gerar template de jornada a partir de captura")
    ap_journey_template.add_argument("--capture", required=True, help="Arquivo .jsonl de captura")
    ap_journey_template.add_argument("--source-dir", required=True, help="Diretorio com codigo-fonte")
    ap_journey_template.add_argument("--name", default="", help="Nome da jornada")
    ap_journey_template.add_argument("--out", default="", help="Arquivo de saida do template (.json)")

    ap_journey_synthesize = ap_journey_sub.add_parser("synthesize", help="Sintetizar jornadas a partir de template ou captura")
    ap_journey_synthesize.add_argument("--template", default="", help="Arquivo de template (.json)")
    ap_journey_synthesize.add_argument("--capture", default="", help="Arquivo .jsonl de captura (modo direto)")
    ap_journey_synthesize.add_argument("--source-dir", default="", help="Diretorio com codigo-fonte (modo direto)")
    ap_journey_synthesize.add_argument("--name", default="", help="Nome da jornada (modo direto)")
    ap_journey_synthesize.add_argument("--samples", type=int, default=10, help="Numero de sessoes sinteticas")
    ap_journey_synthesize.add_argument("--out", required=True, help="Diretorio de saida")
    ap_journey_synthesize.add_argument("--seed", type=int, default=42, help="Seed para reproducibilidade")

    ap_journey_stress = ap_journey_sub.add_parser("stress", help="Simular stress nas sessoes sintetizadas")
    ap_journey_stress.add_argument("--sessions-dir", required=True, help="Diretorio com sessoes sintetizadas")
    ap_journey_stress.add_argument("--concurrency", type=int, default=10, help="Concorrencia simulada")
    ap_journey_stress.add_argument("--out", default="", help="Arquivo de saida do relatorio de stress")

    ap_journey_validate = ap_journey_sub.add_parser("validate", help="Validar sessoes sintetizadas contra template")
    ap_journey_validate.add_argument("--sessions-dir", required=True, help="Diretorio com sessoes")
    ap_journey_validate.add_argument("--template", required=True, help="Arquivo de template (.json)")
    ap_journey_validate.add_argument("--out", default="", help="Arquivo de saida do relatorio de validacao")

    ap_journey_replay = ap_journey_sub.add_parser("replay", help="Dry-run replay de sessoes sinteticas")
    ap_journey_replay.add_argument("--sessions-dir", required=True, help="Diretorio com sessoes")
    ap_journey_replay.add_argument("--delay-ms", type=float, default=10.0, help="Delay simulado entre inputs (ms)")
    ap_journey_replay.add_argument("--out", default="", help="Arquivo de saida do relatorio")

    ap_journey_list = ap_journey_sub.add_parser("list", help="Listar jornadas")

    ap_journey_show = ap_journey_sub.add_parser("show", help="Mostrar detalhes de uma jornada")
    ap_journey_show.add_argument("--journey-id", required=True, help="ID da jornada")

    ap_journey_run = ap_journey_sub.add_parser("run", help="Executar jornada com dados sintéticos")
    ap_journey_run.add_argument("--journey-id", required=True, help="ID da jornada")
    ap_journey_run.add_argument("--sessions", type=int, default=10, help="Número de sessões")
    ap_journey_run.add_argument("--seed", type=int, default=0, help="Seed")
    ap_journey_run.add_argument("--output-script", default="", help="Arquivo de saída do script replay")

    # Schedule commands
    ap_schedule = ap_synthetic_sub.add_parser("schedule", help="Agendamento de execuções periódicas")
    ap_schedule_sub = ap_schedule.add_subparsers(dest="schedule_cmd", required=True)

    ap_schedule_add = ap_schedule_sub.add_parser("add", help="Adicionar agendamento")
    ap_schedule_add.add_argument("--name", required=True)
    ap_schedule_add.add_argument("--journey-id", required=True)
    ap_schedule_add.add_argument("--interval-hours", type=int, default=24)
    ap_schedule_add.add_argument("--sessions", type=int, default=10)

    ap_schedule_list = ap_schedule_sub.add_parser("list", help="Listar agendamentos")
    ap_schedule_run = ap_schedule_sub.add_parser("run", help="Executar agendamento agora")
    ap_schedule_run.add_argument("--schedule-id", required=True)
    ap_schedule_history = ap_schedule_sub.add_parser("history", help="Histórico de execuções")
    ap_schedule_history.add_argument("--schedule-id", required=True)
    ap_schedule_regression = ap_schedule_sub.add_parser("regression", help="Verificar regressão")
    ap_schedule_regression.add_argument("--schedule-id", required=True)
    ap_schedule_regression.add_argument("--run-id", type=int, required=True)

    # Record command
    ap_record = ap_synthetic_sub.add_parser("record", help="Converter captura em jornada")
    ap_record.add_argument("--from-jsonl", required=True, help="Arquivo .jsonl de captura")

    # Explore command
    ap_explore = ap_synthetic_sub.add_parser("explore", help="Explorar sistema e descobrir telas")
    ap_explore.add_argument("--source-dir", required=True, help="Diretório de código-fonte")

    # Export JUnit command
    ap_export = ap_synthetic_sub.add_parser("export-junit", help="Exportar resultados como JUnit XML")
    ap_export.add_argument("--journey-id", required=True, help="ID da jornada")
    ap_export.add_argument("--sessions", type=int, default=10)
    ap_export.add_argument("--threshold-pct", type=float, default=90.0)
    ap_export.add_argument("--output", required=True, help="Arquivo XML de saída")

    # Quickstart
    ap_quick = ap_synthetic_sub.add_parser("quickstart", help="Pipeline completo em um comando")
    ap_quick.add_argument("--source-dir", required=True, help="Diretório de código-fonte")
    ap_quick.add_argument("--concurrency", type=int, default=5, help="Sessões concorrentes")
    ap_quick.add_argument("--sessions", type=int, default=20, help="Sessões por módulo")
    ap_quick.add_argument("--report-dir", default="", help="Diretório de saída dos relatórios")

    # Pipeline (Discovery → Journey → Synthetic integrado)
    ap_pipeline = ap_synthetic_sub.add_parser("pipeline", help="Pipeline Discovery→Journey→Synthetic integrado")
    ap_pipeline.add_argument("--source-dir", required=True, help="Diretório de código-fonte")
    ap_pipeline.add_argument("--sessions", type=int, default=10, help="Sessões por dataset")
    ap_pipeline.add_argument("--seed", type=int, default=0)
    ap_pipeline.add_argument("--no-save", action="store_true", help="Não salvar no banco")

    # Benchmark
    ap_bench = ap_synthetic_sub.add_parser("benchmark", help="Benchmark AIX vs Linux")
    ap_bench.add_argument("--name", required=True, help="Nome do benchmark")
    ap_bench.add_argument("--journey-id", required=True, help="ID da jornada")
    ap_bench.add_argument("--envs", required=True, help="JSON: [{\"name\":\"aix\",\"host\":\"...\"},{\"name\":\"linux\",\"host\":\"...\"}]")
    ap_bench.add_argument("--concurrency", type=int, default=5)
    ap_bench.add_argument("--iterations", type=int, default=3)
    ap_bench.add_argument("--seed", type=int, default=0)
    ap_bench.add_argument("--timeout", type=int, default=300)

    # AI Assessment
    ap_assess = ap_synthetic_sub.add_parser("assess", help="AI Assessment completo")
    ap_assess.add_argument("--pipeline-file", default="", help="JSON do pipeline (opcional)")
    ap_assess.add_argument("--source-dir", default="", help="Diretório de código-fonte")

    # Diff
    ap_diff = ap_synthetic_sub.add_parser("diff-quickstart", help="Comparar dois quickstarts")
    ap_diff.add_argument("--before", required=True, help="JSON do quickstart anterior")
    ap_diff.add_argument("--after", required=True, help="JSON do quickstart atual")

    # CSV Export
    ap_csv = ap_synthetic_sub.add_parser("export-csv", help="Exportar dataset como CSV")
    ap_csv.add_argument("--dataset-id", type=int, required=True, help="ID do dataset")
    ap_csv.add_argument("--output", required=True, help="Arquivo CSV de saída")

    # Watch
    ap_watch = ap_synthetic_sub.add_parser("watch", help="Monitorar alterações no código-fonte")
    ap_watch.add_argument("--source-dir", required=True, help="Diretório para monitorar")

    # Metrics
    ap_metrics = ap_synthetic_sub.add_parser("metrics", help="Métricas consolidadas")

    ap_kb = ap_synthetic_sub.add_parser("knowledge-base", help="P2-A: Base de Conhecimento Sintética completa")
    ap_kb.add_argument("--source-dir", required=True, help="Diretório do código-fonte legado (.prg, .sql)")
    ap_kb.add_argument("--output", default="", help="Arquivo JSON de saída (stdout se omitido)")
    ap_kb.add_argument("--samples", type=int, default=3, help="Registros de amostra por entidade")
    ap_kb.add_argument("--captures-dir", default="", help="Diretório de capturas .jsonl para parametrizar")

    # Environment profiles (lab / production / homologation)
    ap_env = sub.add_parser("env-profiles", help="Aplica perfil lab/producao a target environment")
    ap_env.add_argument("profile", choices=["lab", "production", "homologation"],
                        help="Perfil a aplicar")
    ap_env.add_argument("--name", required=True, help="Nome do ambiente")
    ap_env.add_argument("--host", required=True, help="Hostname ou IP")
    ap_env.add_argument("--db", default="", help="Caminho do banco SQLite")
    ap_env.add_argument("--env-id", default="", help="ID do ambiente")
    ap_env.add_argument("--port", type=int, default=22, help="Porta SSH")

    ap_runs_start = ap_runs_sub.add_parser("start")
    ap_runs_start.add_argument("--run-id", type=int, required=True)
    ap_runs_start.add_argument("--hmac-key-file", required=True)

    for name in ["pause", "resume", "cancel", "status", "retry"]:
        p2 = ap_runs_sub.add_parser(name)
        p2.add_argument("--run-id", type=int, required=True)
        if name in ("resume",):
            p2.add_argument("--hmac-key-file", required=True)
        if name in ("retry",):
            p2.add_argument("--created-by", required=True, help="username")

    ns = ap.parse_args(argv)

    if ns.cmd in {"start", "verify", "replay", "capture-session"}:
        return handle_runtime_command(ns, _read_key)

    if ns.cmd == "env-profiles":
        from .cli_commands.env_profiles import get_profile, apply_profile
        from .state_db import connect as _connect, default_db_path as _default_db_path, init_db as _init_db

        db = ns.db or _default_db_path()
        con = _connect(db)
        _init_db(con)
        profile = get_profile(ns.profile)
        if not profile:
            print(f"Perfil '{ns.profile}' nao encontrado", file=sys.stderr)
            return 2
        env_id = ns.env_id or ns.name.lower().replace(" ", "-")
        rid = apply_profile(con, env_id, ns.name, ns.host, profile, port=ns.port)
        con.close()
        print(json.dumps({
            "id": rid, "env_id": env_id, "name": ns.name,
            "profile": ns.profile,
            "gateway_required": profile.get("gateway_required"),
        }, ensure_ascii=False, indent=2))
        return 0

    if ns.cmd == "user":
        from . import auth as _auth
        from .state_db import connect as _connect, default_db_path as _default_db_path, init_db as _init_db, query_one as _q1

        db = ns.db or _default_db_path()
        con = _connect(db)
        _init_db(con)
        try:
            if ns.user_cmd == "add":
                ph = _auth.pbkdf2_hash_password(ns.password)
                con.execute(
                    "INSERT INTO users(username,password_hash,role,created_at_ms) VALUES(?,?,?,?)",
                    (ns.username, ph, ns.role, int(time.time() * 1000)),
                )
                print("OK")
                return 0
        finally:
            con.close()

    if ns.cmd == "runs":
        from .state_db import connect as _connect, default_db_path as _default_db_path, init_db as _init_db, query_one as _q1
        from .replay_control import Runner as _Runner, create_run as _create_run, pause_run as _pause, cancel_run as _cancel, retry_run as _retry, set_run_compliance as _set_run_compliance

        db = ns.db or _default_db_path()
        con = _connect(db)
        _init_db(con)
        try:
            if ns.runs_cmd == "create":
                u = _q1(con, "SELECT id FROM users WHERE username=?", (ns.created_by,))
                if not u:
                    print("Erro: created-by inexistente", file=sys.stderr)
                    return 2
                resolved_host = ns.target_host
                resolved_user = ns.target_user
                resolved_command = ns.target_command
                params = {}
                target_env_id = int(ns.target_env_id or 0) or None
                connection_profile_id = int(ns.connection_profile_id or 0) or None
                target_policy = {}
                if target_env_id:
                    env = _q1(con, "SELECT * FROM target_environments WHERE id=?", (target_env_id,))
                    if not env:
                        print("Erro: target-env-id inexistente", file=sys.stderr)
                        return 2
                    target_policy = dict(env)
                    if not resolved_host:
                        resolved_host = str(env["host"] or "")
                    params["target_environment"] = str(env["env_id"] or "")
                    params["environment"] = str(env["name"] or env["env_id"] or "")
                    params["target_platform"] = str(env["platform"] or "linux")
                    if env["port"]:
                        params["target_port"] = int(env["port"])
                    params["target_transport_hint"] = str(env["transport_hint"] or "ssh")
                if connection_profile_id:
                    profile = _q1(con, "SELECT * FROM connection_profiles WHERE id=?", (connection_profile_id,))
                    if not profile:
                        print("Erro: connection-profile-id inexistente", file=sys.stderr)
                        return 2
                    if not resolved_user:
                        resolved_user = str(profile["username"] or "")
                    if not resolved_command:
                        resolved_command = str(profile["command"] or "")
                    params["connection_profile_id"] = int(profile["id"])
                    params["connection_profile_name"] = str(profile["name"] or "")
                    params["transport"] = str(profile["transport"] or "ssh")
                    if profile["port"]:
                        params["target_port"] = int(profile["port"])
                    if profile["credential_ref"]:
                        params["credential_ref"] = str(profile["credential_ref"])
                    if profile["auth_mode"]:
                        params["auth_mode"] = str(profile["auth_mode"])
                if not resolved_host:
                    print("Erro: target-host inexistente e target-env-id ausente", file=sys.stderr)
                    return 2
                if target_policy and target_policy.get("metadata_json"):
                    try:
                        target_metadata = json.loads(target_policy["metadata_json"] or "{}")
                    except Exception:
                        target_metadata = {}
                    if isinstance(target_metadata, dict):
                        if target_metadata.get("gateway_host"):
                            params.setdefault("gateway_host", str(target_metadata.get("gateway_host")))
                            params.setdefault("gateway_route_mode", "proxyjump")
                        if target_metadata.get("gateway_user"):
                            params.setdefault("gateway_user", str(target_metadata.get("gateway_user")))
                        if target_metadata.get("gateway_port"):
                            params.setdefault("gateway_port", int(target_metadata.get("gateway_port") or 0))
                rid = _create_run(
                    con,
                    created_by=int(u["id"]),
                    log_dir=ns.log_dir,
                    target_host=resolved_host,
                    target_user=resolved_user,
                    target_command=resolved_command,
                    mode=ns.mode,
                    target_env_id=target_env_id,
                    connection_profile_id=connection_profile_id,
                )
                # persist load-test params (if any)
                if ns.mode == "parallel-sessions":
                    if ns.concurrency and ns.concurrency > 0:
                        params["concurrency"] = ns.concurrency
                    params["ramp_up_per_sec"] = ns.ramp_up_per_sec
                    params["speed"] = ns.speed
                    params["jitter_ms"] = ns.jitter_ms
                    params["on_checkpoint_mismatch"] = ns.on_checkpoint_mismatch
                    pool = [p.strip() for p in (ns.target_user_pool or "").split(",") if p.strip()]
                    if pool:
                        params["target_user_pool"] = pool
                if params:
                    con.execute("UPDATE replay_runs SET params_json=? WHERE id=?", (json.dumps(params, ensure_ascii=False), rid))
                if target_policy and target_policy.get("gateway_required") and not str(params.get("gateway_host") or "").strip():
                    params.update(
                        {
                            key: value
                            for key, value in derive_gateway_route_from_capture(ns.log_dir, target_policy=target_policy).items()
                            if value not in (None, "")
                        }
                    )
                    con.execute("UPDATE replay_runs SET params_json=? WHERE id=?", (json.dumps(params, ensure_ascii=False), rid))
                compliance = evaluate_run_compliance(
                    ns.log_dir,
                    target_policy=target_policy,
                    resolved_target={
                        "target_host": resolved_host,
                        "target_user": resolved_user,
                        "target_command": resolved_command,
                    },
                    resolved_params=params,
                )
                _set_run_compliance(con, rid, compliance)
                partial = {}
                if ns.replay_from_seq_global and ns.replay_from_seq_global > 0:
                    partial["replay_from_seq_global"] = ns.replay_from_seq_global
                if ns.replay_to_seq_global and ns.replay_to_seq_global > 0:
                    partial["replay_to_seq_global"] = ns.replay_to_seq_global
                if ns.replay_session_id:
                    partial["replay_session_id"] = ns.replay_session_id
                if ns.replay_from_checkpoint_sig:
                    partial["replay_from_checkpoint_sig"] = ns.replay_from_checkpoint_sig
                params["match_mode"] = ns.match_mode
                params["match_threshold"] = ns.match_threshold
                params["input_mode"] = ns.input_mode
                params["on_deterministic_mismatch"] = ns.on_deterministic_mismatch
                if ns.match_ignore_case:
                    params["match_ignore_case"] = True
                if partial:
                    merged = {}
                    if params:
                        merged.update(params)
                    merged.update(partial)
                    con.execute("UPDATE replay_runs SET params_json=? WHERE id=?", (json.dumps(merged, ensure_ascii=False), rid))
                print(rid)
                return 0

            if ns.runs_cmd == "status":
                r = _q1(con, "SELECT * FROM replay_runs WHERE id=?", (ns.run_id,))
                if not r:
                    print("Erro: run inexistente", file=sys.stderr)
                    return 2
                print(dict(r))
                return 0

            if ns.runs_cmd == "resume":
                # handled after closing connection (foreground runner)
                pass

            if ns.runs_cmd == "pause":
                _pause(con, ns.run_id)
                print("OK")
                return 0
            if ns.runs_cmd == "cancel":
                _cancel(con, ns.run_id)
                print("OK")
                return 0
            if ns.runs_cmd == "retry":
                u = _q1(con, "SELECT id FROM users WHERE username=?", (ns.created_by,))
                if not u:
                    print("Erro: created-by inexistente", file=sys.stderr)
                    return 2
                nid = _retry(con, ns.run_id, created_by=int(u["id"]))
                print(nid)
                return 0
        finally:
            con.close()

        # start/resume execute runner in foreground (control via DB)
        if ns.runs_cmd in ("start", "resume"):
            if not ns.hmac_key_file:
                print("Erro: falta --hmac-key-file", file=sys.stderr)
                return 2
            key = _read_key(ns.hmac_key_file)
            runner = _Runner(db, key)
            con2 = _connect(db)
            _init_db(con2)
            try:
                if ns.runs_cmd == "resume":
                    from .replay_control import resume_run as _rsm
                    _rsm(con2, ns.run_id)
                else:
                    con2.execute("UPDATE replay_runs SET status='running' WHERE id=? AND status='queued'", (ns.run_id,))
                con2.close()
                # run synchronously so it actually completes even without dashboard server
                runner.run_foreground(ns.run_id)
                print("OK")
                return 0
            finally:
                try:
                    con2.close()
                except Exception:
                    pass

    if ns.cmd == "targets":
        return handle_targets(ns)

    if ns.cmd == "profiles":
        return handle_profiles(ns)

    if ns.cmd == "synthetic":
        return _handle_synthetic(ns)

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
