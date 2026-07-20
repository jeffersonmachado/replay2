"""Modos de comparacao para replay deterministico.

Implementa: visual, text, semantic, hybrid.

Cada modo seleciona a assinatura apropriada e retorna resultado
estruturado com o fallback utilizado.
"""
from __future__ import annotations

from .signatures import text_sig, visual_sig, semantic_sig

VALID_COMPARISON_MODES = {"visual", "text", "semantic", "hybrid"}


def normalize_comparison_mode(value: object, default: str = "visual") -> str:
    mode = str(value or "").strip().lower()
    fallback = str(default or "visual").strip().lower()
    if default == "":
        fallback = ""
    if fallback not in VALID_COMPARISON_MODES:
        fallback = "" if default == "" else "visual"
    return mode if mode in VALID_COMPARISON_MODES else fallback


def _mode_from_source(source: object) -> str:
    if source is None:
        return ""
    if isinstance(source, str):
        return source
    if isinstance(source, dict):
        return str(source.get("comparison_mode") or source.get("match_mode") or "")
    return str(getattr(source, "comparison_mode", "") or "")


def resolve_comparison_mode(
    *,
    event: object | None = None,
    session: object | None = None,
    replay: object | None = None,
    default: str = "visual",
) -> dict:
    """Resolve o modo efetivo com precedencia unica.

    Ordem: evento > sessao > replay global > padrao seguro.
    """
    for source_name, source in (("event", event), ("session", session), ("replay", replay)):
        raw = _mode_from_source(source)
        mode = normalize_comparison_mode(raw, default="")
        if mode:
            return {"comparison_mode": mode, "source": source_name}
    return {"comparison_mode": normalize_comparison_mode(default), "source": "default"}


def select_signature(
    snapshot: dict,
    mode: str,
    *,
    legacy_screen_sig: str = "",
) -> dict:
    """Seleciona assinatura conforme modo de comparacao.

    Regras:
    - visual/text/semantic: SEM fallback. Falha se assinatura ausente.
    - hybrid: tenta niveis em ordem, ambos os lados devem ter o mesmo nivel.
    - legacy_screen_sig: usado APENAS como ultimo nivel do hybrid,
      explicitamente marcado como 'legacy_screen_sig'.

    Returns:
        {
            "comparison_mode_requested": str,
            "comparison_mode_used": str | None,
            "signature": str | None,
            "fallback_reason": str | None,
        }
    """
    mode = str(mode or "visual").strip().lower()

    if mode == "visual":
        sig = snapshot.get("visual_sig", "")
        if sig:
            return {
                "comparison_mode_requested": "visual",
                "comparison_mode_used": "visual",
                "signature": sig,
                "fallback_reason": None,
            }
        return {
            "comparison_mode_requested": "visual",
            "comparison_mode_used": None,
            "signature": None,
            "fallback_reason": "visual_sig_not_available",
        }

    if mode == "text":
        sig = snapshot.get("text_sig", "")
        if sig:
            return {
                "comparison_mode_requested": "text",
                "comparison_mode_used": "text",
                "signature": sig,
                "fallback_reason": None,
            }
        return {
            "comparison_mode_requested": "text",
            "comparison_mode_used": None,
            "signature": None,
            "fallback_reason": "text_sig_not_available",
        }

    if mode == "semantic":
        sig = snapshot.get("semantic_sig", "")
        if sig:
            return {
                "comparison_mode_requested": "semantic",
                "comparison_mode_used": "semantic",
                "signature": sig,
                "fallback_reason": None,
            }
        # SEM fallback para legacy no modo semantic puro
        return {
            "comparison_mode_requested": "semantic",
            "comparison_mode_used": None,
            "signature": None,
            "fallback_reason": "semantic_sig_not_available",
        }

    if mode == "hybrid":
        # Tenta visual primeiro (ambos os lados precisam ter)
        vis = snapshot.get("visual_sig", "")
        if vis:
            return {
                "comparison_mode_requested": "hybrid",
                "comparison_mode_used": "visual",
                "signature": vis,
                "fallback_reason": None,
            }
        # Depois text
        txt = snapshot.get("text_sig", "")
        if txt:
            return {
                "comparison_mode_requested": "hybrid",
                "comparison_mode_used": "text",
                "signature": txt,
                "fallback_reason": "visual_sig_not_available",
            }
        # Depois semantic
        sem = snapshot.get("semantic_sig", "")
        if sem:
            return {
                "comparison_mode_requested": "hybrid",
                "comparison_mode_used": "semantic",
                "signature": sem,
                "fallback_reason": "visual_sig_and_text_sig_not_available",
            }
        # Ultimo: legado (explicitamente marcado)
        if legacy_screen_sig:
            return {
                "comparison_mode_requested": "hybrid",
                "comparison_mode_used": "legacy_screen_sig",
                "signature": legacy_screen_sig,
                "fallback_reason": "using_legacy_screen_sig",
            }
        return {
            "comparison_mode_requested": "hybrid",
            "comparison_mode_used": None,
            "signature": None,
            "fallback_reason": "no_signature_available",
        }

    return {
        "comparison_mode_requested": mode,
        "comparison_mode_used": None,
        "signature": None,
        "fallback_reason": f"unknown_mode_{mode}",
    }


