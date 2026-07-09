from __future__ import annotations

import re
from pathlib import Path

from .entity_catalog import EntityDefinition, FieldDefinition, OperationDefinition

# Padroes SQL
_RE_CREATE_TABLE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:[\w]+\.)?(\w+)\s*\(([^;]+)\)",
    re.IGNORECASE,
)
_RE_COLUMN_DEF = re.compile(
    r"(\w+)\s+(\w+(?:\s*\([^)]*\))?)"  # nome tipo(tamanho)
    r"(?:\s+(NOT\s+NULL))?"              # NOT NULL
    r"(?:\s+(PRIMARY\s+KEY))?",          # PRIMARY KEY
    re.IGNORECASE,
)
_RE_INSERT = re.compile(
    r"INSERT\s+INTO\s+(?:[\w]+\.)?(\w+)\s*\(([^)]+)\)",
    re.IGNORECASE,
)
_RE_UPDATE = re.compile(
    r"UPDATE\s+(?:[\w]+\.)?(\w+)\s+SET\s+(.+?)(?:WHERE|$)",
    re.IGNORECASE | re.DOTALL,
)
_RE_SELECT = re.compile(
    r"SELECT\s+(.+?)\s+FROM\s+(?:[\w]+\.)?(\w+)",
    re.IGNORECASE | re.DOTALL,
)
_RE_DECLARE_CURSOR = re.compile(
    r"DECLARE\s+\w+\s+CURSOR\s+FOR\s+SELECT\s+(.+?)\s+FROM\s+(?:[\w]+\.)?(\w+)",
    re.IGNORECASE | re.DOTALL,
)
_RE_SQLEXEC_SELECT = re.compile(
    r"SQLEXEC\s*\([^,]+?\s*,\s*\[?\s*SELECT\s+(.+?)\s+FROM\s+(?:[\w]+\.)?(\w+)",
    re.IGNORECASE | re.DOTALL,
)
_RE_DELETE = re.compile(
    r"DELETE\s+FROM\s+(?:[\w]+\.)?(\w+)",
    re.IGNORECASE,
)
_RE_JOIN = re.compile(
    r"JOIN\s+(?:[\w]+\.)?(\w+)",
    re.IGNORECASE,
)
_RE_WHERE = re.compile(
    r"WHERE\s+(.+?)(?:ORDER BY|GROUP BY|LIMIT|HAVING|$)",
    re.IGNORECASE | re.DOTALL,
)


def _split_fields(fields_str: str) -> list[str]:
    """Extrai nomes de campos de uma string como 'NOME, CPF, TELEFONE'.
    Descarta expressoes SQL como funcoes DAY(), MONTH(), etc."""
    result: list[str] = []
    for f in fields_str.split(","):
        f = f.strip().strip('"').strip("'").strip("`")
        if not f:
            continue
        # Pular expressoes SQL: funcoes, operacoes, subqueries
        if re.search(r"[()]", f):
            # Se tem parenteses mas e alias, extrai alias: "DAY(x) AS alias" -> "alias"
            alias_m = re.search(r"\bAS\s+(\w+)\s*$", f, re.IGNORECASE)
            if alias_m:
                result.append(alias_m.group(1))
            continue
        # Pular literais e expressoes com operadores
        if re.search(r"^['\"\d]", f) or re.search(r"[+\-*/]", f):
            continue
        # Se tem 'AS alias', extrai alias
        as_m = re.search(r"\bAS\s+(\w+)\s*$", f, re.IGNORECASE)
        if as_m:
            result.append(as_m.group(1))
        else:
            # Remove qualificador de tabela: tabela.campo -> campo
            dot_parts = f.split(".")
            result.append(dot_parts[-1].strip())
    return result


def _extract_set_fields(set_str: str) -> list[str]:
    """Extrai nomes de campos de SET field=value, field=value."""
    result: list[str] = []
    for part in set_str.split(","):
        eq_idx = part.find("=")
        if eq_idx > 0:
            result.append(part[:eq_idx].strip().strip('"').strip("'").strip("`"))
    return result


def _extract_where_fields(where_str: str) -> list[str]:
    """Extrai campos referenciados em WHERE."""
    fields: list[str] = []
    # field = value ou field IN (...) ou field LIKE ...
    for m in re.finditer(r"(\w+)\s*(?:=|!=|<>|>=|<=|>|<|LIKE|IN|BETWEEN|IS)", where_str, re.IGNORECASE):
        fields.append(m.group(1))
    return fields


