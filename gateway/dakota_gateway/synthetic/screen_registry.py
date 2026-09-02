from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from .schema import FieldSchema, ScreenSchema
from ..db.connection import transaction


@dataclass
class ScreenRecord:
    id: Optional[int] = None
    screen_signature: str = ""
    title: str = ""
    program_name: str = ""
    created_at: str = ""
    updated_at: str = ""


@dataclass
class ScreenFieldRecord:
    id: Optional[int] = None
    screen_id: int = 0
    field_name: str = ""
    prompt: str = ""
    datatype: str = "text"
    required: bool = False
    unique_flag: bool = False
    lookup_table: Optional[str] = None
    constraints_json: Optional[str] = None


class ScreenRegistry:
    """Gerencia registro de telas e seus campos no banco SQLite."""

    def __init__(self, connection):
        """Recebe uma conexao SQLite com row_factory=sqlite3.Row."""
        self.con = connection

    # ------------------------------------------------------------------
    # Screens
    # ------------------------------------------------------------------

    def register_screen(
        self,
        screen_signature: str,
        title: str = "",
        program_name: str = "",
    ) -> int:
        now = datetime.now().isoformat()
        cur = self.con.execute(
            """INSERT INTO screens (screen_signature, title, program_name, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(screen_signature) DO UPDATE SET
                 title=excluded.title,
                 program_name=excluded.program_name,
                 updated_at=excluded.updated_at""",
            (screen_signature, title, program_name, now, now),
        )
        self.con.commit()
        row = self.con.execute(
            "SELECT id FROM screens WHERE screen_signature=?", (screen_signature,)
        ).fetchone()
        return row["id"] if row else 0

    def get_screen_by_signature(self, signature: str) -> Optional[ScreenRecord]:
        row = self.con.execute(
            "SELECT * FROM screens WHERE screen_signature=?", (signature,)
        ).fetchone()
        if row:
            return ScreenRecord(
                id=row["id"],
                screen_signature=row["screen_signature"],
                title=row["title"],
                program_name=row["program_name"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
        return None

    def get_screen_by_id(self, screen_id: int) -> Optional[ScreenRecord]:
        row = self.con.execute(
            "SELECT * FROM screens WHERE id=?", (screen_id,)
        ).fetchone()
        if row:
            return ScreenRecord(
                id=row["id"],
                screen_signature=row["screen_signature"],
                title=row["title"],
                program_name=row["program_name"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
        return None

    def list_screens(self) -> list[ScreenRecord]:
        rows = self.con.execute("SELECT * FROM screens ORDER BY title").fetchall()
        return [
            ScreenRecord(
                id=r["id"],
                screen_signature=r["screen_signature"],
                title=r["title"],
                program_name=r["program_name"],
                created_at=r["created_at"],
                updated_at=r["updated_at"],
            )
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Screen Fields
    # ------------------------------------------------------------------

    def register_field(self, field: ScreenFieldRecord) -> int:
        cur = self.con.execute(
            """INSERT INTO screen_fields
               (screen_id, field_name, prompt, datatype, required, unique_flag, lookup_table, constraints_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                field.screen_id,
                field.field_name,
                field.prompt,
                field.datatype,
                1 if field.required else 0,
                1 if field.unique_flag else 0,
                field.lookup_table,
                field.constraints_json,
            ),
        )
        self.con.commit()
        return cur.lastrowid or 0

    def register_fields_from_schema(self, screen_id: int, schema: ScreenSchema) -> list[int]:
        """Registra todos os campos da tela em UMA transação.

        Antes cada campo comitava sozinho (``register_field`` chama commit):
        N campos = N fsyncs. Aqui o loop roda dentro de um único BEGIN/COMMIT
        (execute por linha porque o retorno precisa do lastrowid de cada
        campo; o ganho vem de eliminar os N commits, não do executemany).
        """
        ids: list[int] = []
        with transaction(self.con):
            for fs in schema.fields:
                constraints = json.dumps(
                    {
                        "min_length": fs.min_length,
                        "max_length": fs.max_length,
                        "min_value": fs.min_value,
                        "max_value": fs.max_value,
                        "choices": fs.choices,
                        "format": fs.format,
                    },
                    ensure_ascii=False,
                    default=None,
                ) if any([fs.min_length, fs.max_length, fs.min_value, fs.max_value, fs.choices, fs.format]) else None
                cur = self.con.execute(
                    """INSERT INTO screen_fields
                       (screen_id, field_name, prompt, datatype, required, unique_flag, lookup_table, constraints_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        screen_id,
                        fs.name,
                        fs.prompt or fs.name,
                        fs.datatype,
                        1 if fs.required else 0,
                        1 if fs.unique else 0,
                        fs.lookup,
                        constraints,
                    ),
                )
                ids.append(cur.lastrowid or 0)
        return ids

    def get_fields_by_screen(self, screen_id: int) -> list[ScreenFieldRecord]:
        rows = self.con.execute(
            "SELECT * FROM screen_fields WHERE screen_id=? ORDER BY id", (screen_id,)
        ).fetchall()
        return [
            ScreenFieldRecord(
                id=r["id"],
                screen_id=r["screen_id"],
                field_name=r["field_name"],
                prompt=r["prompt"],
                datatype=r["datatype"],
                required=bool(r["required"]),
                unique_flag=bool(r["unique_flag"]),
                lookup_table=r["lookup_table"],
                constraints_json=r["constraints_json"],
            )
            for r in rows
        ]

    def get_screen_schema(self, screen_id: int) -> Optional[ScreenSchema]:
        screen = self.get_screen_by_id(screen_id)
        if not screen:
            return None
        fields = self.get_fields_by_screen(screen_id)
        return ScreenSchema(
            screen_id=str(screen.id),
            screen_signature=screen.screen_signature,
            title=screen.title,
            program_name=screen.program_name,
            fields=[
                FieldSchema(
                    name=f.field_name,
                    datatype=f.datatype,
                    required=f.required,
                    unique=f.unique_flag,
                    prompt=f.prompt,
                    lookup=f.lookup_table,
                    constraints_json=f.constraints_json,
                )
                for f in fields
            ],
        )