def compare_signatures(
    expected_snapshot: dict,
    observed_snapshot: dict,
    mode: str = "visual",
    *,
    legacy_expected_screen_sig: str = "",
    legacy_observed_screen_sig: str = "",
) -> dict:
    """Compara dois snapshots conforme modo de comparacao.

    Regras:
    - visual/text/semantic: compara apenas a assinatura do nivel solicitado.
    - hybrid: encontra o maior nivel comum aos dois lados.
      Ambos os lados DEVEM concordar no nivel. Nao compara niveis diferentes.
    - legacy_screen_sig: usado apenas como ultimo recurso no hybrid.

    Returns:
        {
            "comparison_mode_requested": str,
            "comparison_mode_used": str | None,
            "expected_sig": str | None,
            "observed_sig": str | None,
            "matched": bool,
            "fallback_reason": str | None,
        }
    """
    mode = str(mode or "visual").strip().lower()

    if mode in ("visual", "text", "semantic"):
        sig_key = {"visual": "visual_sig", "text": "text_sig", "semantic": "semantic_sig"}[mode]
        expected_sig = expected_snapshot.get(sig_key, "")
        observed_sig = observed_snapshot.get(sig_key, "")

        if not expected_sig or not observed_sig:
            return {
                "comparison_mode_requested": mode,
                "comparison_mode_used": None,
                "expected_sig": expected_sig or None,
                "observed_sig": observed_sig or None,
                "matched": False,
                "fallback_reason": f"{mode}_signature_missing",
            }

        return {
            "comparison_mode_requested": mode,
            "comparison_mode_used": mode,
            "expected_sig": expected_sig,
            "observed_sig": observed_sig,
            "matched": expected_sig == observed_sig,
            "fallback_reason": None,
        }

    # Hybrid: encontra nivel comum mais alto
    levels = [
        ("visual", "visual_sig"),
        ("text", "text_sig"),
        ("semantic", "semantic_sig"),
    ]

    for level_name, level_key in levels:
        exp = expected_snapshot.get(level_key, "")
        obs = observed_snapshot.get(level_key, "")
        if exp and obs:
            return {
                "comparison_mode_requested": "hybrid",
                "comparison_mode_used": level_name,
                "expected_sig": exp,
                "observed_sig": obs,
                "matched": exp == obs,
                "fallback_reason": None,
            }

    # Ultimo recurso: legado (ambos devem ter)
    legacy_exp = legacy_expected_screen_sig or expected_snapshot.get("screen_sig", "")
    legacy_obs = legacy_observed_screen_sig or observed_snapshot.get("screen_sig", "")
    if legacy_exp and legacy_obs:
        return {
            "comparison_mode_requested": "hybrid",
            "comparison_mode_used": "legacy_screen_sig",
            "expected_sig": legacy_exp,
            "observed_sig": legacy_obs,
            "matched": legacy_exp == legacy_obs,
            "fallback_reason": None,
        }

    return {
        "comparison_mode_requested": "hybrid",
        "comparison_mode_used": None,
        "expected_sig": None,
        "observed_sig": None,
        "matched": False,
        "fallback_reason": "no_common_signature_level",
    }