class SQLExtractor:
    """Extrai entidades e operacoes de comandos SQL."""

    @staticmethod
    def extract(content: str, source_file: str = "") -> list[EntityDefinition]:
        entities: dict[str, EntityDefinition] = {}
        lines = content.split("\n")

        # Primeiro: detecta CREATE TABLE multi-linha (junta linhas entre parenteses)
        joined = content  # usa conteudo completo para CREATE TABLE

        SQLExtractor._extract_create_table_multiline(joined, source_file, entities)

        for line_no, line in enumerate(lines, 1):
            SQLExtractor._extract_insert(line, line_no, source_file, entities)
            SQLExtractor._extract_update(line, line_no, source_file, entities)
            SQLExtractor._extract_declare_cursor(line, line_no, source_file, entities)
            SQLExtractor._extract_sqlexec(line, line_no, source_file, entities)
            SQLExtractor._extract_select(line, line_no, source_file, entities)
            SQLExtractor._extract_delete(line, line_no, source_file, entities)

        return list(entities.values())

    @staticmethod
    def _extract_create_table_multiline(
        content: str, source_file: str, entities: dict[str, EntityDefinition]
    ) -> None:
        """Extrai CREATE TABLE multi-linha do conteudo completo."""
        for m in _RE_CREATE_TABLE.finditer(content):
            table = m.group(1).upper()
            body = m.group(2)

            if table not in entities:
                entities[table] = EntityDefinition(name=table, storage_type="sql", source=source_file)
            ent = entities[table]

            for cm in _RE_COLUMN_DEF.finditer(body):
                col_name = cm.group(1).upper()
                col_type = (cm.group(2) or "TEXT").upper().strip()
                is_required = bool(cm.group(3))
                is_pk = bool(cm.group(4))

                if col_name in ("PRIMARY", "FOREIGN", "UNIQUE", "CONSTRAINT", "INDEX", "CHECK"):
                    continue

                if col_name.upper() not in {f.name.upper() for f in ent.fields}:
                    ent.fields.append(FieldDefinition(
                        name=col_name, datatype=col_type,
                        required=is_required, unique_flag=is_pk,
                        source_file=source_file, source_line=0,
                    ))

    @staticmethod
    def _extract_create_table(
        line: str, line_no: int, source_file: str, entities: dict[str, EntityDefinition]
    ) -> None:
        """Extrai entidades e campos de CREATE TABLE."""
        m = _RE_CREATE_TABLE.search(line)
        if not m:
            return
        table = m.group(1).upper()
        body = m.group(2)

        if table not in entities:
            entities[table] = EntityDefinition(name=table, storage_type="sql", source=source_file)
        ent = entities[table]

        for cm in _RE_COLUMN_DEF.finditer(body):
            col_name = cm.group(1).upper()
            col_type = (cm.group(2) or "TEXT").upper().strip()
            is_required = bool(cm.group(3))
            is_pk = bool(cm.group(4))

            if col_name in ("PRIMARY", "FOREIGN", "UNIQUE", "CONSTRAINT", "INDEX", "CHECK"):
                continue

            if col_name.upper() not in {f.name.upper() for f in ent.fields}:
                ent.fields.append(FieldDefinition(
                    name=col_name, datatype=col_type,
                    required=is_required, unique_flag=is_pk,
                    source_file=source_file, source_line=line_no,
                ))

    @staticmethod
    def _extract_insert(
        line: str, line_no: int, source_file: str, entities: dict[str, EntityDefinition]
    ) -> None:
        m = _RE_INSERT.search(line)
        if not m:
            return
        table = m.group(1).upper()
        fields = _split_fields(m.group(2))
        if table not in entities:
            entities[table] = EntityDefinition(name=table, storage_type="sql", source=source_file)
        ent = entities[table]
        for fname in fields:
            if fname.upper() not in {ff.name.upper() for ff in ent.fields}:
                ent.fields.append(FieldDefinition(name=fname, source_file=source_file, source_line=line_no))
        ent.operations.append(
            OperationDefinition(
                operation_type="insert",
                entity_name=table,
                fields=fields,
                source_file=source_file,
                line_number=line_no,
            )
        )

    @staticmethod
    def _extract_update(
        line: str, line_no: int, source_file: str, entities: dict[str, EntityDefinition]
    ) -> None:
        m = _RE_UPDATE.search(line)
        if not m:
            return
        table = m.group(1).upper()
        fields = _extract_set_fields(m.group(2))
        if table not in entities:
            entities[table] = EntityDefinition(name=table, storage_type="sql", source=source_file)
        ent = entities[table]
        for fname in fields:
            if fname.upper() not in {ff.name.upper() for ff in ent.fields}:
                ent.fields.append(FieldDefinition(name=fname, source_file=source_file, source_line=line_no))
        ent.operations.append(
            OperationDefinition(
                operation_type="update",
                entity_name=table,
                fields=fields,
                source_file=source_file,
                line_number=line_no,
            )
        )

    @staticmethod
    def _extract_select(
        line: str, line_no: int, source_file: str, entities: dict[str, EntityDefinition]
    ) -> None:
        m = _RE_SELECT.search(line)
        if not m:
            return
        table = m.group(2).upper()
        fields_str = m.group(1).strip()
        fields: list[str] = []
        if fields_str != "*":
            fields = _split_fields(fields_str)
        if table not in entities:
            entities[table] = EntityDefinition(name=table, storage_type="sql", source=source_file)
        ent = entities[table]
        for fname in fields:
            if fname.upper() not in {ff.name.upper() for ff in ent.fields}:
                ent.fields.append(FieldDefinition(name=fname, source_file=source_file, source_line=line_no))
        ent.operations.append(
            OperationDefinition(
                operation_type="select",
                entity_name=table,
                fields=fields,
                source_file=source_file,
                line_number=line_no,
            )
        )
        # JOINs
        for jm in _RE_JOIN.finditer(line):
            join_table = jm.group(1).upper()
            if join_table not in entities:
                entities[join_table] = EntityDefinition(name=join_table, storage_type="sql", source=source_file)

    @staticmethod
    def _extract_declare_cursor(
        line: str, line_no: int, source_file: str, entities: dict[str, EntityDefinition]
    ) -> None:
        m = _RE_DECLARE_CURSOR.search(line)
        if not m:
            return
        table = m.group(2).upper()
        fields_str = m.group(1).strip()
        fields: list[str] = []
        if fields_str != "*":
            fields = _split_fields(fields_str)
        if table not in entities:
            entities[table] = EntityDefinition(name=table, storage_type="sql", source=source_file)
        ent = entities[table]
        for fname in fields:
            if fname.upper() not in {ff.name.upper() for ff in ent.fields}:
                ent.fields.append(FieldDefinition(name=fname, source_file=source_file, source_line=line_no))
        ent.operations.append(
            OperationDefinition(
                operation_type="select",
                entity_name=table,
                fields=fields,
                source_file=source_file,
                line_number=line_no,
            )
        )

    @staticmethod
    def _extract_sqlexec(
        line: str, line_no: int, source_file: str, entities: dict[str, EntityDefinition]
    ) -> None:
        m = _RE_SQLEXEC_SELECT.search(line)
        if not m:
            return
        table = m.group(2).upper()
        fields_str = m.group(1).strip()
        fields: list[str] = []
        if fields_str != "*":
            fields = _split_fields(fields_str)
        if table not in entities:
            entities[table] = EntityDefinition(name=table, storage_type="sql", source=source_file)
        ent = entities[table]
        for fname in fields:
            if fname.upper() not in {ff.name.upper() for ff in ent.fields}:
                ent.fields.append(FieldDefinition(name=fname, source_file=source_file, source_line=line_no))
        ent.operations.append(
            OperationDefinition(
                operation_type="select",
                entity_name=table,
                fields=fields,
                source_file=source_file,
                line_number=line_no,
            )
        )

    @staticmethod
    def _extract_delete(
        line: str, line_no: int, source_file: str, entities: dict[str, EntityDefinition]
    ) -> None:
        m = _RE_DELETE.search(line)
        if not m:
            return
        table = m.group(1).upper()
        if table not in entities:
            entities[table] = EntityDefinition(name=table, storage_type="sql", source=source_file)
        entities[table].operations.append(
            OperationDefinition(
                operation_type="delete",
                entity_name=table,
                source_file=source_file,
                line_number=line_no,
            )
        )
