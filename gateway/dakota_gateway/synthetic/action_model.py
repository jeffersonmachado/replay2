"""Modelo estruturado de ações sintéticas (jornada → ações executáveis).

Converte o script textual de replay (``JourneyBuilder.generate_replay_script``)
em uma sequência de :class:`SyntheticAction` tipada, preservando a semântica
que antes se perdia na transformação para ``list[str]``:

- ``INPUT`` — texto imprimível de um campo (nunca recebe ENTER implícito);
- ``KEY`` — tecla especial com bytes exatos (ENTER, TAB, ESC, F1–F12, setas);
- ``WAIT`` — espera explícita declarada pela jornada (``{WAIT:ms}``);
- ``CHECKPOINT`` — verificação de estado declarada (``{VERIFY:sig}``);
- ``BARRIER`` — ponto de sincronização sem bytes (reservado para políticas).

Regras absolutas:

- a confirmação (ENTER/TAB/F-key) só existe quando o script a declara
  explicitamente — o parser nunca inventa bytes;
- tecla desconhecida é erro de síntese (``ValueError``), nunca vira
  ``\\rKEY\\r`` na trilha;
- cada ação carrega ``origin`` (passo/ação/tela da jornada) para auditoria.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum


class SyntheticActionKind(str, Enum):
    INPUT = "input"
    KEY = "key"
    WAIT = "wait"
    BARRIER = "barrier"
    CHECKPOINT = "checkpoint"


# Bytes exatos por tecla — fonte única (antes duplicado, incompleto e com
# fallback ``\r<nome>\r`` no replay_adapter e no remote_executor).
KEY_BYTES: dict[str, bytes] = {
    "ENTER": b"\r",
    "TAB": b"\t",
    "ESC": b"\x1b",
    "BACKSPACE": b"\x7f",
    "UP": b"\x1b[A",
    "DOWN": b"\x1b[B",
    "RIGHT": b"\x1b[C",
    "LEFT": b"\x1b[D",
    "F1": b"\x1bOP",
    "F2": b"\x1bOQ",
    "F3": b"\x1bOR",
    "F4": b"\x1bOS",
    "F5": b"\x1b[15~",
    "F6": b"\x1b[17~",
    "F7": b"\x1b[18~",
    "F8": b"\x1b[19~",
    "F9": b"\x1b[20~",
    "F10": b"\x1b[21~",
    "F11": b"\x1b[23~",
    "F12": b"\x1b[24~",
}


@dataclass(frozen=True)
class SyntheticAction:
    """Ação executável da jornada, com bytes exatos e metadados de origem."""

    kind: SyntheticActionKind
    data: bytes = b""
    key_name: str = ""
    wait_ms: int = 0
    signature: str = ""
    label: str = ""
    origin: dict = field(default_factory=dict)

    @property
    def is_barrier(self) -> bool:
        """Ações que nunca podem ser fundidas em batch com vizinhas."""
        return self.kind in (
            SyntheticActionKind.KEY,
            SyntheticActionKind.WAIT,
            SyntheticActionKind.BARRIER,
            SyntheticActionKind.CHECKPOINT,
        )


_STEP_RE = re.compile(r"^#\s*---\s*Passo\s+(\d+):\s*(.*?)\s*---\s*$")
_ACTION_RE = re.compile(r"^#\s*Ação:\s*([^|]*?)\s*(?:\|\s*Trigger:\s*(.*))?$")
_MARKER_RE = re.compile(r"^\{([A-Z]+)(?::(.*))?\}$")


def parse_replay_script(script: str) -> list[SyntheticAction]:
    """Converte o script textual de replay em ações estruturadas.

    Determinístico: mesma entrada → mesma lista de ações. Marcadores
    reconhecidos: ``{KEY:nome}``, ``{ENTER}`` (legado), ``{WAIT:ms}`` e
    ``{VERIFY:assinatura}``. Comentários ``# --- Passo N: título ---`` e
    ``# Ação: x | Trigger: y`` alimentam ``origin``.
    """
    actions: list[SyntheticAction] = []
    origin: dict = {}

    for raw_line in script.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            step_match = _STEP_RE.match(line)
            if step_match:
                origin = {
                    "step_order": int(step_match.group(1)),
                    "screen_title": step_match.group(2),
                }
                continue
            action_match = _ACTION_RE.match(line)
            if action_match:
                origin["step_action"] = action_match.group(1).strip()
                trigger = (action_match.group(2) or "").strip()
                if trigger:
                    origin["trigger"] = trigger
            continue

        marker = _MARKER_RE.match(line)
        if marker:
            tag, arg = marker.group(1), (marker.group(2) or "")
            if tag == "KEY":
                key_name = arg.strip().upper()
                data = KEY_BYTES.get(key_name)
                if data is None:
                    raise ValueError(f"tecla desconhecida no script de replay: {arg!r}")
                actions.append(SyntheticAction(
                    kind=SyntheticActionKind.KEY, data=data,
                    key_name=key_name, origin=dict(origin),
                ))
            elif tag == "ENTER":  # marcador legado ({ENTER})
                actions.append(SyntheticAction(
                    kind=SyntheticActionKind.KEY, data=KEY_BYTES["ENTER"],
                    key_name="ENTER", origin=dict(origin),
                ))
            elif tag == "WAIT":
                try:
                    wait_ms = int(arg)
                except ValueError as exc:
                    raise ValueError(f"WAIT inválido no script de replay: {arg!r}") from exc
                if wait_ms < 0:
                    raise ValueError(f"WAIT negativo no script de replay: {arg!r}")
                actions.append(SyntheticAction(
                    kind=SyntheticActionKind.WAIT, wait_ms=wait_ms,
                    label=f"{{WAIT:{wait_ms}}}", origin=dict(origin),
                ))
            elif tag == "VERIFY":
                actions.append(SyntheticAction(
                    kind=SyntheticActionKind.CHECKPOINT, signature=arg.strip(),
                    label=f"{{VERIFY:{arg.strip()}}}", origin=dict(origin),
                ))
            else:
                raise ValueError(f"marcador desconhecido no script de replay: {line!r}")
            continue

        actions.append(SyntheticAction(
            kind=SyntheticActionKind.INPUT,
            data=line.encode("utf-8"),
            origin=dict(origin),
        ))

    return actions
