"""Diff visual de telas para o dashboard: compara tela esperada vs real."""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ScreenDiffLine:
    """Uma linha do diff de tela."""
    line_number: int = 0
    expected: str = ""
    observed: str = ""
    status: str = "equal"  # equal, added, removed, changed
    diff_parts: list[dict] = field(default_factory=list)  # para highlight inline


@dataclass
class ScreenDiff:
    """Resultado do diff entre duas telas."""
    expected_sig: str = ""
    observed_sig: str = ""
    similarity: float = 0.0
    lines: list[ScreenDiffLine] = field(default_factory=list)
    added_lines: int = 0
    removed_lines: int = 0
    changed_lines: int = 0
    summary: str = ""


class ScreenDiffer:
    """Compara telas esperadas vs observadas e gera diff visual."""

    @staticmethod
    def diff(
        expected: str,
        observed: str,
        expected_sig: str = "",
        observed_sig: str = "",
    ) -> ScreenDiff:
        """Compara duas telas e retorna diff estruturado."""
        # Normalizar
        expected_clean = ScreenDiffer._clean(expected)
        observed_clean = ScreenDiffer._clean(observed)

        expected_lines = expected_clean.split("\n")
        observed_lines = observed_clean.split("\n")

        # Calcular similaridade
        sm = difflib.SequenceMatcher(None, expected_clean, observed_clean)

        # Gerar diff unificado
        differ = difflib.unified_diff(
            expected_lines,
            observed_lines,
            fromfile="esperado",
            tofile="observado",
            lineterm="",
        )

        diff_lines: list[ScreenDiffLine] = []
        added = 0
        removed = 0
        changed = 0
        line_no = 0

        for dline in differ:
            line_no += 1
            if dline.startswith("---") or dline.startswith("+++") or dline.startswith("@@"):
                continue

            if dline.startswith("+"):
                diff_lines.append(ScreenDiffLine(
                    line_number=line_no,
                    expected="",
                    observed=dline[1:],
                    status="added",
                ))
                added += 1
            elif dline.startswith("-"):
                diff_lines.append(ScreenDiffLine(
                    line_number=line_no,
                    expected=dline[1:],
                    observed="",
                    status="removed",
                ))
                removed += 1
            elif dline.startswith(" "):
                diff_lines.append(ScreenDiffLine(
                    line_number=line_no,
                    expected=dline[1:],
                    observed=dline[1:],
                    status="equal",
                ))
            else:
                # Linha modificada
                diff_lines.append(ScreenDiffLine(
                    line_number=line_no,
                    expected="",
                    observed=dline,
                    status="changed",
                ))
                changed += 1

        return ScreenDiff(
            expected_sig=expected_sig,
            observed_sig=observed_sig,
            similarity=round(sm.ratio(), 4),
            lines=diff_lines,
            added_lines=added,
            removed_lines=removed,
            changed_lines=changed,
            summary=ScreenDiffer._build_summary(added, removed, changed),
        )

    @staticmethod
    def diff_sessions(
        session_screens: list[dict],
        expected_screens: list[dict],
    ) -> list[ScreenDiff]:
        """Compara telas de múltiplas sessões."""
        diffs: list[ScreenDiff] = []
        for i, (sess, exp) in enumerate(zip(session_screens, expected_screens)):
            diff = ScreenDiffer.diff(
                expected=exp.get("text", exp.get("screen_text", "")),
                observed=sess.get("text", sess.get("screen_text", "")),
                expected_sig=exp.get("sig", exp.get("screen_sig", "")),
                observed_sig=sess.get("sig", sess.get("screen_sig", "")),
            )
            diffs.append(diff)
        return diffs

    @staticmethod
    def to_html(diff: ScreenDiff) -> str:
        """Converte diff para HTML com highlight inline."""
        parts: list[str] = []
        parts.append(f"""<div style="font-family:monospace;font-size:13px;margin-bottom:16px;">
<div style="margin-bottom:8px;color:#666;">
  Similaridade: <strong>{diff.similarity * 100:.1f}%</strong>
  | +{diff.added_lines} adicionadas
  | -{diff.removed_lines} removidas
  | ~{diff.changed_lines} alteradas
</div>""")

        for line in diff.lines:
            bg = {
                "equal": "transparent",
                "added": "#e8f5e9",
                "removed": "#ffebee",
                "changed": "#fff3e0",
            }.get(line.status, "transparent")

            prefix = {
                "equal": "&nbsp;",
                "added": "+",
                "removed": "-",
                "changed": "~",
            }.get(line.status, " ")

            color = {
                "equal": "#333",
                "added": "#2e7d32",
                "removed": "#c62828",
                "changed": "#e65100",
            }.get(line.status, "#333")

            text = line.observed or line.expected
            # Escape HTML
            text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            text = text.replace(" ", "&nbsp;")

            parts.append(
                f'<div style="background:{bg};color:{color};padding:1px 8px;">'
                f'<span style="width:20px;display:inline-block;">{prefix}</span>{text}</div>'
            )

        parts.append("</div>")
        return "\n".join(parts)

    @staticmethod
    def to_json(diff: ScreenDiff) -> dict:
        """Converte diff para JSON (para API)."""
        return {
            "expected_sig": diff.expected_sig,
            "observed_sig": diff.observed_sig,
            "similarity": diff.similarity,
            "added_lines": diff.added_lines,
            "removed_lines": diff.removed_lines,
            "changed_lines": diff.changed_lines,
            "summary": diff.summary,
            "lines": [
                {
                    "line": l.line_number,
                    "expected": l.expected,
                    "observed": l.observed,
                    "status": l.status,
                }
                for l in diff.lines
            ],
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _clean(text: str) -> str:
        """Remove ANSI e normaliza whitespace."""
        cleaned = re.sub(r"\x1B\[[0-9;?]*[A-Za-z]", "", text)
        cleaned = re.sub(r"\x1B.", "", cleaned)
        cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
        return cleaned

    @staticmethod
    def _build_summary(added: int, removed: int, changed: int) -> str:
        parts = []
        if added:
            parts.append(f"+{added} linhas adicionadas")
        if removed:
            parts.append(f"-{removed} linhas removidas")
        if changed:
            parts.append(f"~{changed} linhas alteradas")
        if not parts:
            return "Telas idênticas"
        return ", ".join(parts)
