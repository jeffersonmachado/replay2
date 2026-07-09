from __future__ import annotations

import hashlib
import json
import re
import sqlite3


def build_business_rules_state(db_acquire, db_release) -> dict:
    state = {"rules_evaluated": 0, "rules_ok": 0, "gaps": [], "flows_coverage": [], "gaps_detail": []}
    try:
        con = db_acquire()
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM business_evals ORDER BY id DESC LIMIT 1").fetchone()
        if row:
            state["rules_evaluated"] = row["rules_evaluated"]
            state["rules_ok"] = row["rules_ok"]
            state["rules_broken"] = row["rules_broken"]
            state["gaps"] = json.loads(row["gaps_json"]) if row["gaps_json"] else []
            # Limita a top 20 fluxos por tamanho e trunca dados para evitar pagina gigante
            all_flows = json.loads(row["flows_coverage_json"]) if row["flows_coverage_json"] else []
            all_flows.sort(key=lambda f: len(f.get("entities_covered", [])), reverse=True)
            trimmed = []
            for f in all_flows[:20]:
                ents = f.get("entities_covered", [])
                steps = f.get("steps", [])
                # Trunca entidades para no maximo 30
                if len(ents) > 30:
                    f["entities_covered"] = ents[:30]
                # Trunca steps para no maximo 30 e encurta why
                if len(steps) > 30:
                    f["steps"] = steps[:30]
                for s in f.get("steps", []):
                    why = s.get("why", "")
                    if len(why) > 80:
                        s["why"] = why[:77] + "..."
                trimmed.append(f)
            state["flows_coverage"] = trimmed
            state["flows_total"] = len(all_flows)
            state["recommendation"] = row["recommendation"]
        gaps_rows = con.execute(
            "SELECT * FROM business_gaps "
            "ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 ELSE 2 END, created_at DESC"
        ).fetchall()
        state["gaps_detail"] = []
        for row in gaps_rows:
            state["gaps_detail"].append({
                "gap_id": row["gap_id"],
                "severity": row["severity"],
                "description": row["description"],
                "missing_entity": row["missing_entity"],
                "affected_flow": row["affected_flow"],
                "impact": row["impact"],
                "recommendation": row["recommendation"],
                "suggested_files": json.loads(row["suggested_files"]) if row["suggested_files"] else [],
                "status": row["status"],
            })
        state["journeys_generated"] = con.execute(
            "SELECT COUNT(*) FROM journey_reports WHERE generated=1"
        ).fetchone()[0]
        # Mapa de entidade -> fonte (.prg) e menu
        entity_source_map = {}
        try:
            for row in con.execute("SELECT name, source FROM source_entities").fetchall():
                entity_source_map[row["name"].lower()] = row["source"] or ""
        except Exception:
            pass
        state["entity_sources"] = entity_source_map
        # Mapa de programa -> menu (telas)
        menu_map = {}
        try:
            for row in con.execute("SELECT program_name, title FROM screens WHERE title != ''").fetchall():
                menu_map[row["program_name"].lower()] = row["title"] or ""
        except Exception:
            pass
        state["menu_map"] = menu_map
        db_release(con)
    except Exception:
        pass
    return state


