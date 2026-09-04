"""Leitor de tabelas Recital (``<TABELA>.<modulo>``) para amostragem de valores.

Complementa o ``index_file_reader`` (que lê a expressão da chave dos
``i<TABELA>.00N``): aqui lemos os DADOS da tabela para amostrar valores reais
da coluna-chave — matéria-prima dos ``lookup_values`` da síntese a partir de
captura (campo FK coberto por valores reais deixa de ser âncora e passa a
variar dentro do cadastro, ex.: condição de pagamento, fornecedor).

Formato observado no legado Dakota (AIX, big-endian), validado nas tabelas
``arq210.cmp``, ``arq310.cmp``, ``arq100.cad``, ``arq220.cmp``:

- header fixo de ``_DESC_AREA`` (3104) bytes; descritores de campo de 24
  bytes a partir de 32: nome 11 (NUL-padded), tipo 1 (``C``/``N``/``D``...),
  comprimento u32 BE @+12, offset no registro u32 BE @+20;
- ``rec_len`` (u32 BE @20) = 1 (flag de deleção) + soma dos comprimentos;
- área de nomes de ``_NAME_AREA`` (3552) bytes (não usada aqui);
- registros a partir de ``DATA_START`` (6656) até o fim do arquivo —
  ``rec[0]`` é a flag (``' '`` ativo, ``'*'`` deletado) e o campo ocupa
  ``rec[foff : foff+len]`` (o campo de offset 1 começa logo após a flag).

Qualquer inconsistência (tamanho não múltiplo de ``rec_len``, soma dos campos
diferente, nome inválido) rejeita a tabela: o chamador segue SEM valores de
lookup e o campo permanece âncora — comportamento anterior preservado.
"""
from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from pathlib import Path

from .index_file_reader import scan_index_files

_DESC_AREA = 3104  # bloco de descritores: 32 + 128 campos × 24
_NAME_AREA = 3552  # bloco de nomes (constante observada no legado)
DATA_START = _DESC_AREA + _NAME_AREA  # 6656 — início dos registros

_DESC_OFFSET = 32
_DESC_STRIDE = 24
_HDR_REC_LEN = 20

_RE_FIELD_NAME = re.compile(r"^[A-Za-z_]\w*$")
_RE_INDEX_SUFFIX = re.compile(r"^\.\d{3}$")


@dataclass
class TableField:
    """Campo da tabela: nome, tipo Recital (C/N/D...), tamanho e offset."""

    name: str
    type: str
    length: int
    offset: int  # posição 1-based no registro (logo após a flag de deleção)


@dataclass
class RecitalTable:
    """Tabela Recital aberta: schema + geometria dos registros."""

    path: Path
    fields: list[TableField]
    record_length: int
    record_count: int
    data_start: int = DATA_START


def _be(raw: bytes, off: int) -> int:
    return struct.unpack(">I", raw[off:off + 4])[0]


def read_table(path: str | Path) -> RecitalTable | None:
    """Lê o schema e a geometria da tabela. Nunca levanta: None se inválida."""
    path = Path(str(path or "").strip())
    try:
        size = path.stat().st_size
        with open(path, "rb") as fh:
            head = fh.read(_DESC_AREA)
    except OSError:
        return None
    if len(head) < _DESC_AREA:
        return None
    rec_len = _be(head, _HDR_REC_LEN)
    if rec_len <= 1 or rec_len > 65535:
        return None
    if size < DATA_START + rec_len or (size - DATA_START) % rec_len != 0:
        return None
    fields: list[TableField] = []
    total = 1  # flag de deleção
    off = _DESC_OFFSET
    while off + _DESC_STRIDE <= _DESC_AREA:
        raw_name = head[off:off + 11].split(b"\x00", 1)[0]
        try:
            name = raw_name.decode("ascii")
        except UnicodeDecodeError:
            break
        if not name or not _RE_FIELD_NAME.match(name):
            break
        ftype = chr(head[off + 11])
        length = _be(head, off + 12)
        foff = _be(head, off + 20)
        if length <= 0 or length > 4096 or foff != total:
            break
        fields.append(TableField(name=name, type=ftype, length=length, offset=foff))
        total += length
        off += _DESC_STRIDE
    if not fields or total != rec_len:
        return None
    # Saneamento: a flag do primeiro registro precisa ser ' ' ou '*'.
    try:
        with open(path, "rb") as fh:
            fh.seek(DATA_START)
            flag = fh.read(1)
    except OSError:
        return None
    if flag not in (b" ", b"*"):
        return None
    return RecitalTable(
        path=path,
        fields=fields,
        record_length=rec_len,
        record_count=(size - DATA_START) // rec_len,
    )


