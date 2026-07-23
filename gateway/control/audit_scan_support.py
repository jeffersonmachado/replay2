from __future__ import annotations

import os
import re
import subprocess


def analyze_remote_navigation() -> dict | None:
    # Host parametrizável por configuração (sem IP hard-coded no código).
    ssh_host = os.environ.get("DAKOTA_AUDIT_SSH_HOST", "").strip() or "results@10.5.8.25"
    ssh_pass = os.environ.get("SSH_PASSWORD", "")
    if not ssh_pass:
        return {"error": "SSH_PASSWORD nao definida", "source": "remoto"}

    # Credencial via variável SSHPASS (sshpass -e), nunca em argv (visível em ps).
    # accept-new: confia na primeira chave conhecida sem desabilitar a verificação.
    ssh_base = ["sshpass", "-e", "ssh", "-o", "StrictHostKeyChecking=accept-new", ssh_host]
    ssh_env = {**os.environ, "SSHPASS": ssh_pass}

    def _remote_strings(path: str) -> str:
        try:
            return subprocess.check_output(
                ssh_base + [f"strings {path} 2>/dev/null"],
                timeout=15,
                stderr=subprocess.DEVNULL,
                env=ssh_env,
            ).decode("utf-8", errors="replace")
        except Exception:
            return ""

    def _remote_cat(path: str) -> str:
        try:
            return subprocess.check_output(
                ssh_base + [f"cat {path} 2>/dev/null"],
                timeout=15,
                stderr=subprocess.DEVNULL,
                env=ssh_env,
            ).decode("utf-8", errors="replace")
        except Exception:
            return ""

    result: dict = {
        "source": f"remoto ({ssh_host}:/dakota1)",
        "menusig": {},
        "gmenucad": {},
        "gmenu": {},
        "modules": [],
    }

    sigfunc = _remote_strings("/dakota1/lib/sigfunc.dbo")
    if sigfunc:
        submenus = sorted(set(re.findall(r"MENU([A-Z]+[0-9]*)", sigfunc)))
        wopcao_refs = re.findall(r"gmenu\((\w+)\)", sigfunc)
        result["menusig"] = {
            "file": "/dakota1/lib/sigfunc.dbo",
            "size": len(sigfunc),
            "entry_point": "MENUSIG",
            "dispatch": "vescolha[wopcao]",
            "submenus": submenus,
            "gmenu_calls": sorted(set(wopcao_refs)),
            "has_telamenu": "telamenu" in sigfunc.lower(),
        }

    biblio = _remote_strings("/dakota1/lib/biblio.dbo")
    if biblio:
        crud_options = sorted(set(re.findall(r'fTraduz\([^,]+,"([^"]+)"', biblio)))
        l_opca_refs = re.findall(r'lOpca\s*[=<>!]+\s*"([A-Z])"', biblio)
        result["gmenucad"] = {
            "file": "/dakota1/lib/biblio.dbo",
            "size": len(biblio),
            "function": "FMENUCAD",
            "crud_options": crud_options,
            "lOpca_cases": sorted(set(l_opca_refs)),
            "variable": "LOPCA / lOpca",
        }

    exodus = _remote_strings("/dakota1/lib/exodus.dbo")
    if exodus:
        gmenu_refs = sorted(set(re.findall(r"(?:GMENU|GMENUCAD|P_GMENU)([A-Z0-9]*)", exodus)))
        gmenucad_call = re.findall(r'gMenuCad\([^)]*"([^"]+)"', exodus)
        result["gmenu"] = {
            "file": "/dakota1/lib/exodus.dbo",
            "size": len(exodus),
            "functions": ["GMENU", "GMENUCAD", "GMENUCAD2", "P_GMENU", "gMenuCad"],
            "variants": gmenu_refs,
            "gmenucad_full": gmenucad_call[0] if gmenucad_call else "",
        }

    modulo_raw = ""
    for path in ("/dakota1/caduni/modulo.dbf", "/dakota1/lib/modulo.dbf", "/dakota1/prg/lib/modulo.dbf"):
        raw = _remote_cat(path)
        if raw:
            modulo_raw = raw
            break
    if modulo_raw:
        text = modulo_raw.decode("latin-1", errors="replace") if isinstance(modulo_raw, bytes) else modulo_raw
        entries = re.findall(r"([A-Z]{3})([A-Z][A-Za-zÀ-ÿ\s/]{10,50})", text)
        modules = []
        for code, desc in entries:
            desc = desc.strip()
            if desc and len(code) == 3 and code.isalpha():
                modules.append({"code": code, "label": desc})
        result["modules"] = sorted(modules, key=lambda item: item["code"])

    return result