def build_pipeline_last_state(db_acquire, db_release) -> dict:
    state = {"last_execution": None}
    try:
        con = db_acquire()
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM business_evals ORDER BY id DESC LIMIT 1").fetchone()
        if row:
            state["last_execution"] = {
                "rules_evaluated": row["rules_evaluated"],
                "rules_ok": row["rules_ok"],
                "rules_broken": row["rules_broken"],
                "gaps_count": len(json.loads(row["gaps_json"])) if row["gaps_json"] else 0,
                "recommendation": row["recommendation"],
                "source_hash": row["source_hash"],
                "source_dir": row["source_dir"] if "source_dir" in row.keys() and row["source_dir"] else "",
                "created_at": row["created_at"],
            }
        # Fallback: le source_dir do pipeline_runs se business_evals nao tiver
        if not state["last_execution"] or not state["last_execution"].get("source_dir"):
            pipe_row = con.execute(
                "SELECT source_dir FROM pipeline_runs WHERE status='completed' AND source_dir != '' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if pipe_row and pipe_row["source_dir"]:
                if not state["last_execution"]:
                    state["last_execution"] = {}
                state["last_execution"]["source_dir"] = pipe_row["source_dir"]
                if not state["last_execution"].get("source_hash"):
                    state["last_execution"]["source_hash"] = hashlib.sha256(pipe_row["source_dir"].encode()).hexdigest()[:16]
        state["entities_count"] = con.execute("SELECT COUNT(*) FROM source_entities").fetchone()[0]
        state["screens_count"] = con.execute("SELECT COUNT(*) FROM screens").fetchone()[0]
        state["journeys_count"] = con.execute("SELECT COUNT(*) FROM journey_reports WHERE generated=1").fetchone()[0]
        state["datasets_count"] = con.execute("SELECT COUNT(*) FROM synthetic_datasets").fetchone()[0]
        db_release(con)
    except Exception:
        pass
    return state


def infer_program_purpose(filename: str) -> str:
    name = filename.replace(".prg", "").replace(".dbo", "").upper()
    name = re.sub(r"^[^A-Z0-9]+", "", name)
    if not name:
        return "Programa desconhecido"

    module_map = {
        "CAD": "Cadastro", "COP": "Contas a Pagar", "CRE": "Contas a Receber",
        "FIN": "Financeiro", "FAT": "Faturamento", "PED": "Pedido",
        "EST": "Estoque", "PRD": "Producao", "EXP": "Expedicao",
        "COM": "Compras", "NF": "Nota Fiscal", "CRM": "CRM",
        "RH": "Recursos Humanos", "CTB": "Contabilidade", "ASS": "Assistencia Tecnica",
        "VOIP": "VoIP/Telefonia", "CLI": "Clientes", "CON": "Contratos",
        "VEN": "Vendas", "ORC": "Orcamento", "SYS": "Sistema",
        "AA": "Sistema (core)", "BB": "Sistema (infra)",
    }
    prefix3 = name[:3]
    module = module_map.get(prefix3, "")
    if not module and len(name) >= 2:
        module = module_map.get(name[:2], "")

    digits = re.search(r"(\d{2,})[a-zA-Z]*$", name)
    sub_type = ""
    if digits:
        num = digits.group(1)
        sub_map = {
            "01": "Inclusao/Cadastro", "02": "Alteracao/Edicao",
            "03": "Consulta", "04": "Relatorio", "05": "Exclusao",
            "10": "Principal", "99": "Utilitario",
        }
        sub_type = sub_map.get(num[:2], f"Operacao {num}")

    parts = [part for part in (module, sub_type) if part]
    return " — ".join(parts) if parts else f"Programa {name}"


def build_journeys_report_state(db_acquire, db_release) -> dict:
    state = {"reports": []}
    try:
        con = db_acquire()
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT journey_id, entity_name, generated, report_json, created_at "
            "FROM journey_reports ORDER BY created_at DESC"
        ).fetchall()
        for row in rows:
            try:
                report = json.loads(row["report_json"]) if row["report_json"] else {}
            except Exception:
                report = {}
            state["reports"].append({
                "journey_id": row["journey_id"],
                "entity_name": row["entity_name"],
                "generated": bool(row["generated"]),
                "report": report,
                "created_at": row["created_at"],
            })
        db_release(con)
    except Exception:
        pass
    return state