def _iter_sample_records(
    table: RecitalTable,
    fields: list[TableField],
    *,
    limit: int,
) -> "list[tuple[str, ...]]":
    """Tuplas de valores das colunas ``fields`` lidas do MESMO registro.

    Varre o arquivo com passo ``record_count // limit`` (início, meio e fim
    sem ler tudo), só registros ativos. Tuplas com alguma coluna vazia são
    descartadas; dedup preservando a ordem de coleta.
    """
    stride = max(1, table.record_count // limit)
    values: dict[tuple[str, ...], None] = {}
    try:
        with open(table.path, "rb") as fh:
            for i in range(0, table.record_count, stride):
                fh.seek(table.data_start + i * table.record_length)
                rec = fh.read(table.record_length)
                if len(rec) < table.record_length or rec[0:1] != b" ":
                    continue
                row = tuple(
                    rec[f.offset:f.offset + f.length].decode("latin-1").strip()
                    for f in fields
                )
                if not all(row):
                    continue
                values.setdefault(row)
                if len(values) >= limit:
                    break
    except OSError:
        return []
    return list(values)


def sample_key_tuples(
    table: RecitalTable,
    columns: list[str],
    *,
    limit: int = 300,
) -> "list[tuple[str, ...]]":
    """Amostra tuplas de colunas do mesmo registro (chave composta real).

    Matéria-prima da variação em par da síntese: campos que juntos consultam
    um cadastro (ex.: modelo+combinação do produto) só podem variar como
    tupla — valor de coluna isolado quebraria a combinação. Campos de data
    (``D``) e colunas inexistentes esvaziam o resultado.
    """
    wanted = [str(c or "").strip().lower() for c in (columns or [])]
    if not wanted or any(not c for c in wanted) or limit <= 0:
        return []
    fields: list[TableField] = []
    for name in wanted:
        field = next(
            (f for f in table.fields if f.name.lower() == name), None
        )
        if field is None or field.type.upper() == "D":
            return []
        fields.append(field)
    return _iter_sample_records(table, fields, limit=limit)


def sample_column_values(
    table: RecitalTable,
    column: str,
    *,
    limit: int = 300,
) -> list[str]:
    """Amostra valores distintos da coluna (registros ativos, ordem por stride).

    Varre o arquivo com passo ``record_count // limit`` para cobrir início,
    meio e fim sem ler tudo. Campos de data (``D`` — 4 bytes binários) não são
    amostrados. Dedup preservando a ordem de coleta.
    """
    wanted = str(column or "").strip().lower()
    if not wanted or limit <= 0:
        return []
    field = next(
        (f for f in table.fields if f.name.lower() == wanted), None
    )
    if field is None or field.type.upper() == "D":
        return []
    return [row[0] for row in _iter_sample_records(table, [field], limit=limit)]


def _candidate_files(
    data_dirs: list,
    table: str,
    module_hint: str = "",
) -> list:
    """Candidatos físicos para a tabela lógica, em ordem de preferência.

    Convenção física do legado Dakota (verificado no MIG24): o dado mora em
    ``arq<NNN>.<mod>`` enquanto o nome lógico no fonte é ``<mod><NNN>``
    (``use cmp310`` → ``arq310.cmp``; ``iest361.001`` ↔ ``arq361.est``) e o
    ``.dbo`` homônimo é objeto compilado, não dado. Tabelas ``arq<NNN>``
    existem replicadas por módulo com conteúdos DIFERENTES (cad/arq210.cad ≠
    cmp/arq210.cmp) — por isso o diretório do módulo da captura tem
    precedência para elas.
    """
    stem = str(table or "").strip().lower()
    if not stem:
        return []
    dirs = [Path(str(d or "").strip()) for d in (data_dirs or []) if str(d or "").strip()]
    hint = str(module_hint or "").strip().lower()
    if hint:
        dirs.sort(key=lambda p: 0 if p.name.lower() == hint else 1)
    m = re.match(r"^([a-z]+)(\d.*)$", stem)
    candidates: list[Path] = []
    seen: set[str] = set()

    def _add(base: Path, wanted_stem: str) -> None:
        if not base.is_dir():
            return
        try:
            entries = sorted(base.iterdir())
        except OSError:
            return
        for candidate in entries:
            if not candidate.is_file():
                continue
            if candidate.stem.lower() != wanted_stem:
                continue
            suffix = candidate.suffix.lower()
            if _RE_INDEX_SUFFIX.match(suffix) or suffix == ".tmp":
                continue
            key = str(candidate)
            if key not in seen:
                seen.add(key)
                candidates.append(candidate)

    if m:
        # Nome lógico <mod><NNN>: o físico é arq<NNN> no diretório <mod>.
        mod, rest = m.group(1), m.group(2)
        for base in dirs:
            if base.name.lower() == mod:
                _add(base, f"arq{rest}")
    for base in dirs:
        _add(base, stem)
    return candidates


def find_table_file(data_dirs: list, table: str, module_hint: str = "") -> Path | None:
    """Primeiro candidato físico parseável da tabela (None se nenhum)."""
    for candidate in _candidate_files(data_dirs, table, module_hint):
        if read_table(candidate) is not None:
            return candidate
    return None


_RE_KEYISH_NAME = re.compile(r"^(cod|codigo|numero|num|chave|id)", re.IGNORECASE)


def _key_column_fallback(table: RecitalTable) -> str:
    """Coluna-chave sem índice: campo C com nome de código (``CODIGO``,
    ``NUMERO``...) tem precedência sobre o primeiro campo C — ex.: arq310
    abre com TIPOOC (tipo do documento), mas a chave consultada é NUMERO."""
    fields = [f for f in table.fields if f.type.upper() == "C"]
    if not fields:
        return ""
    keyish = next((f.name for f in fields if _RE_KEYISH_NAME.match(f.name)), "")
    return keyish or fields[0].name


def sample_lookup_tables(
    data_dirs: list,
    tables: set[str] | list[str] | dict[str, str],
    *,
    limit: int = 300,
) -> dict[str, list[str]]:
    """Amostra a coluna-chave de cada tabela FK referenciada pela captura.

    ``tables``: conjunto/lista de nomes lógicos, ou dict nome→módulo da
    captura (hint de diretório para tabelas ``arq<NNN>`` replicadas por
    módulo). A coluna amostrada é o primeiro campo da primeira chave do
    índice (``i<TABELA>.001`` via ``scan_index_files``, chaveado pelo nome
    LÓGICO) — é ela que o VALID do fonte consulta; sem índice (cadastros
    auxiliares ``arq<NNN>`` costumam não ter), cai para o primeiro campo
    ``C`` da tabela (convenção do legado: o código abre o cadastro). Sem
    arquivo de dados parseável ou sem coluna, a tabela não entra no
    resultado: o campo correspondente segue âncora (valor original
    mantido), como antes.
    """
    if isinstance(tables, dict):
        items = {str(t).strip().lower(): str(h).strip().lower()
                 for t, h in tables.items() if str(t).strip()}
    else:
        items = {str(t).strip().lower(): "" for t in (tables or []) if str(t).strip()}
    index_cache: dict[str, dict] = {}
    sampled: dict[str, list[str]] = {}
    for table in sorted(items):
        for candidate in _candidate_files(data_dirs, table, items[table]):
            rec_table = read_table(candidate)
            if rec_table is None:
                continue
            dir_key = str(candidate.parent)
            if dir_key not in index_cache:
                index_cache[dir_key] = scan_index_files(candidate.parent)
            dir_keys = index_cache[dir_key]
            keys = dir_keys.get(table.upper()) or []
            if keys and keys[0]:
                column = keys[0][0]
                if not any(f.name.lower() == column for f in rec_table.fields):
                    column = ""
            else:
                column = ""
            if not column:
                column = _key_column_fallback(rec_table)
            if not column:
                continue
            values = sample_column_values(rec_table, column, limit=limit)
            if values:
                sampled[table] = values
                break
    return sampled
