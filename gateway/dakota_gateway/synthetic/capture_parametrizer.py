#!/usr/bin/env python3
"""Parametriza capturas .jsonl existentes para replay com dados sintéticos."""
from __future__ import annotations

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
        session_id = ""
        current_input_start: int = 0

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

                # Coletar screen signatures
                if event_type == "checkpoint" and event.get("screen_sig"):
                    screens.append({
                        "screen_sig": event.get("screen_sig", ""),
                        "screen_sample": event.get("screen_sample", ""),
                        "norm_len": event.get("norm_len", 0),
                        "seq_global": event.get("seq_global", 0),
                        "input_start": len(inputs),
                    })

                # Coletar inputs (key_text)
                if event_type in ("bytes", "checkpoint") and event.get("key_text"):
                    key_text = str(event.get("key_text", ""))
                    if key_text in ("\r", "\n"):
                        inputs.append("{KEY:ENTER}")
                    elif key_text == "\t":
                        inputs.append("{KEY:TAB}")
                    elif key_text == "\x1b":
                        inputs.append("{KEY:ESC}")
                    elif re.match(r"^\x1b\[", key_text):
                        inputs.append("{KEY:" + key_text[2:] + "}")
                    elif key_text == "":
                        continue
                    else:
                        inputs.append(key_text)

        # Fecha input_end de cada tela
        for i, screen in enumerate(screens):
            if i + 1 < len(screens):
                screen["input_end"] = screens[i + 1]["input_start"]
            else:
                screen["input_end"] = len(inputs)
            # Adiciona os inputs daquela tela
            start = screen.get("input_start", 0)
            end = screen.get("input_end", len(inputs))
            screen["inputs"] = inputs[start:end]

        template.session_id = session_id
        template.screen_sequence = [s["screen_sig"] for s in screens]
        template.screen_contexts = screens
        template.input_templates = self.template_engine.detect_placeholders(inputs)
        template.metadata = {
            "total_screens": len(screens),
            "total_inputs": len(inputs),
            "original_inputs": inputs,
        }

        return template

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
