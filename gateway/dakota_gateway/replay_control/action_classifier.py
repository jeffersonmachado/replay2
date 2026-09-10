"""Classificador determinístico de ações de terminal (motor adaptativo).

Cada ação de input recebe uma :class:`ActionClass` semântica. A classificação
é 100% determinística (mesmos bytes → mesma classe) e conservadora: qualquer
sequência não reconhecida é ``UNKNOWN``, que o safety guard trata como
bloqueio de otimização.

A classe semântica determina a política de sincronização:

- ``PRINTABLE_INPUT`` / ``FIELD_EDIT``: elegíveis a batching (com regras do
  safety guard);
- ``ENTER``/``TAB``/``ESC``/``FUNCTION_KEY``/``NAVIGATION``/``SUBMIT``/
  ``QUERY``/``SCREEN_TRANSITION``: barreiras — nunca fundidas em batch;
- ``EXPLICIT_WAIT``/``CHECKPOINT``: sincronização, jamais batch;
- ``UNKNOWN``: fallback conservador obrigatório.

``SUBMIT``/``QUERY``/``SCREEN_TRANSITION`` não são inferidas dos bytes — são
promoções de contexto feitas pelo scheduler (ex.: ENTER seguido de checkpoint
de outra tela). O classificador nunca "adivinha".
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class ActionClass(str, Enum):
    PRINTABLE_INPUT = "printable_input"
    FIELD_EDIT = "field_edit"
    TAB = "tab"
    ENTER = "enter"
    ESC = "esc"
    FUNCTION_KEY = "function_key"
    NAVIGATION = "navigation"
    SUBMIT = "submit"
    QUERY = "query"
    SCREEN_TRANSITION = "screen_transition"
    EXPLICIT_WAIT = "explicit_wait"
    CHECKPOINT = "checkpoint"
    UNKNOWN = "unknown"


#: Classes que jamais entram em batch (barreiras semânticas).
BARRIER_CLASSES = frozenset({
    ActionClass.TAB,
    ActionClass.ENTER,
    ActionClass.ESC,
    ActionClass.FUNCTION_KEY,
    ActionClass.NAVIGATION,
    ActionClass.SUBMIT,
    ActionClass.QUERY,
    ActionClass.SCREEN_TRANSITION,
    ActionClass.EXPLICIT_WAIT,
    ActionClass.CHECKPOINT,
    ActionClass.UNKNOWN,
})

#: Única classe elegível a batching na Fase 1 (conservador por desenho).
BATCHABLE_CLASSES = frozenset({ActionClass.PRINTABLE_INPUT})

# key_kind gravado na trilha sintética (replay_adapter) → classe.
_KEY_KIND_HINTS = {
    "printable": ActionClass.PRINTABLE_INPUT,
    "enter": ActionClass.ENTER,
    "tab": ActionClass.TAB,
    "esc": ActionClass.ESC,
    "function_key": ActionClass.FUNCTION_KEY,
    "navigation": ActionClass.NAVIGATION,
    "field_edit": ActionClass.FIELD_EDIT,
    "wait": ActionClass.EXPLICIT_WAIT,
}

# CSI/SS3 de function keys (F1–F12 nos modos xterm/VT): ESC O P-S,
# ESC [ 1 N ~, ESC [ 2 N ~.
_FUNCTION_KEY_RE = re.compile(rb"^\x1bO[P-Z]$|^\x1b\[(?:1[0-9]|2[0-4])\~$")
_NAVIGATION_RE = re.compile(rb"^\x1b\[[ABCD]$|^\x1bO[ABCD]$")


@dataclass(frozen=True)
class ClassifiedAction:
    action_class: ActionClass
    data: bytes
    reason: str

    @property
    def batchable(self) -> bool:
        return self.action_class in BATCHABLE_CLASSES

    @property
    def is_barrier(self) -> bool:
        return self.action_class in BARRIER_CLASSES


def _classify_raw(data: bytes) -> tuple[ActionClass, str]:
    if data in (b"\r", b"\n"):
        return ActionClass.ENTER, "byte CR/LF"
    if data == b"\t":
        return ActionClass.TAB, "byte TAB"
    if data == b"\x1b":
        return ActionClass.ESC, "ESC isolado"
    if data in (b"\x7f", b"\x08"):
        return ActionClass.FIELD_EDIT, "backspace/delete"
    if _FUNCTION_KEY_RE.match(data):
        return ActionClass.FUNCTION_KEY, "sequencia CSI/SS3 de function key"
    if _NAVIGATION_RE.match(data):
        return ActionClass.NAVIGATION, "sequencia de seta"
    if not data:
        return ActionClass.UNKNOWN, "payload vazio sem hint"
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return ActionClass.UNKNOWN, "utf-8 invalido"
    if text.isprintable():
        return ActionClass.PRINTABLE_INPUT, "texto imprimivel"
    return ActionClass.UNKNOWN, "controle nao reconhecido"


def classify_bytes(data: bytes, *, key_kind: str | None = None) -> ClassifiedAction:
    """Classifica os bytes de um evento de input.

    ``key_kind`` (gravado na trilha pelo replay_adapter) é um hint auditável:
    vale quando existe, mas bytes inconsistentes com o hint caem na
    classificação pelos bytes (o hint nunca reclassifica bytes que dizem
    outra coisa — ex.: ``key_kind="printable"`` com data ``\\r``).
    """
    by_bytes, reason = _classify_raw(data)
    if key_kind:
        hinted = _KEY_KIND_HINTS.get(str(key_kind).strip().lower())
        if hinted is not None:
            # hint "wait" é o único válido para payload vazio
            if hinted is ActionClass.EXPLICIT_WAIT:
                return ClassifiedAction(hinted, data, "key_kind=wait da trilha")
            if hinted is by_bytes:
                return ClassifiedAction(hinted, data, f"key_kind={key_kind} da trilha")
            # hint nunca reclassifica bytes que dizem outra coisa — nem
            # promove bytes desconhecidos (conservador: UNKNOWN permanece)
            return ClassifiedAction(by_bytes, data, f"bytes vencem hint ({reason})")
    return ClassifiedAction(by_bytes, data, reason)
