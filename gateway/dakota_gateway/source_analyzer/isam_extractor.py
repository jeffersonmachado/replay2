from __future__ import annotations

import re

from .entity_catalog import EntityDefinition, FieldDefinition, OperationDefinition

# ISAM / DBF / xBase classico:
# USE <arquivo> [ALIAS <alias>] [SHARED] [EXCLUSIVE] [INDEX <idx>] [ORDER <tag>]
_RE_USE = re.compile(
    r"USE\s+(\w+)(?:\s+ALIAS\s+(\w+))?(?:\s+SHARED|EXCLUSIVE)?(?:\s+IN\s+\d+)?",
    re.IGNORECASE,
)
_RE_SELECT_AREA = re.compile(
    r"(?:SELECT|SELE)\s+(\w+)",
    re.IGNORECASE,
)
_RE_APPEND_BLANK = re.compile(r"APPEND\s+BLANK", re.IGNORECASE)
_RE_REPLACE = re.compile(
    r"REPLACE\s+(.+?)(?:\s+FOR\s+.+?)?(?:\s+WHILE\s+.+?)?(?:\s+ALL|NEXT\s+\d+|RECORD\s+\d+|REST)?\s*$",
    re.IGNORECASE,
)
_RE_SEEK = re.compile(r"SEEK\s+(\w+)", re.IGNORECASE)
_RE_SET_ORDER = re.compile(r"SET\s+ORDER\s+TO\s+(\w+)", re.IGNORECASE)
_RE_INDEX_ON = re.compile(r"INDEX\s+ON\s+(\w+)\s+TAG\s+(\w+)", re.IGNORECASE)
_RE_LOCATE = re.compile(r"LOCATE\s+FOR\s+(.+)", re.IGNORECASE)
_RE_SCATTER = re.compile(r"SCATTER\s+(?:MEMVAR|NAME\s+\w+)", re.IGNORECASE)
_RE_GATHER = re.compile(r"GATHER\s+(?:MEMVAR|FROM\s+\w+)", re.IGNORECASE)
_RE_DBSEEK = re.compile(r"DBSEEK\s*\(\s*(\w+)", re.IGNORECASE)
_RE_FIELDGET = re.compile(r"FIELDGET\s*\(\s*(\d+)", re.IGNORECASE)
_RE_FIELDPUT = re.compile(r"FIELDPUT\s*\(\s*(\d+)\s*,\s*(\w+)", re.IGNORECASE)
_RE_GO_TOP = re.compile(r"GO\s+TOP", re.IGNORECASE)
_RE_GO_BOTTOM = re.compile(r"GO\s+BOTTOM", re.IGNORECASE)
_RE_DELETE = re.compile(r"\bDELETE\b", re.IGNORECASE)
_RE_PACK = re.compile(r"\bPACK\b", re.IGNORECASE)
_RE_ZAP = re.compile(r"\bZAP\b", re.IGNORECASE)
_RE_RECALL = re.compile(r"\bRECALL\b", re.IGNORECASE)


def _split_replace_fields(replace_str: str) -> list[str]:
    """Extrai FIELD WITH expr de REPLACE FIELD1 WITH x, FIELD2 WITH y."""
    fields: list[str] = []
    for part in replace_str.split(","):
        part = part.strip()
        m = re.match(r"(\w+)\s+WITH", part, re.IGNORECASE)
        if m:
            fields.append(m.group(1))
    return fields


def _extract_locate_fields(locate_str: str) -> list[str]:
    """Extrai campos de LOCATE FOR FIELD = expr."""
    fields: list[str] = []
    for m in re.finditer(r"(\w+)\s*(?:=|!=|>=|<=|>|<|\$)", locate_str):
        fields.append(m.group(1))
    return fields


