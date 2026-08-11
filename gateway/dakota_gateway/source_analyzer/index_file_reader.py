"""Leitor de arquivos de índice Recital/xBase (``i<TABELA>.00N``).

Os índices do Recital guardam a expressão da chave em texto claro no
primeiro bloco do arquivo (ex.: ``rede + loja``, ``rede + loja +
dtos(data)``, ``codigo``). Essa é a fonte mais confiável de "qual campo é
chave" — não depende de heurística sobre o fonte .prg nem de a KB ter
sido populada com ``INDEX ON``.

Convencão observada no legado Dakota: para a tabela ``est100`` (dados em
``est100.est``/``.dbo``), os índices são ``iest100.001``, ``iest100.002``...
(prefixo ``i`` + nome da tabela + extensão numérica).
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

# Extensões de dados reconhecidas como tabela (par do arquivo de índice).
_DATA_EXTS = (".est", ".dbo", ".db", ".dbf")

# Extensão numérica de índice: .001, .002, ... .999
_RE_INDEX_FILE = re.compile(r"^i(.+)\.\d{3}$", re.IGNORECASE)

# Chamada de função simples na expressão: dtos(data), upper(nome), str(cod)
_RE_FUNC_CALL = re.compile(r"^\w+\((.+)\)$")


def parse_key_expression(expression: str) -> list[str]:
    """Quebra a expressão da chave em nomes de campos.

    ``rede + loja + dtos(data)`` → ``["rede", "loja", "data"]``.
    Funções de um argumento (dtos/upper/str/...) são desembrulhadas;
    pedaços que não viram nome de campo são descartados.
    """
    fields: list[str] = []
    for part in str(expression or "").split("+"):
        token = part.strip().strip('"').strip("'")
        if not token:
            continue
        m = _RE_FUNC_CALL.match(token)
        if m:
            token = m.group(1).strip()
        if re.match(r"^[A-Za-z_]\w*$", token):
            fields.append(token.lower())
    return fields


def read_index_expression(path: Path) -> str:
    """Lê a expressão da chave no primeiro bloco do arquivo de índice."""
    try:
        with open(path, "rb") as f:
            head = f.read(512)
    except OSError:
        return ""
    raw = head.split(b"\x00", 1)[0]
    try:
        text = raw.decode("ascii", errors="strict").strip()
    except UnicodeDecodeError:
        return ""
    return text if parse_key_expression(text) else ""


def scan_index_files(data_dir: str | Path) -> dict[str, list[list[str]]]:
    """Varre ``data_dir`` atrás de ``i<TABELA>.00N`` com tabela existente.

    Retorna ``{TABELA: [[campo, ...], ...]}`` — uma lista de chaves por
    tabela (cada arquivo de índice é uma chave, possivelmente composta).
    Só considera índice cujo par de dados (``<TABELA>.est|.dbo|.db|.dbf``)
    existe no mesmo diretório.
    """
    base = Path(str(data_dir or "").strip())
    if not base.is_dir():
        return {}
    data_stems = {
        p.stem.upper() for p in base.iterdir() if p.is_file() and p.suffix.lower() in _DATA_EXTS
    }
    keys: dict[str, list[list[str]]] = {}
    for path in sorted(base.iterdir()):
        if not path.is_file():
            continue
        m = _RE_INDEX_FILE.match(path.name)
        if not m:
            continue
        table = m.group(1).upper()
        if table not in data_stems:
            continue
        expression = read_index_expression(path)
        fields = parse_key_expression(expression)
        if fields:
            keys.setdefault(table, []).append(fields)
    return keys


def discover_data_dir(source_dir: str | Path, env: dict | None = None) -> str:
    """Resolve o diretório de dados (onde ficam os índices).

    Ordem: ``DAKOTA_DATA_ROOT`` explícito → varredura dos irmãos de
    ``source_dir`` procurando quem tem arquivos de índice com tabela par
    (no Dakota: ``/dakota11/prg`` → ``/dakota11/est``). Vazio se nada
    for encontrado — o chamador segue sem o enriquecimento.
    """
    env = env if env is not None else os.environ
    explicit = str(env.get("DAKOTA_DATA_ROOT") or "").strip()
    if explicit and Path(explicit).is_dir():
        return explicit
    source = Path(str(source_dir or "").strip())
    parent = source.parent if source.is_dir() else None
    if parent and parent.is_dir():
        try:
            siblings = sorted(p for p in parent.iterdir() if p.is_dir())
        except OSError:
            siblings = []
        for candidate in siblings:
            if scan_index_files(candidate):
                return str(candidate)
    return ""


def enrich_entities_with_index_files(entities: list[Any], data_dir: str | Path) -> int:
    """Acrescenta os índices lidos dos arquivos às entidades da KB.

    Casa pelo nome da entidade (``USE <tabela>``) com o stem do arquivo de
    dados, em maiúsculas. Retorna quantas entidades foram enriquecidas.
    Idempotente: índices já presentes (mesmo field) não são duplicados.
    """
    keys = scan_index_files(data_dir)
    if not keys:
        return 0
    enriched = 0
    for entity in entities or []:
        table = str(getattr(entity, "name", "") or "").upper()
        table_keys = keys.get(table)
        if not table_keys:
            continue
        indexes = getattr(entity, "indexes", None)
        if indexes is None:
            indexes = []
            try:
                entity.indexes = indexes
            except AttributeError:
                continue
        known = {
            str(idx.get("field") or "").lower()
            for idx in indexes
            if isinstance(idx, dict)
        }
        added = False
        for fields in table_keys:
            for field in fields:
                if field not in known:
                    indexes.append({"field": field, "index": f"arquivo:{table}", "source": "index_file"})
                    known.add(field)
                    added = True
        if added:
            enriched += 1
    return enriched
