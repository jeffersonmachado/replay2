"""Conversão de sessão gravada → JourneyDefinition automática.

Modo "watch and learn": grava uma sessão real → converte em jornada parametrizável.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .journey import JourneyDefinition, JourneyStep
from .template_engine import TemplateEngine


@dataclass
class RecordedSession:
    """Sessão gravada (de .jsonl ou captura ao vivo)."""
    session_id: str = ""
    screen_signatures: list[str] = field(default_factory=list)
    screen_texts: list[str] = field(default_factory=list)
    inputs: list[str] = field(default_factory=list)
    timestamps_ms: list[int] = field(default_factory=list)
    source: str = ""


class SessionRecorder:
    """Grava uma sessão e converte em JourneyDefinition."""

    def __init__(self):
        self.template_engine = TemplateEngine()
        self._recording = False
        self._current_session: Optional[RecordedSession] = None

    # ------------------------------------------------------------------
    # Gravação
    # ------------------------------------------------------------------

    def start_recording(self, session_id: str = "") -> RecordedSession:
        """Inicia gravação de uma nova sessão."""
        import uuid
        self._recording = True
        self._current_session = RecordedSession(
            session_id=session_id or str(uuid.uuid4()),
        )
        return self._current_session

    def record_event(self, screen_text: str = "", screen_sig: str = "",
                     input_text: str = "", ts_ms: int = 0):
        """Registra um evento na sessão (tela + input)."""
        if not self._recording or not self._current_session:
            return
        if screen_text or screen_sig:
            self._current_session.screen_texts.append(screen_text)
            self._current_session.screen_signatures.append(screen_sig)
        if input_text:
            self._current_session.inputs.append(input_text)
        if ts_ms:
            self._current_session.timestamps_ms.append(ts_ms)

    def stop_recording(self) -> Optional[RecordedSession]:
        """Finaliza gravação."""
        self._recording = False
        session = self._current_session
        self._current_session = None
        return session

    # ------------------------------------------------------------------
    # Conversão .jsonl → RecordedSession
    # ------------------------------------------------------------------

    def from_jsonl(self, jsonl_path: str) -> Optional[RecordedSession]:
        """Converte arquivo .jsonl de captura em RecordedSession."""
        path = Path(jsonl_path)
        if not path.exists():
            return None

        session = RecordedSession(source=str(path))
        session_id = ""

        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if not session_id:
                    session_id = event.get("session_id", "")
                    session.session_id = session_id

                event_type = event.get("type", "")

                if event_type == "checkpoint":
                    if event.get("screen_sig"):
                        session.screen_signatures.append(event["screen_sig"])
                    if event.get("screen_sample"):
                        session.screen_texts.append(event["screen_sample"])
                    if event.get("key_text") and event["key_text"] not in ("\r", "\n", ""):
                        session.inputs.append(event["key_text"])
                    session.timestamps_ms.append(event.get("ts_ms", 0))

                elif event_type == "bytes":
                    key = event.get("key_text", "")
                    if key and key not in ("\r", "\n", "\t", ""):
                        session.inputs.append(key)

        return session

    # ------------------------------------------------------------------
    # Conversão RecordedSession → JourneyDefinition
    # ------------------------------------------------------------------

    def to_journey(self, session: RecordedSession,
                   journey_name: str = "",
                   category: str = "recorded") -> JourneyDefinition:
        """Converte sessão gravada em JourneyDefinition."""
        steps: list[JourneyStep] = []
        unique_sigs: list[str] = []
        seen: set[str] = set()

        for sig in session.screen_signatures:
            if sig and sig not in seen:
                unique_sigs.append(sig)
                seen.add(sig)

        # Detectar placeholders nos inputs
        input_templates = self.template_engine.detect_placeholders(session.inputs)

        step_order = 0
        for i, sig in enumerate(unique_sigs):
            # Extrair título da tela (heurística)
            title = self._extract_screen_title(
                session.screen_texts[i] if i < len(session.screen_texts) else ""
            )

            steps.append(JourneyStep(
                step_order=step_order,
                screen_signature=sig,
                screen_title=title or f"Tela {i+1}",
                action="navigate" if i == 0 else "navigate",
                trigger="ENTER" if i == 0 else "",
                description=f"Tela detectada: {title or sig[:30]}",
            ))
            step_order += 1

            # Adicionar input step se houver inputs correspondentes
            if i < len(input_templates):
                steps.append(JourneyStep(
                    step_order=step_order,
                    screen_signature=sig,
                    screen_title=title or f"Tela {i+1}",
                    action="input",
                    input_template=input_templates[i] if i < len(input_templates) else "",
                    description="Input detectado da gravação",
                ))
                step_order += 1

        return JourneyDefinition(
            journey_id=f"recorded_{session.session_id[:8]}",
            name=journey_name or f"Jornada Gravada {session.session_id[:8]}",
            description=f"Jornada convertida de sessão gravada ({len(steps)} passos)",
            category=category,
            entry_screen=unique_sigs[0] if unique_sigs else "",
            steps=steps,
            tags=["recorded", "auto_converted"],
            metadata_json=json.dumps({
                "source": session.source,
                "session_id": session.session_id,
                "total_inputs": len(session.inputs),
                "total_screens": len(session.screen_signatures),
            }, ensure_ascii=False),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_screen_title(screen_text: str) -> str:
        """Extrai título de tela (heurística)."""
        # Procurar por linhas com caracteres de frame
        lines = screen_text.split("\n")
        for line in lines:
            line = line.strip()
            # Ignorar bordas
            if re.match(r"^[+\-|=]+$", line):
                continue
            # Primeira linha com conteúdo significativo
            if len(line) > 3 and not line.startswith("|"):
                return line[:60]
            # Linha entre bordas: | TITULO |
            m = re.match(r"\|?\s*([A-Za-zÀ-ÿ0-9][A-Za-zÀ-ÿ0-9\s\-_]{3,50})\s*\|?", line)
            if m:
                return m.group(1).strip()
        return ""
