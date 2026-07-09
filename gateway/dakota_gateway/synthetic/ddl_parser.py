"""Parser de DDL SQL: CREATE TABLE -> ScreenSchema + EntityDefinition."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from .schema import ScreenSchema, FieldSchema
from ..source_analyzer.entity_catalog import EntityDefinition, FieldDefinition


@dataclass
class DDLParseResult:
    """Resultado do parsing de DDL."""
    entities: list[EntityDefinition] = field(default_factory=list)
    screen_schemas: list[ScreenSchema] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class DDLParser:
    """Extrai entidades e ScreenSchemas de arquivos DDL SQL."""

    _RE_CREATE_TABLE = re.compile(
        r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:[\w]+\.)?(\w+)\s*\((.*?)\);",
        re.IGNORECASE | re.DOTALL,
    )
    _RE_COLUMN = re.compile(
        r"^\s*(\w+)\s+(\w+)(?:\s*\(([^)]+)\))?",
        re.IGNORECASE,
    )
    _RE_PRIMARY_KEY = re.compile(
        r"PRIMARY\s+KEY\s*\(([^)]+)\)",
        re.IGNORECASE,
    )
    _RE_FOREIGN_KEY = re.compile(
        r"FOREIGN\s+KEY\s*\((\w+)\)\s*REFERENCES\s+(\w+)\s*\((\w+)\)",
        re.IGNORECASE,
    )
    _RE_UNIQUE = re.compile(r"UNIQUE", re.IGNORECASE)
    _RE_NOT_NULL = re.compile(r"NOT\s+NULL", re.IGNORECASE)
    _RE_DEFAULT = re.compile(r"DEFAULT\s+([^\s,]+)", re.IGNORECASE)
    _RE_CHECK = re.compile(r"CHECK\s*\((.+?)\)", re.IGNORECASE)

    # Mapa de tipos SQL -> datatype normalizado
    _TYPE_MAP = {
        "integer": "integer", "int": "integer", "bigint": "integer",
        "smallint": "integer", "tinyint": "integer", "serial": "integer",
        "numeric": "decimal", "decimal": "decimal", "float": "decimal",
        "double": "decimal", "real": "decimal", "money": "decimal",
        "varchar": "text", "char": "text", "character": "text",
        "text": "text", "clob": "text", "memo": "text", "blob": "text",
        "date": "date", "datetime": "datetime", "timestamp": "datetime",
        "time": "text", "boolean": "boolean", "bool": "boolean",
        "bit": "boolean",
    }

    def parse(self, ddl_content: str, source: str = "") -> DDLParseResult:
        """Analisa conteudo DDL SQL."""
        result = DDLParseResult()

        for match in self._RE_CREATE_TABLE.finditer(ddl_content):
            table_name = match.group(1)
            columns_block = match.group(2)

            entity = EntityDefinition(
                name=table_name,
                storage_type="sql",
                source=source,
            )
            screen_fields: list[FieldSchema] = []

            # Identificar PKs
            pk_match = self._RE_PRIMARY_KEY.search(columns_block)
            pk_columns: set[str] = set()
            if pk_match:
                pk_columns = {
                    c.strip().strip('"').strip("'").strip("`")
                    for c in pk_match.group(1).split(",")
                }

            # Identificar FKs
            fk_matches = self._RE_FOREIGN_KEY.finditer(columns_block)

            # Parse colunas
            lines = columns_block.split("\n")
            for line in lines:
                line = line.strip().rstrip(",")
                if not line or line.upper().startswith(("PRIMARY", "FOREIGN", "CONSTRAINT", "INDEX", "UNIQUE", "CHECK")):
                    continue

                col_match = self._RE_COLUMN.match(line)
                if not col_match:
                    continue

                col_name = col_match.group(1).strip().strip('"').strip("'").strip("`")
                col_type = col_match.group(2).strip().lower()

                field_def = FieldDefinition(
                    name=col_name,
                    datatype=self._normalize_type(col_type),
                    required=bool(self._RE_NOT_NULL.search(line)),
                    unique_flag=col_name in pk_columns or bool(self._RE_UNIQUE.search(line)),
                )
                entity.fields.append(field_def)

                # Criar FieldSchema para ScreenSchema
                screen_fields.append(FieldSchema(
                    name=col_name,
                    datatype=self._normalize_type(col_type),
                    required=field_def.required,
                    unique=field_def.unique_flag,
                    prompt=col_name.replace("_", " ").title(),
                    min_length=0,
                    max_length=self._extract_length(col_type),
                ))

            result.entities.append(entity)
            result.screen_schemas.append(ScreenSchema(
                screen_signature=f"table_{table_name.lower()}",
                title=table_name,
                program_name=table_name,
                fields=screen_fields,
            ))

        return result

    def parse_file(self, file_path: str) -> DDLParseResult:
        """Analisa arquivo DDL SQL."""
        try:
            content = open(file_path, encoding="utf-8", errors="replace").read()
        except Exception:
            return DDLParseResult()
        return self.parse(content, source=file_path)

    def parse_directory(self, dir_path: str) -> DDLParseResult:
        """Analisa todos os arquivos .sql em um diretorio."""
        from pathlib import Path
        result = DDLParseResult()
        base = Path(dir_path)
        for sql_file in sorted(base.rglob("*.sql")):
            file_result = self.parse_file(str(sql_file))
            result.entities.extend(file_result.entities)
            result.screen_schemas.extend(file_result.screen_schemas)
            result.warnings.extend(file_result.warnings)
        return result

    def _normalize_type(self, sql_type: str) -> str:
        return self._TYPE_MAP.get(sql_type.lower().split("(")[0], "text")

    @staticmethod
    def _extract_length(sql_type: str) -> int:
        match = re.search(r"\((\d+)", sql_type)
        return int(match.group(1)) if match else 0