def scan_local_sistema(*, analyze_menus_fn) -> dict | None:
    base = "/opt/dados/sistema"
    if not os.path.isdir(base):
        return None
    try:
        prog_dir = os.path.join(base, "programas")
        data_dir = os.path.join(base, "data")

        modules: list[dict] = []
        total_prg = 0
        total_dbo = 0
        if os.path.isdir(prog_dir):
            for entry in sorted(os.listdir(prog_dir)):
                full = os.path.join(prog_dir, entry)
                if os.path.isdir(full) and not entry.startswith("."):
                    counts: dict[str, int] = {}
                    for root2, _dirs2, files2 in os.walk(full):
                        for filename in files2:
                            ext = os.path.splitext(filename)[1].lower()
                            counts[ext] = counts.get(ext, 0) + 1
                    prg = counts.get(".prg", 0)
                    dbo = counts.get(".dbo", 0)
                    dbf = counts.get(".dbf", 0)
                    fmo = counts.get(".fmo", 0)
                    fmt = counts.get(".fmt", 0)
                    total_prg += prg
                    total_dbo += dbo
                    total_files = sum(counts.values())
                    modules.append({
                        "name": entry,
                        "files": total_files,
                        "prg": prg,
                        "dbo": dbo,
                        "dbf": dbf,
                        "fmo": fmo,
                        "fmt": fmt,
                    })

        databases: list[dict] = []
        if os.path.isdir(data_dir):
            for entry in sorted(os.listdir(data_dir)):
                full = os.path.join(data_dir, entry)
                if os.path.isdir(full) and not entry.startswith("."):
                    file_count = sum(len(files) for _, __, files in os.walk(full))
                    databases.append({"name": entry, "files": file_count})

        tipo_counts: dict[str, int] = {}
        tipo_labels = {
            ".prg": "Programas fonte",
            ".dbo": "Compilados",
            ".dbf": "Tabelas de dados",
            ".dbx": "Indices",
            ".dbt": "Campos memo",
            ".fmo": "Formularios/menus",
            ".fmt": "Formatos relatorio",
            ".db": "SQLite/outros",
        }
        for root, dirs, files in os.walk(base):
            depth = root[len(base):].count(os.sep)
            if depth > 3:
                dirs.clear()
            for filename in files:
                ext = os.path.splitext(filename)[1].lower()
                if ext in tipo_labels:
                    tipo_counts[ext] = tipo_counts.get(ext, 0) + 1

        tipos = []
        for ext, label in tipo_labels.items():
            count = tipo_counts.get(ext, 0)
            if count > 0:
                tipos.append({"ext": ext, "label": label, "count": count})
        tipos.sort(key=lambda item: -item["count"])

        outros_dados: list[dict] = []
        for entry in sorted(os.listdir(base)):
            full = os.path.join(base, entry)
            if os.path.isdir(full) and entry not in ("programas", "data", "lost+found") and not entry.startswith("."):
                file_count = sum(len(files) for _, __, files in os.walk(full))
                if file_count == 0:
                    continue
                info: dict = {"name": entry, "files": file_count}
                subdirs = [
                    name for name in sorted(os.listdir(full))
                    if os.path.isdir(os.path.join(full, name)) and not name.startswith(".")
                ]
                if subdirs:
                    subs = []
                    for subdir in subdirs[:10]:
                        subdir_full = os.path.join(full, subdir)
                        subdir_files = sum(len(files) for _, __, files in os.walk(subdir_full))
                        prg_count = sum(1 for _, __, files in os.walk(subdir_full) for filename in files if filename.endswith(".prg"))
                        dbf_count = sum(1 for _, __, files in os.walk(subdir_full) for filename in files if filename.endswith(".dbf"))
                        subs.append({
                            "name": subdir,
                            "files": subdir_files,
                            "prg": prg_count,
                            "dbf": dbf_count,
                        })
                    info["subdirs"] = subs
                outros_dados.append(info)

        menu_analysis = analyze_menus_fn(base)
        return {
            "base_path": base,
            "modules": modules,
            "module_count": len(modules),
            "programs_prg": total_prg,
            "programs_dbo": total_dbo,
            "databases": databases,
            "file_types": tipos,
            "other_dirs": outros_dados,
            "menu_analysis": menu_analysis,
        }
    except Exception:
        return None


