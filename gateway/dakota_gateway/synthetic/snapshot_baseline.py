"""Snapshot baseline: salva e compara telas de referência (golden screens)."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from .journey import JourneyDefinition, JourneyStep


@dataclass
class ScreenSnapshot:
    """Snapshot golden de uma tela."""
    screen_signature: str = ""
    screen_text_hash: str = ""  # SHA256 do texto normalizado
    screen_text: str = ""
    step_order: int = 0
    journey_id: str = ""
    captured_at: str = ""
    tags: list[str] = field(default_factory=list)


@dataclass
class BaselineComparison:
    """Resultado da comparação com baseline."""
    screen_signature: str = ""
    matches: bool = False
    similarity: float = 0.0
    hash_match: bool = False
    expected_hash: str = ""
    observed_hash: str = ""
    diff_summary: str = ""


class SnapshotBaseline:
    """Gerencia baseline de telas golden para comparação de regressão visual."""

    def __init__(self, db_connection: Optional[sqlite3.Connection] = None):
        self.con = db_connection

    # ------------------------------------------------------------------
    # Captura de baseline
    # ------------------------------------------------------------------

    def capture_baseline(
        self,
        journey: JourneyDefinition,
        screens: list[dict],
        baseline_name: str = "default",
        tags: Optional[list[str]] = None,
    ) -> list[ScreenSnapshot]:
        """Captura telas de uma execução como baseline golden."""
        snapshots: list[ScreenSnapshot] = []
        now = datetime.now().isoformat()
        tags = tags or []

        for i, (step, screen) in enumerate(zip(
            sorted(journey.steps, key=lambda s: s.step_order),
            screens,
        )):
            text = screen.get("screen_text", screen.get("norm_text", ""))
            text_hash = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()

            snap = ScreenSnapshot(
                screen_signature=step.screen_signature or step.screen_id,
                screen_text_hash=text_hash,
                screen_text=text[:2000],
                step_order=step.step_order,
                journey_id=journey.journey_id,
                captured_at=now,
                tags=tags,
            )
            snapshots.append(snap)

            # Persistir
            if self.con:
                self._save_snapshot(snap, baseline_name)

        return snapshots

    def get_baseline(
        self,
        journey_id: str,
        baseline_name: str = "default",
    ) -> list[ScreenSnapshot]:
        """Recupera baseline salva."""
        if not self.con:
            return []

        rows = self.con.execute(
            """SELECT * FROM screen_baselines
               WHERE journey_id=? AND baseline_name=?
               ORDER BY step_order""",
            (journey_id, baseline_name),
        ).fetchall()

        return [
            ScreenSnapshot(
                screen_signature=r["screen_signature"],
                screen_text_hash=r["screen_text_hash"],
                screen_text=r["screen_text"] or "",
                step_order=r["step_order"],
                journey_id=r["journey_id"],
                captured_at=r["captured_at"] or "",
                tags=json.loads(r["tags_json"] or "[]"),
            )
            for r in rows
        ]

    def list_baselines(self, journey_id: str = "") -> list[dict]:
        """Lista baselines disponíveis."""
        if not self.con:
            return []

        query = "SELECT DISTINCT baseline_name, journey_id, COUNT(*) as screens, MAX(captured_at) as last_captured FROM screen_baselines"
        params = ()
        if journey_id:
            query += " WHERE journey_id=?"
            params = (journey_id,)
        query += " GROUP BY baseline_name, journey_id ORDER BY last_captured DESC"

        return [dict(r) for r in self.con.execute(query, params).fetchall()]

    # ------------------------------------------------------------------
    # Comparação
    # ------------------------------------------------------------------

    def compare(
        self,
        journey_id: str,
        observed_screens: list[dict],
        baseline_name: str = "default",
    ) -> list[BaselineComparison]:
        """Compara telas observadas contra baseline golden."""
        baseline = self.get_baseline(journey_id, baseline_name)
        comparisons: list[BaselineComparison] = []

        for i, observed in enumerate(observed_screens):
            obs_text = observed.get("screen_text", observed.get("norm_text", ""))
            obs_hash = hashlib.sha256(obs_text.encode("utf-8", errors="replace")).hexdigest()

            baseline_snap = baseline[i] if i < len(baseline) else None

            if baseline_snap:
                hash_match = obs_hash == baseline_snap.screen_text_hash

                # Similaridade textual
                from difflib import SequenceMatcher
                sm = SequenceMatcher(None, baseline_snap.screen_text, obs_text)
                similarity = round(sm.ratio(), 4)

                comparisons.append(BaselineComparison(
                    screen_signature=baseline_snap.screen_signature,
                    matches=hash_match,
                    similarity=similarity,
                    hash_match=hash_match,
                    expected_hash=baseline_snap.screen_text_hash,
                    observed_hash=obs_hash,
                    diff_summary=f"Similaridade: {similarity*100:.1f}%" + (" (MATCH)" if hash_match else " (DIVERGE)"),
                ))
            else:
                comparisons.append(BaselineComparison(
                    screen_signature=f"step_{i}",
                    matches=False,
                    similarity=0.0,
                    hash_match=False,
                    expected_hash="",
                    observed_hash=obs_hash,
                    diff_summary="Nova tela (sem baseline)",
                ))

        return comparisons

    def is_regression(self, comparisons: list[BaselineComparison]) -> bool:
        """True se qualquer tela divergir do baseline."""
        return any(not c.matches for c in comparisons)

    def regression_summary(self, comparisons: list[BaselineComparison]) -> dict:
        """Resumo da regressão."""
        total = len(comparisons)
        matches = sum(1 for c in comparisons if c.matches)
        diverged = total - matches
        return {
            "total_screens": total,
            "matched": matches,
            "diverged": diverged,
            "is_regression": diverged > 0,
            "diverged_screens": [
                {"screen": c.screen_signature, "similarity": c.similarity}
                for c in comparisons if not c.matches
            ],
        }

    # ------------------------------------------------------------------
    # Persistência
    # ------------------------------------------------------------------

    def _save_snapshot(self, snap: ScreenSnapshot, baseline_name: str) -> None:
        if not self.con:
            return
        self.con.execute(
            """INSERT OR REPLACE INTO screen_baselines
               (journey_id, baseline_name, step_order, screen_signature,
                screen_text_hash, screen_text, captured_at, tags_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (snap.journey_id, baseline_name, snap.step_order,
             snap.screen_signature, snap.screen_text_hash, snap.screen_text[:2000],
             snap.captured_at, json.dumps(snap.tags, ensure_ascii=False)),
        )
        self.con.commit()
