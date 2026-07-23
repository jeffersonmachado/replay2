"""Serviço da Knowledge Base P2-A — análise de código-fonte legado.

Regra de negócio do endpoint GET /api/knowledge-base, extraída do server.py
para manter o dispatcher enxuto.
"""
from __future__ import annotations

import os
import time

from control.server_support import validate_source_path

# Limite de arquivos fonte analisados por requisição.
MAX_SOURCE_FILES = 5000


def build_knowledge_base_report(source_dir: str) -> tuple[int, dict]:
    """Analisa o diretório de fontes e retorna (status_http, payload).

    Validações:
    - parâmetro source obrigatório → 400;
    - diretório inexistente → 404;
    - fora de DAKOTA_SOURCE_ROOT (quando configurado) → 403/500;
    - mais de MAX_SOURCE_FILES arquivos → 400.
    """
    source_dir = str(source_dir or "").strip()
    if not source_dir:
        return 400, {"error": "parametro 'source' obrigatorio"}

    if not os.path.isdir(source_dir):
        return 404, {"error": f"diretorio nao encontrado: {source_dir}"}

    allowed, status, message = validate_source_path(source_dir)
    if not allowed:
        return status, {"error": message}

    start = time.time()
    from dakota_gateway.source_analyzer.parser import SourceParser

    parser = SourceParser(source_dir)
    source_count = len(parser._collect_source_files())
    if source_count > MAX_SOURCE_FILES:
        return 400, {"error": f"muitos arquivos fonte ({source_count}). Limite: {MAX_SOURCE_FILES}"}

    try:
        parser.parse_all()
        report = parser.discovery_report()
        elapsed = time.time() - start
        report["_meta"] = {"elapsed_s": round(elapsed, 2), "files_scanned": source_count}
    except Exception as exc:
        from control.routes.route_helpers import public_error_message

        return 500, {"error": f"falha ao analisar fonte: {public_error_message(exc)}"}

    return 200, report