def build_audit_state(
    db_acquire,
    db_release,
    *,
    local_sistema,
    remote_navigation,
    infer_program_purpose_fn,
) -> dict:
    import os

    state: dict = {
        "entities": [],
        "program_dirs": [],
        "data_dirs": [],
        "local_sistema": local_sistema,
        "remote_navigation": remote_navigation,
    }
    try:
        con = db_acquire()
        con.row_factory = sqlite3.Row

        screen_rows = con.execute(
            "SELECT screen_signature, title, program_name FROM screens"
        ).fetchall()
        screen_title_by_sig: dict[str, str] = {}
        screen_title_by_prog: dict[str, str] = {}
        for row in screen_rows:
            sig = (row["screen_signature"] or "").strip()
            title = (row["title"] or "").strip()
            prog = (row["program_name"] or "").strip()
            if sig and title:
                screen_title_by_sig[sig] = title
            if prog and title:
                screen_title_by_prog[prog.upper()] = title

        storage_rows = con.execute(
            "SELECT name, storage_type, source FROM source_entities"
        ).fetchall()
        entity_storage: dict[str, dict] = {}
        for row in storage_rows:
            entity_storage[row["name"]] = {
                "storage_type": row["storage_type"],
                "source": row["source"],
            }

        entity_sources: dict[str, set[str]] = {}
        entity_fields: dict[str, set[str]] = {}
        program_entities: dict[str, set[str]] = {}
        all_program_dirs: dict[str, dict] = {}

        rows = con.execute(
            "SELECT entity_name, inference_type, final_decision, confidence, "
            "evidence_json, created_at FROM audit_trails ORDER BY id"
        ).fetchall()
        raw_entities: list[dict] = []
        for row in rows:
            try:
                evidence = json.loads(row["evidence_json"]) if row["evidence_json"] else []
            except Exception:
                evidence = []
            entity_name = row["entity_name"]
            raw_entities.append({
                "name": entity_name,
                "inference_type": row["inference_type"],
                "final_decision": row["final_decision"],
                "confidence": row["confidence"],
                "evidence": evidence,
                "created_at": row["created_at"],
            })

            entity_sources.setdefault(entity_name, set())
            entity_fields.setdefault(entity_name, set())

            for item in evidence:
                source_file = (item.get("source_file") or "").strip()
                if source_file:
                    entity_sources[entity_name].add(source_file)
                    program_entities.setdefault(source_file, set()).add(entity_name)

                rule = item.get("rule") or ""
                pattern = item.get("pattern") or ""
                if rule == "ALIAS_ARROW" and "->" in pattern:
                    parts = pattern.split("->")
                    if len(parts) == 2:
                        entity_fields[entity_name].add(parts[1].strip())

        for source_file in sorted(program_entities.keys()):
            parent_dir = os.path.dirname(source_file)
            dir_parts = parent_dir.split("/")
            leaf_dir = dir_parts[-1] if dir_parts else ""
            base_path = "/".join(dir_parts[:-1]) if len(dir_parts) > 1 else parent_dir
            key = f"{base_path}/{leaf_dir}" if leaf_dir else base_path
            if key not in all_program_dirs:
                all_program_dirs[key] = {
                    "path": key,
                    "leaf": leaf_dir,
                    "base_path": base_path,
                    "program_count": 0,
                    "entity_count": 0,
                    "programs": [],
                }
            all_program_dirs[key]["program_count"] += 1
            all_program_dirs[key]["entity_count"] += len(program_entities[source_file])
            all_program_dirs[key]["programs"].append({
                "file": os.path.basename(source_file),
                "entity_count": len(program_entities[source_file]),
            })

        data_dirs_set: set[str] = set()
        for storage in entity_storage.values():
            source = storage.get("source", "")
            if source and os.path.isdir(source):
                data_dirs_set.add(source)
        state["data_dirs"] = sorted(data_dirs_set)
        state["program_dirs"] = sorted(all_program_dirs.values(), key=lambda item: item["path"])

        entity_deps: dict[str, list[dict]] = {}
        for entity_name in entity_sources:
            deps: list[dict] = []
            seen_targets: set[str] = set()
            for source_file in entity_sources.get(entity_name, set()):
                co_entities = program_entities.get(source_file, set()) - {entity_name}
                for other in co_entities:
                    if other not in seen_targets:
                        seen_targets.add(other)
                        deps.append({
                            "target_entity": other,
                            "relationship": "co-ocorrencia em programa",
                            "via_program": os.path.basename(source_file),
                            "via_program_full": source_file,
                        })
            if deps:
                entity_deps[entity_name] = deps

        all_entity_names = set(entity_sources.keys())
        for entity_name in entity_sources:
            for other in all_entity_names:
                if other == entity_name:
                    continue
                for field in entity_fields.get(entity_name, set()):
                    if field.lower() == other.lower() or field.lower().startswith(other.lower()[:6]):
                        deps = entity_deps.setdefault(entity_name, [])
                        already = any(
                            dep["target_entity"] == other and "campo" in dep.get("relationship", "")
                            for dep in deps
                        )
                        if not already:
                            deps.append({
                                "target_entity": other,
                                "relationship": f"campo '{field}' referencia entidade",
                                "via_program": "",
                                "via_program_full": "",
                            })
                        break

        for entity in raw_entities:
            entity_name = entity["name"]
            sources = entity_sources.get(entity_name, set())

            programs = []
            for source_file in sorted(sources):
                basename = os.path.basename(source_file)
                parent = os.path.dirname(source_file)
                dir_leaf = os.path.basename(parent) if parent else ""
                relative_dir = ""
                if "/programas/" in parent:
                    relative_dir = parent.split("/programas/", 1)[-1] if "/programas/" in parent else dir_leaf

                title = screen_title_by_sig.get(basename.replace(".prg", "").upper(), "")
                if not title:
                    title = screen_title_by_prog.get(basename.replace(".prg", "").upper(), "")
                if not title:
                    title = infer_program_purpose_fn(basename)

                co_count = len(program_entities.get(source_file, set()))
                programs.append({
                    "file": basename,
                    "file_path": source_file,
                    "dir": dir_leaf if dir_leaf != "programas" else "",
                    "relative_dir": relative_dir,
                    "parent_dir": parent,
                    "title": title,
                    "entity_count": co_count,
                })

            deps = entity_deps.get(entity_name, [])
            storage = entity_storage.get(entity_name)

            entity["programs"] = programs
            entity["dependencies"] = deps
            if storage:
                entity["storage_type"] = storage["storage_type"]
                entity["storage_source"] = storage["source"]
            state["entities"].append(entity)

        db_release(con)
    except Exception:
        pass
    return state
