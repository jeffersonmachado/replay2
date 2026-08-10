#!/usr/bin/env python3
"""Parametriza capturas .jsonl existentes para replay com dados sintéticos."""
from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .template_engine import TemplateEngine


@dataclass
class CaptureTemplate:
    """Template extraído de uma captura, pronto para replay parametrizado."""
    capture_source: str = ""  # arquivo .jsonl de origem
    session_id: str = ""
    screen_sequence: list[str] = field(default_factory=list)  # screen_sigs ordenadas
    input_templates: list[str] = field(default_factory=list)  # templates por input
    screen_contexts: list[dict] = field(default_factory=list)  # contexto de cada tela
    metadata: dict = field(default_factory=dict)


@dataclass
class ParametrizedSession:
    """Uma sessão de replay com dados sintéticos preenchidos."""
    session_index: int = 0
    inputs: list[str] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)


class CaptureParametrizer:
    """Transforma capturas .jsonl em templates parametrizáveis com dados sintéticos."""

    def __init__(self):
        self.template_engine = TemplateEngine()

    @staticmethod
    def _split_key_events(text: str) -> list[str]:
        """Divide um bloco de input em texto corrido e teclas de controle.

        ``deterministic_input`` traz o input inteiro da tela estável (ex.:
        ``"1\\r"``); para parametrizar/replays precisamos da sequência
        ``["1", "{KEY:ENTER}"]``. Sequências ANSI (``\\x1b[...``) viram
        ``{KEY:...}`` como no fluxo de ``key_text``.
        """
        parts: list[str] = []
        buf: list[str] = []
        i = 0
        n = len(text)

        def flush() -> None:
            if buf:
                parts.append("".join(buf))
                buf.clear()

        while i < n:
            ch = text[i]
            if ch in ("\r", "\n"):
                flush()
                parts.append("{KEY:ENTER}")
            elif ch == "\t":
                flush()
                parts.append("{KEY:TAB}")
            elif ch == "\x1b":
                flush()
                m = re.match(r"^\x1b\[[A-Za-z]?", text[i:])
                if m and len(m.group(0)) > 2:
                    parts.append("{KEY:" + m.group(0)[2:] + "}")
                    i += len(m.group(0)) - 1
                else:
                    parts.append("{KEY:ESC}")
                    if m:
                        i += len(m.group(0)) - 1
            else:
                buf.append(ch)
            i += 1
        flush()
        return parts

    # ------------------------------------------------------------------
    # Análise de captura
    # ------------------------------------------------------------------

    def analyze_capture(self, jsonl_path: str) -> CaptureTemplate:
        """Analisa arquivo .jsonl e extrai templates de input e telas."""
        path = Path(jsonl_path)
        template = CaptureTemplate(capture_source=str(path))

        if not path.exists():
            return template

        screens: list[dict] = []
        inputs: list[str] = []
        keystroke: list[bool] = []  # True = token de tecla ecoada (det_input)
        cursors: list[tuple[int, int] | None] = []  # cursor por token (pré-fusão)
        session_id = ""
        current_input_start: int = 0
        term_state = None   # engine alimentada pelo fluxo OUT (bytes do host)
        term_broken = False

        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if not isinstance(event, dict):
                    continue

                event_type = event.get("type", "")
                if not session_id:
                    session_id = event.get("session_id", "")

                # O fluxo OUT (bytes do host) alimenta uma engine de terminal
                # viva: o cursor no instante de cada deterministic_input é a
                # posição do campo em digitação. O screen_raw da tela estável
                # NÃO serve para isso — a aplicação estaciona o cursor no
                # canto (24,79) após o redraw; os posicionamentos reais
                # (ESC[r;cH antes de cada campo) só existem no fluxo bruto.
                if event_type == "bytes" and event.get("data_b64") and \
                        (event.get("dir") or event.get("direction")) == "out":
                    if term_state is None and not term_broken:
                        try:
                            from ..screen import TerminalScreenState
                            term_state = TerminalScreenState(
                                rows=int(event.get("rows") or 25),
                                cols=int(event.get("cols") or 80),
                                encoding=str(event.get("encoding") or "utf-8"),
                            )
                        except Exception:
                            term_broken = True
                    if term_state is not None:
                        try:
                            term_state.feed_bytes(base64.b64decode(
                                str(event["data_b64"])))
                        except Exception:
                            pass  # frame ruim não pode derrubar a análise

                # Coletar screen signatures
                if event_type == "checkpoint" and event.get("screen_sig"):
                    screens.append({
                        "screen_sig": event.get("screen_sig", ""),
                        "screen_sample": event.get("screen_sample", ""),
                        "norm_len": event.get("norm_len", 0),
                        "seq_global": event.get("seq_global", 0),
                        "input_start": len(inputs),
                    })

                # Trilha auditável do gateway: tela estável + input no mesmo
                # evento deterministic_input (screen_sig + key_text/key_b64).
                # Sem este ramo, capturas reais viravam template vazio e a
                # síntese dependia de conversor manual fora do fluxo oficial.
                if event_type == "deterministic_input":
                    sig = event.get("screen_sig") or ""
                    if sig and (not screens
                                or screens[-1].get("screen_sig") != sig):
                        screens.append({
                            "screen_sig": sig,
                            "screen_sample": event.get("screen_sample", ""),
                            "norm_len": event.get("norm_len", 0),
                            "seq_global": event.get("seq_global", 0),
                            "input_start": len(inputs),
                        })
                    # key_b64 é a fonte canônica dos bytes reais; key_text é
                    # a forma "display" ESCAPADA ('\r' literal, backslash+r).
                    # Preferir o b64 — senão ENTERs da trilha real viram
                    # texto '\r' e quebram a fusão de teclas e a navegação.
                    key = None
                    if event.get("key_b64"):
                        try:
                            key = base64.b64decode(
                                str(event["key_b64"])).decode("utf-8", "replace")
                        except Exception:
                            key = None
                    if key is None:
                        key = event.get("key_text")
                    if key:
                        parts = self._split_key_events(str(key))
                        inputs.extend(parts)
                        keystroke.extend([True] * len(parts))
                        pos = (term_state.r, term_state.c) \
                            if term_state is not None else None
                        cursors.extend([pos] * len(parts))

                # Coletar inputs (key_text)
                if event_type in ("bytes", "checkpoint") and event.get("key_text"):
                    key_text = str(event.get("key_text", ""))
                    if key_text in ("\r", "\n"):
                        inputs.append("{KEY:ENTER}")
                        keystroke.append(False)
                    elif key_text == "\t":
                        inputs.append("{KEY:TAB}")
                        keystroke.append(False)
                    elif key_text == "\x1b":
                        inputs.append("{KEY:ESC}")
                        keystroke.append(False)
                    elif re.match(r"^\x1b\[", key_text):
                        inputs.append("{KEY:" + key_text[2:] + "}")
                        keystroke.append(False)
                    elif key_text == "":
                        continue
                    else:
                        inputs.append(key_text)
                        keystroke.append(False)
                    # fluxo bytes/checkpoint não tem posição de campo
                    while len(cursors) < len(inputs):
                        cursors.append(None)

        # Fecha input_end de cada tela
        for i, screen in enumerate(screens):
            if i + 1 < len(screens):
                screen["input_end"] = screens[i + 1]["input_start"]
            else:
                screen["input_end"] = len(inputs)
            # Adiciona os inputs daquela tela (+ posição de cursor de cada um:
            # a do 1º token fundido, que é onde o campo começa na tela)
            start = screen.get("input_start", 0)
            end = screen.get("input_end", len(inputs))
            groups = self._coalesce_groups(
                inputs[start:end], keystroke[start:end], cursors[start:end])
            screen["inputs"] = [
                "".join(str(inputs[start + k]) for k in g) for g in groups]
            screen["input_positions"] = [
                cursors[start + g[0]] if start + g[0] < len(cursors) else None
                for g in groups
            ]

        template.session_id = session_id
        template.screen_sequence = [s["screen_sig"] for s in screens]
        template.screen_contexts = screens
        coalesced = [inp for s in screens for inp in s["inputs"]]
        template.input_templates = self.template_engine.detect_placeholders(coalesced)
        template.metadata = {
            "total_screens": len(screens),
            "total_inputs": len(coalesced),
            "original_inputs": coalesced,
        }

        return template

    @staticmethod
    def _coalesce_groups(tokens: list[str],
                         keystroke: list[bool] | None = None,
                         cursors: list | None = None) -> list[list[int]]:
        """Agrupa os índices dos tokens que formam um único input de campo.

        Mesma regra de ``_coalesce_printable``, mas devolvendo grupos de
        índices — permite derivar metadados paralelos (ex.: posição do
        cursor) sem duplicar a lógica de fusão.

        Quando ``cursors`` é informado, um salto de cursor quebra o grupo:
        máscaras de edição (``@R 999.999.999-99``) avançam o cursor 1-2
        colunas, mas um salto maior (ou mudança de linha) é a aplicação
        auto-avançando para OUTRO campo sem ENTER — fundir os dois campos
        num token só misturaria valores de campos diferentes (captura 13:
        ``'15'``+``'229,9'`` viravam ``'15229,9'``).
        """
        if keystroke is None:
            keystroke = [True] * len(tokens)
        groups: list[list[int]] = []
        buf: list[int] = []
        for i, (tok, is_keystroke) in enumerate(zip(tokens, keystroke)):
            tok = str(tok)
            if is_keystroke and len(tok) == 1 and not tok.startswith("{KEY:"):
                if buf and cursors is not None:
                    prev = cursors[buf[-1]] if buf[-1] < len(cursors) else None
                    cur = cursors[i] if i < len(cursors) else None
                    if prev is not None and cur is not None and (
                            cur[0] != prev[0]
                            or not 0 < cur[1] - prev[1] <= 3):
                        groups.append(buf)
                        buf = []
                buf.append(i)
            else:
                if buf:
                    groups.append(buf)
                    buf = []
                groups.append([i])
        if buf:
            groups.append(buf)
        return groups

    @staticmethod
    def _coalesce_printable(tokens: list[str],
                            keystroke: list[bool] | None = None) -> list[str]:
        """Funde sequências de teclas ecoadas 1 a 1 em um input por campo.

        Capturas reais (gateway) emitem um ``deterministic_input`` por tecla
        ecoada — um campo digitado vira N tokens de 1 caractere
        (``'4','0','0',...``). Sem a fusão, cada caractere disputava uma
        posição de campo no mapeamento input→campo. Só tokens de 1 caractere
        originados de ``deterministic_input`` (``keystroke=True``) são
        fundidos: tokens multi-caractere já são blocos completos (colagem/
        paste ou o fluxo ``bytes``/``checkpoint`` — contrato coberto por
        test_capture_parametrizer_screen_inputs e
        test_capture_parametrizer_deterministic_unit). Comandos
        ({KEY:ENTER}, {KEY:TAB}, setas) delimitam campos e nunca são fundidos.
        """
        groups = CaptureParametrizer._coalesce_groups(tokens, keystroke)
        return ["".join(str(tokens[i]) for i in g) for g in groups]

    def analyze_capture_dir(self, capture_dir: str) -> list[CaptureTemplate]:
        """Analisa todos os .jsonl em um diretório de captura."""
        templates: list[CaptureTemplate] = []
        base = Path(capture_dir)
        for jsonl_file in sorted(base.rglob("*.jsonl")):
            tmpl = self.analyze_capture(str(jsonl_file))
            if tmpl.input_templates:
                templates.append(tmpl)
        return templates

    # ------------------------------------------------------------------
    # Geração de sessões parametrizadas
    # ------------------------------------------------------------------

    def generate_sessions(
        self,
        template: CaptureTemplate,
        datasets: dict[str, list[dict[str, Any]]],
        session_count: int = 10,
        seed: int = 0,
    ) -> list[ParametrizedSession]:
        """Gera sessões parametrizadas a partir de template + datasets."""
        import random
        rng = random.Random(seed)
        sessions: list[ParametrizedSession] = []

        # Extrair entidades referenciadas nos templates
        entities = self.template_engine.extract_entities(template.input_templates)

        for sess_idx in range(session_count):
            # Construir dados para esta sessão
            session_data: dict[str, Any] = {}
            for entity in entities:
                if entity in datasets and sess_idx < len(datasets[entity]):
                    session_data[entity] = datasets[entity][sess_idx]

            # Renderizar inputs
            rendered = self.template_engine.render_batch(
                template.input_templates, [session_data]
            )

            sessions.append(ParametrizedSession(
                session_index=sess_idx,
                inputs=rendered[0] if rendered else [],
                data=session_data,
            ))

        return sessions

    # ------------------------------------------------------------------
    # Conversão para script replay
    # ------------------------------------------------------------------

    def to_replay_script(
        self,
        template: CaptureTemplate,
        sessions: list[ParametrizedSession],
    ) -> str:
        """Gera script de replay multi-sessão a partir de template parametrizado."""
        lines: list[str] = []
        lines.append(f"# Replay parametrizado de: {template.capture_source}")
        lines.append(f"# Sessões: {len(sessions)}")
        lines.append(f"# Telas detectadas: {len(template.screen_sequence)}")
        lines.append("")

        for sess in sessions:
            lines.append(f"# ===== SESSÃO {sess.session_index + 1} ===== ")
            for i, inp in enumerate(sess.inputs):
                # Verificar se é placeholder não resolvido
                if inp.startswith("{{") and inp.endswith("}}"):
                    lines.append(f"# {inp}  (placeholder não resolvido)")
                else:
                    lines.append(inp)
            lines.append("")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Comparação: original vs parametrizado
    # ------------------------------------------------------------------

    def diff_sessions(
        self,
        template: CaptureTemplate,
        sessions: list[ParametrizedSession],
    ) -> dict:
        """Compara sessão original com sessões parametrizadas."""
        original_inputs = template.metadata.get("original_inputs", [])

        diffs = []
        for sess in sessions[:3]:  # Amostra das 3 primeiras
            replaced = 0
            unchanged = 0
            for orig, new in zip(original_inputs, sess.inputs):
                if orig != new:
                    replaced += 1
                else:
                    unchanged += 1
            diffs.append({
                "session": sess.session_index,
                "total_inputs": len(sess.inputs),
                "replaced": replaced,
                "unchanged": unchanged,
                "replaced_pct": round(replaced / max(1, len(sess.inputs)) * 100, 1),
            })

        return {
            "capture_source": template.capture_source,
            "original_inputs": len(original_inputs),
            "sessions_generated": len(sessions),
            "diffs": diffs,
        }
