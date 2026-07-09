"""Recital 8.0 Entity Extractor — Inferencia Inteligente + Auditoria.

Cada entidade inferida gera AuditTrail com evidencias de POR QUE
foi detectada (qual regra, em qual linha, com qual score).
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Dict, List, Set

from .entity_catalog import EntityDefinition, OperationDefinition
from .audit import AuditTrail, AuditEvidence, log_audit

_MODULO_NAMES: Dict[str, str] = {
    "cad": "cadastros", "cre": "contas_receber", "est": "estoque",
    "fat": "faturamento", "ped": "pedidos", "pcp": "producao",
    "cmp": "compras", "fin": "financeiro", "exp": "expedicao",
    "mat": "materiais", "sig": "sistema", "blo": "bloqueio",
    "uni": "unificado", "sol": "solados", "sgm": "gestao_modelos",
    "ses": "sesmt", "ctb": "contabilidade", "imo": "imoveis",
    "sac": "sac", "mkt": "marketing", "loj": "lojas",
    "cpr": "compras", "ndm": "nota_devolucao", "mao": "mao_de_obra",
}

_STOP_WORDS: Set[str] = {
    # Controle de fluxo
    "if", "else", "endif", "function", "return", "parameters", "private",
    "public", "do", "while", "enddo", "case", "endcase", "for", "endfor",
    "scan", "endscan", "with", "endwhile", "iif",
    "set", "declare", "local", "dimension", "text", "endtext",
    # Variaveis genericas
    "lareaant", "lok", "labrarq", "lareares", "larea",
    # Palavras-chave Recital que parecem alias (3-20 chars)
    "off", "on", "talk", "clock", "status", "deleted", "exclusive",
    "century", "hours", "date", "separator", "point", "bell",
    "console", "device", "printer", "alternate", "procedure",
    "carry", "clear", "count", "display", "eject", "erase",
    "goto", "help", "input", "join", "keyboard", "label",
    "list", "note", "pack", "quit", "recall", "reindex",
    "release", "rename", "report", "restore", "run", "save",
    "skip", "sort", "store", "sum", "total", "type", "wait", "zap",
    "copy", "find", "go", "locate", "modify", "scatter", "seek", "update",
    # Campos que vazam do metadata
    "field_name", "field", "fields", "name", "source", "storage",
    # Nomes muito curtos que causam falsos positivos
    "tmp", "log", "msg", "err", "out", "old", "new", "now", "all",
}


def _infer_modulo(alias: str) -> str:
    low = alias.lower()
    for prefix, nome in sorted(_MODULO_NAMES.items(), key=lambda x: -len(x[0])):
        if low.startswith(prefix):
            return nome
    return ""


def _is_likely_entity(alias: str) -> bool:
    low = alias.lower()
    if low in _STOP_WORDS:
        return False
    if len(alias) < 3 or len(alias) > 20:
        return False
    if not re.search(r"[a-z]", low):
        return False
    return True


class RecitalExtractor:

    @staticmethod
    def extract(content: str, source_file: str = "") -> list[EntityDefinition]:
        inferred: Dict[str, dict] = {}
        lines = content.split("\n")

        for line_no, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("*") or stripped.startswith("&&"):
                continue

            # 1. XxxAbreNNN(flag) → score 10
            #    Tambem captura XxxAbreNNN() sem argumento (padrao Dakota)
            m = re.search(r"(\w+)Abre(\d+)\s*\(\s*\d*\s*\)", line, re.IGNORECASE)
            if m:
                alias = (m.group(1) + m.group(2)).lower()
                if _is_likely_entity(alias):
                    RecitalExtractor._record(inferred, alias, "open", source_file, line_no,
                                             _infer_modulo(alias), score=10,
                                             rule="XxxAbreNNN", pattern=m.group(0))

            # 1b. XxxAbre() sem numero → score 8 (ex: CadAbre())
            m = re.search(r"(\w+)Abre\s*\(\s*\)", line, re.IGNORECASE)
            if m:
                alias = m.group(1).lower()
                if _is_likely_entity(alias) and "abre" not in alias.lower():
                    RecitalExtractor._record(inferred, alias, "open", source_file, line_no,
                                             _infer_modulo(alias), score=8,
                                             rule="XxxAbre", pattern=m.group(0))

            # 2. USE arquivo [ALIAS nome] → score 8
            m = re.search(r"use\s+(\S+)", line, re.IGNORECASE)
            if m:
                alias_m = re.search(r"alias\s+(\w+)", line, re.IGNORECASE)
                alias = (alias_m.group(1) if alias_m else m.group(1).split(".")[0]).lower()
                if _is_likely_entity(alias):
                    RecitalExtractor._record(inferred, alias, "use", source_file, line_no,
                                             _infer_modulo(alias), score=8,
                                             rule="USE_ALIAS", pattern=m.group(0))

            # 3. alias->campo → score 1 por mencao
            for arrow_m in re.finditer(r"(\w{3,15})->\w+", line):
                alias = arrow_m.group(1).lower()
                if _is_likely_entity(alias):
                    RecitalExtractor._record(inferred, alias, "reference", source_file, line_no,
                                             _infer_modulo(alias), score=1,
                                             rule="ALIAS_ARROW", pattern=arrow_m.group(0))

            # 3b. alias.campo (notacao alternativa) → score 1
            for dot_m in re.finditer(r"(\w{3,15})\.\w+", line):
                alias = dot_m.group(1).lower()
                if _is_likely_entity(alias) and alias not in {"set", "close", "select", "replace", "delete", "append"}:
                    RecitalExtractor._record(inferred, alias, "reference", source_file, line_no,
                                             _infer_modulo(alias), score=1,
                                             rule="ALIAS_DOT", pattern=dot_m.group(0))

            # 4. select("alias") → score 3
            for sel_m in re.finditer(r'select\s*\(\s*"(\w+)"\s*\)', line, re.IGNORECASE):
                alias = sel_m.group(1).lower()
                if _is_likely_entity(alias):
                    RecitalExtractor._record(inferred, alias, "select", source_file, line_no,
                                             _infer_modulo(alias), score=3,
                                             rule="SELECT_ALIAS", pattern=sel_m.group(0))

            # 5. close alias → score 2
            m = re.match(r"close\s+(\w{3,15})", stripped, re.IGNORECASE)
            if m:
                alias = m.group(1).lower()
                if _is_likely_entity(alias):
                    RecitalExtractor._record(inferred, alias, "close", source_file, line_no,
                                             _infer_modulo(alias), score=2,
                                             rule="CLOSE", pattern=m.group(0))

            # 6. SELECT SQL FROM tabela → score 4
            m = re.search(r"from\s+(\w+)", line, re.IGNORECASE)
            if m and re.search(r"select\s+", line, re.IGNORECASE):
                table = m.group(1).lower()
                if _is_likely_entity(table) and "dual" not in table:
                    RecitalExtractor._record(inferred, table, "select_sql", source_file, line_no,
                                             _infer_modulo(table), score=4,
                                             rule="SELECT_FROM", pattern=m.group(0))

            # 7. set procedure to .../moduloabre → score 5 (padrao Dakota)
            m = re.search(r"set\s+procedure\s+to\s+.*?(\w+)(?:\.dbo|\.prg)?", line, re.IGNORECASE)
            if m:
                proc_name = m.group(1).lower()
                if _is_likely_entity(proc_name) and proc_name not in {"additive", "to"}:
                    # Extrai nome base do modulo (ex: sncabre → snc)
                    base = re.sub(r'abre?$', '', proc_name, flags=re.IGNORECASE)
                    if base and base != proc_name and _is_likely_entity(base):
                        RecitalExtractor._record(inferred, base, "procedure", source_file, line_no,
                                                 _infer_modulo(base), score=5,
                                                 rule="SET_PROCEDURE_TO", pattern=m.group(0))
                    else:
                        RecitalExtractor._record(inferred, proc_name, "procedure", source_file, line_no,
                                                 _infer_modulo(proc_name), score=3,
                                                 rule="SET_PROCEDURE_TO", pattern=m.group(0))

            # 8. do programa → score 2 (chamada de submodulo)
            m = re.match(r"do\s+(\w+)", stripped, re.IGNORECASE)
            if m:
                program = m.group(1).lower()
                if _is_likely_entity(program) and program not in {"while", "case", "endcase", "enddo"}:
                    RecitalExtractor._record(inferred, program, "call", source_file, line_no,
                                             _infer_modulo(program), score=2,
                                             rule="DO_PROGRAM", pattern=m.group(0))

        # ── Consolida com auditoria ──
        entities: List[EntityDefinition] = []
        for alias, info in sorted(inferred.items(), key=lambda x: -x[1]["score"]):
            ent = EntityDefinition(name=alias, storage_type="recital", source=source_file)

            # Monta AuditTrail
            trail = AuditTrail(
                entity_name=alias,
                inference_type="entity_discovery",
                final_decision=f"{alias} (modulo={info.get('modulo', '?')})",
                confidence=min(info["score"] / 20.0, 1.0),
            )
            for ev in info.get("evidence", []):
                trail.add_evidence(
                    rule=ev["rule"], pattern=ev["pattern"], score=ev["score"],
                    source_file=ev["source"], source_line=ev["line"],
                    context=f"modulo={info.get('modulo', '')}",
                )

            # Injeta audit no metadata_json
            meta = {"_score": info["score"], "_module": info.get("modulo", "")}
            meta["_audit"] = trail.to_dict()
            ent.metadata_json = json.dumps(meta, ensure_ascii=False)
            log_audit(trail)

            for op in info["ops"]:
                ent.operations.append(OperationDefinition(
                    operation_type=op["type"], entity_name=alias,
                    source_file=op["source"], line_number=op["line"],
                ))
            entities.append(ent)

        return entities

    @staticmethod
    def _record(inferred: Dict, alias: str, op_type: str, source: str,
                line: int, modulo: str = "", score: int = 1,
                rule: str = "", pattern: str = ""):
        if alias not in inferred:
            inferred[alias] = {"ops": [], "modulo": modulo, "score": 0, "evidence": []}
        inferred[alias]["ops"].append({"type": op_type, "source": source, "line": line})
        inferred[alias]["score"] += score
        if rule:
            inferred[alias]["evidence"].append({
                "rule": rule, "pattern": pattern, "score": score,
                "source": source, "line": line,
            })
        if modulo and (
            not inferred[alias].get("modulo")
            or len(modulo) > len(inferred[alias].get("modulo", ""))
        ):
            inferred[alias]["modulo"] = modulo