def infer_module_label(code: str) -> str:
    labels = {
        "CAD": "Cadastro", "COP": "Contas a Pagar", "CRE": "Contas a Receber",
        "FIN": "Financeiro", "FAT": "Faturamento", "PED": "Pedido",
        "EST": "Estoque", "PRD": "Producao", "EXP": "Expedicao",
        "COM": "Compras", "NF": "Nota Fiscal", "CRM": "CRM",
        "RH": "Recursos Humanos", "CTB": "Contabilidade", "ASS": "Assistencia Tecnica",
        "VOIP": "VoIP/Telefonia", "CLI": "Clientes", "CON": "Contratos",
        "VEN": "Vendas", "ORC": "Orcamento", "SYS": "Sistema",
        "AGE": "Agendamento", "ATE": "Atendimento", "AUD": "Auditoria Medica",
        "COC": "Controle de Compras", "COR": "Contas a Receber",
        "MOC": "Medicina Ocupacional", "TEL": "Telemarketing",
        "ENG": "Engenharia", "FPG": "Folha de Pagamentos",
        "CDM": "Condominios", "SUP": "Suporte", "TRE": "Treinamento",
        "UBB": "UBB", "PON": "Ponto Eletronico", "CAR": "Estacionamento",
        "IMP": "Importacao", "UTI": "Utilitarios", "PRO": "Producao",
    }
    return labels.get(code, f"Modulo {code}")