class ISAMExtractor:
    """Extrai entidades de comandos ISAM/xBase: USE, SELECT, APPEND BLANK, REPLACE, SEEK, LOCATE, INDEX."""

    @staticmethod
    def extract(content: str, source_file: str = "") -> list[EntityDefinition]:
        entities: dict[str, EntityDefinition] = {}
        lines = content.split("\n")

        # Mapa: numero de area de trabalho -> nome da tabela atual
        current_area: dict[int, str] = {}
        current_table: str | None = None
        current_alias: dict[str, str] = {}  # alias -> table

        for line_no, line in enumerate(lines, 1):
            # USE <arquivo> [ALIAS <alias>]
            um = _RE_USE.search(line)
            if um:
                table = um.group(1).upper()
                alias = um.group(2).upper() if um.group(2) else table
                current_table = table
                current_area[0] = table
                current_alias[alias] = table
                if table not in entities:
                    entities[table] = EntityDefinition(
                        name=table, storage_type="isam", source=source_file
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

            # SELECT/SELE <area>
            sm = _RE_SELECT_AREA.match(line)
            if sm and not re.search(r"\bFROM\b", line, re.IGNORECASE) and not re.search(r"SQLEXEC|DECLARE|CURSOR", line, re.IGNORECASE):
                area_name = sm.group(1).upper()
                area_num = 0
                try:
                    area_num = int(area_name)
                except ValueError:
                    area_num = 0
                if area_num in current_area:
                    current_table = current_area[area_num]
                elif area_name in current_alias:
                    current_table = current_alias[area_name]
                continue

            # APPEND BLANK
            if _RE_APPEND_BLANK.search(line) and current_table:
                entities.setdefault(
                    current_table,
                    EntityDefinition(name=current_table, storage_type="isam", source=source_file),
                )
                entities[current_table].operations.append(
                    OperationDefinition(
                        operation_type="append",
                        entity_name=current_table,
                        source_file=source_file,
                        line_number=line_no,
                    )
                )
                continue

            # REPLACE FIELD WITH ...
            rm = _RE_REPLACE.search(line)
            if rm and current_table:
                fields = _split_replace_fields(rm.group(1))
                entities.setdefault(
                    current_table,
                    EntityDefinition(name=current_table, storage_type="isam", source=source_file),
                )
                ent = entities[current_table]
                for fname in fields:
                    if fname.upper() not in {ff.name.upper() for ff in ent.fields}:
                        ent.fields.append(
                            FieldDefinition(name=fname, source_file=source_file, source_line=line_no)
                        )
                # Se teve APPEND BLANK recente (mesma linha ou linhas proximas), eh insert
                recent_ops = ent.operations[-3:] if len(ent.operations) >= 3 else ent.operations
                op_type = "update"
                for op in recent_ops:
                    if op.operation_type == "append":
                        op_type = "insert"
                        break
                ent.operations.append(
                    OperationDefinition(
                        operation_type=op_type,
                        entity_name=current_table,
                        fields=fields,
                        source_file=source_file,
                        line_number=line_no,
                    )
                )
                continue

            # SET ORDER TO <tag>
            om = _RE_SET_ORDER.search(line)
            if om:
                tag = om.group(1).upper()
                if current_table:
                    entities.setdefault(
                        current_table,
                        EntityDefinition(name=current_table, storage_type="isam", source=source_file),
                    )
                    # Registra como indice de busca
                    exists = any(
                        idx.get("field") == tag or idx.get("index") == tag
                        for idx in entities[current_table].indexes
                    )
                    if not exists:
                        entities[current_table].indexes.append({"field": tag, "index": tag})
                        # Adiciona campo se nao existir
                        if tag.upper() not in {ff.name.upper() for ff in entities[current_table].fields}:
                            entities[current_table].fields.append(
                                FieldDefinition(name=tag, source_file=source_file, source_line=line_no)
                            )
                continue

            # SEEK <expr>
            skm = _RE_SEEK.search(line)
            if skm and current_table:
                entities.setdefault(
                    current_table,
                    EntityDefinition(name=current_table, storage_type="isam", source=source_file),
                )
                entities[current_table].operations.append(
                    OperationDefinition(
                        operation_type="seek",
                        entity_name=current_table,
                        fields=[skm.group(1)],
                        source_file=source_file,
                        line_number=line_no,
                    )
                )
                continue

            # INDEX ON <field> TAG <tag>
            im = _RE_INDEX_ON.search(line)
            if im:
                field = im.group(1).upper()
                tag = im.group(2).upper()
                if current_table:
                    entities.setdefault(
                        current_table,
                        EntityDefinition(name=current_table, storage_type="isam", source=source_file),
                    )
                    exists = any(
                        idx.get("field") == field and idx.get("index") == tag
                        for idx in entities[current_table].indexes
                    )
                    if not exists:
                        entities[current_table].indexes.append({"field": field, "index": tag})
                        if field.upper() not in {ff.name.upper() for ff in entities[current_table].fields}:
                            entities[current_table].fields.append(
                                FieldDefinition(name=field, source_file=source_file, source_line=line_no)
                            )
                continue

            # LOCATE FOR <cond>
            lm = _RE_LOCATE.search(line)
            if lm and current_table:
                fields = _extract_locate_fields(lm.group(1))
                entities.setdefault(
                    current_table,
                    EntityDefinition(name=current_table, storage_type="isam", source=source_file),
                )
                entities[current_table].operations.append(
                    OperationDefinition(
                        operation_type="locate",
                        entity_name=current_table,
                        fields=fields,
                        conditions=[lm.group(1).strip()],
                        source_file=source_file,
                        line_number=line_no,
                    )
                )
                for fname in fields:
                    if fname.upper() not in {ff.name.upper() for ff in entities[current_table].fields}:
                        entities[current_table].fields.append(
                            FieldDefinition(name=fname, source_file=source_file, source_line=line_no)
                        )
                continue

            # SCATTER MEMVAR
            if _RE_SCATTER.search(line) and current_table:
                entities.setdefault(
                    current_table,
                    EntityDefinition(name=current_table, storage_type="isam", source=source_file),
                )
                entities[current_table].operations.append(
                    OperationDefinition(
                        operation_type="scatter",
                        entity_name=current_table,
                        source_file=source_file,
                        line_number=line_no,
                    )
                )
                continue

            # GATHER MEMVAR
            if _RE_GATHER.search(line) and current_table:
                entities.setdefault(
                    current_table,
                    EntityDefinition(name=current_table, storage_type="isam", source=source_file),
                )
                entities[current_table].operations.append(
                    OperationDefinition(
                        operation_type="gather",
                        entity_name=current_table,
                        source_file=source_file,
                        line_number=line_no,
                    )
                )
                continue

            # DELETE
            if _RE_DELETE.search(line) and current_table:
                entities.setdefault(
                    current_table,
                    EntityDefinition(name=current_table, storage_type="isam", source=source_file),
                )
                entities[current_table].operations.append(
                    OperationDefinition(
                        operation_type="delete",
                        entity_name=current_table,
                        source_file=source_file,
                        line_number=line_no,
                    )
                )
                continue

            # PACK
            if _RE_PACK.search(line) and current_table:
                entities.setdefault(
                    current_table,
                    EntityDefinition(name=current_table, storage_type="isam", source=source_file),
                )
                entities[current_table].operations.append(
                    OperationDefinition(
                        operation_type="pack",
                        entity_name=current_table,
                        source_file=source_file,
                        line_number=line_no,
                    )
                )
                continue

            # ZAP
            if _RE_ZAP.search(line) and current_table:
                entities.setdefault(
                    current_table,
                    EntityDefinition(name=current_table, storage_type="isam", source=source_file),
                )
                entities[current_table].operations.append(
                    OperationDefinition(
                        operation_type="zap",
                        entity_name=current_table,
                        source_file=source_file,
                        line_number=line_no,
                    )
                )
                continue

            # DBSEEK(expr)
            dm = _RE_DBSEEK.search(line)
            if dm and current_table:
                entities.setdefault(
                    current_table,
                    EntityDefinition(name=current_table, storage_type="isam", source=source_file),
                )
                entities[current_table].operations.append(
                    OperationDefinition(
                        operation_type="dbseek",
                        entity_name=current_table,
                        fields=[dm.group(1)],
                        source_file=source_file,
                        line_number=line_no,
                    )
                )
                continue

            # FIELDGET / FIELDPUT
            fm_get = _RE_FIELDGET.search(line)
            fm_put = _RE_FIELDPUT.search(line)
            if current_table:
                if fm_get:
                    entities.setdefault(
                        current_table,
                        EntityDefinition(name=current_table, storage_type="isam", source=source_file),
                    )
                    entities[current_table].operations.append(
                        OperationDefinition(
                            operation_type="fieldget",
                            entity_name=current_table,
                            fields=[fm_get.group(1)],
                            source_file=source_file,
                            line_number=line_no,
                        )
                    )
                if fm_put:
                    entities.setdefault(
                        current_table,
                        EntityDefinition(name=current_table, storage_type="isam", source=source_file),
                    )
                    entities[current_table].operations.append(
                        OperationDefinition(
                            operation_type="fieldput",
                            entity_name=current_table,
                            fields=[fm_put.group(1), fm_put.group(2)],
                            source_file=source_file,
                            line_number=line_no,
                        )
                    )

        return list(entities.values())
