from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from .error_detector import ErrorDetector, DetectedError
from .journey import JourneyDefinition, JourneyStep, JourneyDataset


# Compatível com os tipos de falha do replay_control
ALLOWED_FAILURE_TYPES = {
    "functional",
    "timeout",
    "screen_divergence",
    "technical_error",
    "navigation_error",
    "concurrency_error",
    "checkpoint_mismatch",
    "integrity_error",
    "cancelled",
    "validation_error",
    "data_error",
    "permission_error",
}

ALLOWED_SEVERITIES = {"low", "medium", "high", "critical"}


@dataclass
class JourneyVerificationResult:
    """Resultado da verificação de uma execução de jornada."""
    journey_id: str = ""
    session_index: int = 0
    total_steps: int = 0
    steps_passed: int = 0
    steps_failed: int = 0
    steps_warning: int = 0
    errors: list[DetectedError] = field(default_factory=list)
    step_results: list[dict] = field(default_factory=list)
    duration_ms: int = 0
    summary: str = ""

    @property
    def passed(self) -> bool:
        return self.steps_failed == 0

    @property
    def failure_rate_pct(self) -> float:
        if self.total_steps == 0:
            return 0.0
        return round(self.steps_failed / self.total_steps * 100, 2)


class JourneyVerifier:
    """Verifica execução de jornada contra telas capturadas e detecta erros."""

    def __init__(
        self,
        db_connection: Optional[sqlite3.Connection] = None,
        run_id: int = 0,
    ):
        self.detector = ErrorDetector()
        self.con = db_connection
        self.run_id = run_id

    # ------------------------------------------------------------------
    # Verificação de sessão
    # ------------------------------------------------------------------

    def verify_session(
        self,
        journey: JourneyDefinition,
        session_index: int,
        captured_screens: list[dict],
    ) -> JourneyVerificationResult:
        """Verifica uma sessão de jornada contra telas capturadas.

        Args:
            journey: Definição da jornada
            session_index: Índice da sessão
            captured_screens: Lista de telas capturadas, cada uma com:
                - screen_text (ou norm_text): texto da tela
                - screen_sig: assinatura da tela
                - seq_global: número de sequência global
        """
        result = JourneyVerificationResult(
            journey_id=journey.journey_id,
            session_index=session_index,
            total_steps=len(journey.steps),
        )

        steps_sorted = sorted(journey.steps, key=lambda s: s.step_order)
        start_ms = int(time.time() * 1000)

        # Mapear steps para telas capturadas (melhor esforço)
        screen_idx = 0
        for step in steps_sorted:
            step_result = {
                "step_order": step.step_order,
                "screen_id": step.screen_id,
                "screen_title": step.screen_title,
                "action": step.action,
                "status": "ok",
                "errors": [],
            }

            # Obter tela correspondente
            screen_text = ""
            screen_sig = ""
            if screen_idx < len(captured_screens):
                screen = captured_screens[screen_idx]
                screen_text = screen.get("screen_text", screen.get("norm_text", ""))
                screen_sig = screen.get("screen_sig", screen.get("screen_signature", ""))
                screen_idx += 1

            # Classificar tela
            classification = self.detector.classify_screen(
                screen_text=screen_text,
                expected_signature=step.expected_signature or step.screen_signature,
                journey_step=step.step_order,
            )

            step_result["classification"] = classification

            if classification["status"] == "error":
                step_result["status"] = "failed"
                result.steps_failed += 1
                result.errors.extend(
                    self.detector.detect(screen_text, {
                        "screen_signature": screen_sig,
                        "step_order": step.step_order,
                        "journey_id": journey.journey_id,
                        "session_index": session_index,
                    })
                )

                # Registrar no banco se disponível
                if self.con and self.run_id:
                    self._record_failure(
                        session_index=session_index,
                        step=step,
                        screen_text=screen_text,
                        classification=classification,
                    )

            elif classification["status"] == "warning":
                step_result["status"] = "warning"
                result.steps_warning += 1
            elif classification["status"] == "navigation_divergence":
                step_result["status"] = "failed"
                result.steps_failed += 1
            else:
                result.steps_passed += 1

            result.step_results.append(step_result)

        result.duration_ms = int(time.time() * 1000) - start_ms
        result.summary = self._build_summary(result)

        return result

    def verify_session_screens(
        self,
        journey: JourneyDefinition,
        session_index: int,
        screen_texts: list[str],
    ) -> JourneyVerificationResult:
        """Versão simplificada: recebe apenas textos de tela."""
        screens = [{"screen_text": t, "screen_sig": ""} for t in screen_texts]
        return self.verify_session(journey, session_index, screens)

    # ------------------------------------------------------------------
    # Análise de erros agregada
    # ------------------------------------------------------------------

    def analyze_errors(
        self,
        all_results: list[JourneyVerificationResult],
    ) -> dict:
        """Analisa erros agregados de múltiplas sessões."""
        total_errors = 0
        by_type: dict[str, int] = {}
        by_severity: dict[str, int] = {}
        by_step: dict[int, int] = {}

        for r in all_results:
            total_errors += r.steps_failed
            for err in r.errors:
                by_type[err.error_type] = by_type.get(err.error_type, 0) + 1
                by_severity[err.severity] = by_severity.get(err.severity, 0) + 1
            for sr in r.step_results:
                if sr["status"] == "failed":
                    step = sr["step_order"]
                    by_step[step] = by_step.get(step, 0) + 1

        return {
            "total_sessions": len(all_results),
            "total_sessions_with_errors": sum(1 for r in all_results if r.steps_failed > 0),
            "total_errors": total_errors,
            "errors_by_type": by_type,
            "errors_by_severity": by_severity,
            "most_failing_steps": sorted(by_step.items(), key=lambda x: -x[1])[:10],
            "overall_pass_rate_pct": round(
                sum(r.steps_passed for r in all_results)
                / max(1, sum(r.total_steps for r in all_results))
                * 100,
                2,
            ),
        }

    # ------------------------------------------------------------------
    # Persistência de falhas
    # ------------------------------------------------------------------

    def _record_failure(
        self,
        session_index: int,
        step: JourneyStep,
        screen_text: str,
        classification: dict,
    ) -> None:
        """Registra falha no banco replay_failures."""
        if not self.con:
            return

        now_ms = int(time.time() * 1000)
        session_id = f"journey-{step.screen_id}-sess{session_index}"

        try:
            self.con.execute(
                """INSERT INTO replay_failures
                   (run_id, ts_ms, session_id, seq_global, seq_session,
                    flow_name, event_type, failure_type, severity,
                    expected_value, observed_value, message, evidence_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    self.run_id,
                    now_ms,
                    session_id,
                    step.step_order,
                    session_index,
                    f"{step.screen_title or step.screen_id}",
                    "journey_step",
                    classification.get("error_type", "functional"),
                    classification.get("severity", "medium"),
                    step.expected_signature or step.screen_signature or "",
                    screen_text[:500] if screen_text else "",
                    classification.get("message", "Erro detectado na jornada"),
                    json.dumps({
                        "journey_id": step.screen_id,
                        "step_order": step.step_order,
                        "action": step.action,
                        "session_index": session_index,
                        "all_errors": classification.get("all_errors", []),
                    }, ensure_ascii=False),
                ),
            )
            self.con.commit()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_summary(result: JourneyVerificationResult) -> str:
        parts = [
            f"Jornada: {result.journey_id}",
            f"Sessão: {result.session_index}",
            f"Passos: {result.steps_passed} ok, {result.steps_warning} warning, {result.steps_failed} falhas",
        ]
        if result.errors:
            error_types = set(e.error_type for e in result.errors)
            parts.append(f"Tipos de erro: {', '.join(sorted(error_types))}")
        parts.append(f"Duração: {result.duration_ms}ms")
        return " | ".join(parts)