def analyze_menus(
    base_dir: str,
    *,
    infer_program_purpose_fn,
    infer_module_label_fn,
) -> dict:
    result: dict = {
        "inferred_modules": [],
        "menu_tree_inferred": [],
        "menu_files": [],
        "program_index": [],
        "call_graph": [],
        "menu_modules": [],
    }

    re_title = re.compile(r"(?:TITLE|TITULO|CAPTION|HEADER)\s+['\"]([^'\"]+)['\"]", re.IGNORECASE)
    re_prompt = re.compile(r"(?:@\s+\d+\s*,\s*\d+\s+PROMPT|MENU\s+OPTION)\s+['\"]([^'\"]+)['\"]", re.IGNORECASE)
    re_do = re.compile(r"DO\s+['\"]?(\w+(?:/\w+)*)['\"]?", re.IGNORECASE)
    re_use = re.compile(r"USE\s+['\"]?(\w+)['\"]?", re.IGNORECASE)
    re_select = re.compile(r"SELECT\s+.*?\bFROM\s+['\"]?(\w+)['\"]?", re.IGNORECASE | re.DOTALL)

    prog_dir = os.path.join(base_dir, "programas")
    dir_modules: dict[str, dict] = {}
    if os.path.isdir(prog_dir):
        for entry in sorted(os.listdir(prog_dir)):
            full = os.path.join(prog_dir, entry)
            if os.path.isdir(full) and not entry.startswith(".") and not entry.startswith("_"):
                mod_name = entry.upper()
                dir_modules[mod_name] = {
                    "code": mod_name,
                    "label": infer_module_label_fn(mod_name),
                    "path": full,
                    "programs": [],
                    "total_prg": 0,
                    "total_dbo": 0,
                    "entities": set(),
                    "menu_program": None,
                }

    dbf_modules: dict[str, str] = {}
    for root, _dirs, files in os.walk(base_dir):
        for fname in files:
            if fname.lower() != "modulo.dbf":
                continue
            fpath = os.path.join(root, fname)
            try:
                raw = open(fpath, "rb").read()
                text = raw.decode("latin-1", errors="replace")
                entries = re.findall(r"([A-Z]{3})([A-Z][A-Za-zÀ-ÿ\s/]{10,50})", text)
                for code, desc in entries:
                    desc = desc.strip()
                    if desc and len(code) == 3 and code.isalpha() and code not in dbf_modules:
                        dbf_modules[code] = desc
                mod_list = [
                    {"code": c, "label": d}
                    for c, d in sorted(
                        set(
                            (c, d)
                            for c, d in re.findall(r"([A-Z]{3})([A-Z][A-Za-zÀ-ÿ\s/]{10,50})", text)
                            if d.strip() and len(c) == 3 and c.isalpha()
                        ),
                        key=lambda item: item[0],
                    )
                ]
                if mod_list:
                    result["menu_modules"].append({
                        "file": fname,
                        "rel_path": os.path.relpath(fpath, base_dir),
                        "modules": mod_list,
                    })
            except Exception:
                pass

    for code, label in dbf_modules.items():
        if code in dir_modules:
            dir_modules[code]["label"] = label
        else:
            dir_modules[code] = {
                "code": code,
                "label": label,
                "path": "",
                "programs": [],
                "total_prg": 0,
                "total_dbo": 0,
                "entities": set(),
                "menu_program": None,
            }

    menu_keywords = {"MENU", "GMENU", "MENUSIG", "wopcao", "fTraduz", "PROMPT", "OPTION"}
    menu_files_found: list[dict] = []
    all_programs: dict[str, dict] = {}
    call_graph: dict[str, set[str]] = {}
    entity_refs: dict[str, set[str]] = {}

    scanned = 0
    for root, dirs, files in os.walk(base_dir):
        depth = root[len(base_dir):].count(os.sep)
        if depth > 4:
            dirs.clear()
        for fname in files:
            fpath = os.path.join(root, fname)
            ext = os.path.splitext(fname)[1].lower()
            if ext not in (".prg", ".dbo"):
                continue
            try:
                fsize = os.path.getsize(fpath)
                if fsize > 500_000:
                    continue
                if ext == ".dbo":
                    try:
                        content = subprocess.check_output(["strings", fpath], timeout=3).decode("utf-8", errors="replace")
                    except Exception:
                        continue
                else:
                    content = open(fpath, "r", encoding="utf-8", errors="replace").read()
            except Exception:
                continue

            scanned += 1
            name_no_ext = os.path.splitext(fname)[0].upper()
            parent_dir = os.path.basename(root).upper()
            module_code = parent_dir if parent_dir in dir_modules else ""

            title = ""
            title_match = re_title.search(content)
            if title_match:
                title = title_match.group(1)
            purpose = title if title else infer_program_purpose_fn(fname)

            calls = set()
            for match in re_do.finditer(content):
                called = match.group(1).replace("/", "_").upper()
                calls.add(called)

            entities = set()
            for match in re_use.finditer(content):
                entity = match.group(1).upper().strip()
                if entity and not entity.startswith("/") and len(entity) > 1:
                    entities.add(entity)
            for match in re_select.finditer(content):
                entity = match.group(1).upper().strip()
                if entity and len(entity) > 1:
                    entities.add(entity)

            content_upper = content.upper() if ext == ".prg" else content
            has_menu = any(keyword in content_upper for keyword in menu_keywords)
            is_menu_file = has_menu or "menu" in name_no_ext.lower()

            options = []
            if is_menu_file:
                for match in re_prompt.finditer(content):
                    options.append(match.group(1))

            rel_path = os.path.relpath(fpath, base_dir)
            prog_info = {
                "file": fname,
                "path": fpath,
                "rel_path": rel_path,
                "module": module_code,
                "title": title,
                "purpose": purpose,
                "calls": sorted(calls),
                "entities": sorted(entities),
                "is_menu": is_menu_file,
                "options": options[:30],
                "option_count": len(options),
                "call_count": len(calls),
                "entity_count": len(entities),
                "size": fsize,
            }

            all_programs[rel_path] = prog_info
            if calls:
                call_graph[rel_path] = calls
            if entities:
                entity_refs[rel_path] = entities

            if module_code and module_code in dir_modules:
                dir_modules[module_code]["programs"].append(prog_info)
                dir_modules[module_code]["entities"].update(entities)
                if ext == ".prg":
                    dir_modules[module_code]["total_prg"] += 1
                elif ext == ".dbo":
                    dir_modules[module_code]["total_dbo"] += 1
                if is_menu_file and not dir_modules[module_code]["menu_program"]:
                    dir_modules[module_code]["menu_program"] = fname

            if is_menu_file and (title or options or calls):
                menu_files_found.append(prog_info)

    inferred_modules = []
    for code in sorted(dir_modules.keys()):
        dm = dir_modules[code]
        dm["programs"].sort(key=lambda item: (not item.get("is_menu", False), -item.get("entity_count", 0)))
        inferred_modules.append({
            "code": code,
            "label": dm["label"],
            "program_count": len(dm["programs"]),
            "prg": dm["total_prg"],
            "dbo": dm["total_dbo"],
            "entity_count": len(dm["entities"]),
            "top_entities": sorted(dm["entities"])[:10],
            "menu_program": dm["menu_program"],
            "top_programs": [{
                "file": item["file"],
                "purpose": item["purpose"],
                "entities": item["entities"][:8],
                "entity_count": item["entity_count"],
                "is_menu": item["is_menu"],
                "option_count": item.get("option_count", 0),
            } for item in dm["programs"][:5]],
        })

    menu_tree: list[dict] = []
    for module in inferred_modules:
        if module["menu_program"] or module["top_programs"]:
            menu_progs = [item for item in module["top_programs"] if item.get("is_menu") or item.get("option_count", 0) > 0]
            if menu_progs or module["program_count"] > 2:
                menu_tree.append({
                    "module_code": module["code"],
                    "module_label": module["label"],
                    "program_count": module["program_count"],
                    "menu_count": len(menu_progs),
                    "menus": menu_progs[:3],
                })

    top_by_entities = sorted(entity_refs.items(), key=lambda item: -len(item[1]))[:30]
    top_programs = []
    for rel_path, entities in top_by_entities:
        info = all_programs.get(rel_path, {})
        top_programs.append({
            "file": info.get("file", rel_path),
            "rel_path": info.get("rel_path", ""),
            "module": info.get("module", ""),
            "purpose": info.get("purpose", ""),
            "entity_count": len(entities),
            "entities": sorted(entities)[:20],
        })

    top_callers = sorted(call_graph.items(), key=lambda item: -len(item[1]))[:20]
    call_graph_list = []
    for rel_path, calls in top_callers:
        info = all_programs.get(rel_path, {})
        call_graph_list.append({
            "file": info.get("file", rel_path),
            "rel_path": info.get("rel_path", ""),
            "module": info.get("module", ""),
            "purpose": info.get("purpose", ""),
            "call_count": len(calls),
            "calls": sorted(calls)[:15],
        })

    result["inferred_modules"] = inferred_modules
    result["menu_tree_inferred"] = menu_tree
    result["menu_files"] = sorted(menu_files_found, key=lambda item: -item.get("option_count", 0) - item.get("call_count", 0))[:30]
    result["program_index"] = top_programs
    result["call_graph"] = call_graph_list
    result["total_scanned"] = scanned
    result["total_programs"] = len(all_programs)
    result["total_menus"] = len(menu_files_found)
    result["total_with_entities"] = len(entity_refs)
    result["total_with_calls"] = len(call_graph)
    result["total_modules"] = len(inferred_modules)

    navigation_tree: list[dict] = []
    all_known_modules: dict[str, dict] = {}
    for module in inferred_modules:
        all_known_modules[module["code"]] = module
    for code, label in dbf_modules.items():
        if code not in all_known_modules:
            all_known_modules[code] = {
                "code": code,
                "label": label,
                "program_count": 0,
                "entity_count": 0,
                "top_entities": [],
                "menu_program": None,
            }

    for code in sorted(all_known_modules.keys()):
        module = all_known_modules[code]
        entry_candidates = [
            f"{code.lower()}menu", f"{code.lower()}0301", f"{code.lower()}0501",
            f"{code.lower()}0000", f"{code.lower()}0101", f"menu{code.lower()}",
        ]
        entry_prog = module.get("menu_program") or ""
        if not entry_prog:
            for item in module.get("top_programs", []):
                pfile = (item.get("file", "")).lower().replace(".prg", "").replace(".dbo", "")
                if pfile in entry_candidates:
                    entry_prog = item.get("file", "")
                    break

        real_count = module.get("program_count", 0)
        nav_entry = {
            "code": code,
            "label": module.get("label", code),
            "program_count": real_count,
            "entity_count": module.get("entity_count", 0),
            "entry_program": entry_prog,
            "has_directory": os.path.isdir(os.path.join(prog_dir, code.lower())) if os.path.isdir(prog_dir) else False,
        }
        if module.get("top_programs"):
            nav_entry["sample_programs"] = [
                {"file": item["file"], "purpose": item.get("purpose", ""), "entities": item.get("entities", [])[:5]}
                for item in module["top_programs"][:3]
            ]
        navigation_tree.append(nav_entry)

    result["navigation_tree"] = navigation_tree

    root_menus: list[dict] = []
    for _key, info in all_programs.items():
        if not info.get("is_menu"):
            continue
        rel = info.get("rel_path", "")
        is_root = False
        if rel.startswith("programas/") and "/" not in rel[len("programas/"):]:
            is_root = True
        elif rel.startswith("fusao/programas/") and "/" not in rel[len("fusao/programas/"):]:
            is_root = True
        elif rel.startswith("fusao/desenv/") and "/" not in rel[len("fusao/desenv/"):]:
            is_root = True
        if not is_root:
            continue
        purpose = info.get("purpose", "") or info.get("title", "") or infer_program_purpose_fn(info.get("file", ""))
        root_menus.append({
            "file": info.get("file", ""),
            "path": info.get("path", ""),
            "rel_path": rel,
            "module": info.get("module", ""),
            "title": info.get("title", ""),
            "purpose": purpose,
            "calls": info.get("calls", [])[:20],
            "entities": info.get("entities", [])[:20],
            "is_menu": True,
            "option_count": info.get("option_count", 0),
            "call_count": info.get("call_count", 0),
            "entity_count": info.get("entity_count", 0),
            "size": info.get("size", 0),
        })
    root_menus.sort(key=lambda item: (not ("menu." in item.get("file", "").lower()), -item.get("call_count", 0)))
    result["root_menu_programs"] = root_menus
    return result
