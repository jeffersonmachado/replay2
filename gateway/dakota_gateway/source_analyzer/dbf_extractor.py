from __future__ import annotations

import re

from .entity_catalog import EntityDefinition, FieldDefinition, OperationDefinition

# DBF especifico: comandos como USE, dbUseArea, CREATE TABLE, etc.
# Estes sao similares ao ISAM mas podem ter sintaxe especifica de driver DBF.
_RE_DBF_USE = re.compile(
    r"(?:dbUseArea|USE)\s*\(\s*(?:\w+\s*,\s*)?['\"](\w+)['\"]",
    re.IGNORECASE,
)
_RE_DBF_CREATE = re.compile(
    r"(?:dbCreate|CREATE)\s+(?:TABLE\s+)?['\"]?(\w+)['\"]?",
    re.IGNORECASE,
)
_RE_DBAPPEND = re.compile(r"dbAppend\s*\(\s*\)", re.IGNORECASE)
_RE_DBCOMMIT = re.compile(r"dbCommit\s*\(\s*\)", re.IGNORECASE)
_RE_DBGO = re.compile(r"dbGo(?:To|Bottom|Top)\s*\(\s*\)", re.IGNORECASE)


class DBFExtractor:
    """Extrai entidades de comandos DBF especificos (driver nativo)."""

    @staticmethod
    def extract(content: str, source_file: str = "") -> list[EntityDefinition]:
        entities: dict[str, EntityDefinition] = {}
        lines = content.split("\n")
        current_table: str | None = None

        for line_no, line in enumerate(lines, 1):
            um = _RE_DBF_USE.search(line)
            if um:
                table = um.group(1).upper()
                current_table = table
                if table not in entities:
                    entities[table] = EntityDefinition(
                        name=table, storage_type="dbf", source=source_file
                    )
                entities[table].operations.append(
                    OperationDefinition(
                        operation_type="use",
                        entity_name=table,
                        source_file=source_file,
                        line_number=line_no,
                    )
                )
                continue

            cm = _RE_DBF_CREATE.search(line)
            if cm:
                table = cm.group(1).upper()
                if table not in entities:
                    entities[table] = EntityDefinition(
                        name=table, storage_type="dbf", source=source_file
                    )
                entities[table].operations.append(
                    OperationDefinition(
                        operation_type="create",
                        entity_name=table,
                        source_file=source_file,
                        line_number=line_no,
                    )
                )
                continue

            if _RE_DBAPPEND.search(line) and current_table:
                entities.setdefault(
                    current_table,
                    EntityDefinition(name=current_table, storage_type="dbf", source=source_file),
                )
                entities[current_table].operations.append(
                    OperationDefinition(
                        operation_type="append",
                        entity_name=current_table,
                        source_file=source_file,
                        line_number=line_no,
                    )
                )

        return list(entities.values())
