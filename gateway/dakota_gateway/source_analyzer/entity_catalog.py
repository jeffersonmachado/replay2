from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class FieldDefinition:
    name: str
    datatype: str = "text"
    required: bool = False
    unique_flag: bool = False
    lookup_table: Optional[str] = None
    constraints_json: Optional[str] = None
    prompt: Optional[str] = None
    min_length: Optional[int] = None
    max_length: Optional[int] = None
    validation_rules: list[str] = field(default_factory=list)
    source_file: Optional[str] = None
    source_line: Optional[int] = None
    # ── Campos adicionados no P2 para rastreabilidade semantica ──
    semantic_type: Optional[str] = None       # cpf, cnpj, email, phone, date...
    confidence: float = 0.0                   # confianca da inferencia 0.0-1.0
    row: Optional[int] = None                 # linha na tela (@ row,col)
    col: Optional[int] = None                 # coluna na tela (@ row,col)
    picture: Optional[str] = None             # mascara PICTURE
    valid_expr: Optional[str] = None          # expressao VALID
    when_expr: Optional[str] = None           # expressao WHEN
    source_evidence: list[str] = field(default_factory=list)  # evidencias da inferencia


@dataclass
class EntityDefinition:
    name: str
    storage_type: str = "unknown"  # sql, isam, dbf, recital
    source: str = ""
    fields: list[FieldDefinition] = field(default_factory=list)
    indexes: list[dict] = field(default_factory=list)
    operations: list[OperationDefinition] = field(default_factory=list)
    metadata_json: Optional[str] = None


@dataclass
class OperationDefinition:
    operation_type: str  # insert, update, delete, select, seek, locate, append, replace, scatter, gather
    entity_name: str
    fields: list[str] = field(default_factory=list)
    source_file: Optional[str] = None
    line_number: Optional[int] = None
    conditions: list[str] = field(default_factory=list)


@dataclass
class ScreenDefinition:
    screen_id: Optional[str] = None
    screen_signature: str = ""
    title: str = ""
    program_name: str = ""
    fields: list[FieldDefinition] = field(default_factory=list)
    source_file: Optional[str] = None
    source_lines: tuple[int, int] = (0, 0)
