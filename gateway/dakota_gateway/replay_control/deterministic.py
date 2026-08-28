"""Comparação determinística do replay_control (decomposição do módulo monolítico)."""
from __future__ import annotations

import selectors

from ..replay import ReplayConfig, ReplayError, SessionReplayState, _TargetSession, _session_config_from_event  # type: ignore
from ..replay_compare import (
    apply_input_echo_fallback,
    apply_synthetic_substitution_fallback,
    apply_volatile_mask_fallback,
    event_requires_comparison,
    expected_snapshot_from_event,
    observed_snapshot_from_session,
    substitution_echo_line_indices,
    wait_for_signature_match,
)
from ..replay_failures import build_failure_record, classify_checkpoint_failure
from dakota_terminal.comparison import compare_signatures, resolve_comparison_mode

from .window import _on_deterministic_mismatch

def _deterministic_failure(
    *,
    sid: str,
    seq_global: int,
    seq_session: int,
    expected_sig: str,
    observed_sig: str,
    params: dict | None,
    checkpoint_timeout_ms: int,
    checkpoint_quiet_ms: int,
    mode_label: str,
    concurrent_mode: bool,
    match: dict | None = None,
    expected_screen: str = "",
    observed_screen: str = "",
    expected_event: dict | None = None,
) -> dict:
    match = match or {
        "comparison_mode_requested": _comparison_mode_from_params(params),
        "comparison_mode_used": "legacy_screen_sig",
        "expected_sig": expected_sig,
        "observed_sig": observed_sig,
        "matched": expected_sig == observed_sig,
        "fallback_reason": "legacy_deterministic_failure_adapter",
    }
    failure_type, severity, reason = classify_checkpoint_failure(
        expected_sig=expected_sig,
        observed_sig=observed_sig,
        params=params,
        timeout_reached=True,
        concurrent_mode=concurrent_mode,
    )
    swap = synthetic_swap_override(
        match, params, expected_screen=expected_screen, observed_screen=observed_screen
    )
    if swap:
        # Divergência explicada pelo de→para: é a troca de dado sintético
        # ecoando na tela, não uma divergência funcional.
        match, failure_type, severity, reason = swap
    else:
        failure_type, severity, reason = stale_reference_override(
            failure_type,
            severity,
            reason,
            expected_event=expected_event,
            expected_screen=expected_screen,
            observed_screen=observed_screen,
        )
    mismatch_mode = _on_deterministic_mismatch(params)
    action = "failed"
    if mismatch_mode == "skip":
        action = "skipped"
    elif mismatch_mode == "send-anyway":
        action = "sent_anyway"
    evidence = {
        "checkpoint_timeout_ms": checkpoint_timeout_ms,
        "checkpoint_quiet_ms": checkpoint_quiet_ms,
        "mode": mode_label,
        "match": match,
        "action": action,
        "mismatch_mode": mismatch_mode,
    }
    # Telas do momento da falha — permitem à UI mostrar O QUE divergiu.
    if expected_screen:
        evidence["expected_screen"] = expected_screen
    if observed_screen:
        evidence["observed_screen"] = observed_screen
    return build_failure_record(
        session_id=sid,
        seq_global=seq_global,
        seq_session=seq_session,
        event_type="deterministic_input",
        failure_type=failure_type,
        severity=severity,
        expected_value=expected_sig,
        observed_value=observed_sig,
        message=f"{reason} session={sid}: expected={expected_sig!r} got={observed_sig!r} action={action}",
        evidence=evidence,
    )


def _comparison_mode_from_params(params: dict | None, default: str = "visual") -> str:
    return resolve_comparison_mode(replay=params, default=default)["comparison_mode"]


def _substitution_pairs_from_params(params: dict | None) -> list:
    """Pares (original → sintético) embutidos nos params da run sintética."""
    raw = params if isinstance(params, dict) else {}
    subs = raw.get("synthetic_substitutions")
    return list(subs) if isinstance(subs, (list, tuple)) else []


def synthetic_swap_override(
    match: dict | None,
    params: dict | None,
    *,
    expected_screen: str = "",
    observed_screen: str = "",
) -> tuple[dict, str, str, str] | None:
    """Reclassificação de troca sintética compartilhada pelos caminhos de falha.

    Retorna ``(match, "synthetic_data_swap", "low", motivo)`` quando o match
    já veio flagado da comparação ou quando as telas da evidência (as mesmas
    exibidas na UI) contêm eco do de→para — o snapshot da comparação é do
    instante do timeout e pode não conter o eco que aparece na tela gravada.
    Sem eco, retorna ``None`` e o chamador mantém a classificação original.
    """
    match = dict(match or {})
    if not match.get("synthetic_substitution"):
        pairs = _substitution_pairs_from_params(params)
        if pairs and expected_screen and observed_screen:
            echo_lines = substitution_echo_line_indices(expected_screen, observed_screen, pairs)
            if echo_lines:
                match["synthetic_substitution"] = True
                match["synthetic_echo_lines"] = echo_lines
    if not match.get("synthetic_substitution"):
        return None
    return (
        match,
        "synthetic_data_swap",
        "low",
        "divergência explicada pela troca de dados sintéticos (de→para aplicado no replay)",
    )


# Idade mínima do snapshot de referência para suspeitar de contexto envelhecido.
STALE_REFERENCE_MIN_AGE_MS = 10000


def stale_reference_override(
    failure_type: str,
    severity: str,
    reason: str,
    *,
    expected_event: dict | None,
    expected_screen: str,
    observed_screen: str,
) -> tuple[str, str, str]:
    """Rebaixa divergência cuja tela de referência da captura está envelhecida.

    Quando o snapshot esperado foi gravado muito antes do input (o usuário leu
    a tela por um longo tempo, ou a captura mudou de contexto — ex.: trecho da
    trilha já no shell do servidor), a comparação não mede comportamento
    funcional. Critério combinado (medido na run 32 da captura 13: idade alta
    sozinha é normal em captura humana): idade >= STALE_REFERENCE_MIN_AGE_MS
    E as telas esperada/observada não compartilham nenhuma linha não-vazia
    (contexto totalmente diferente). O tipo da falha é mantido e a severidade
    é rebaixada para low com motivo explicativo.
    """
    try:
        age_ms = int((expected_event or {}).get("screen_snapshot_age_ms") or 0)
    except (TypeError, ValueError):
        age_ms = 0
    if age_ms < STALE_REFERENCE_MIN_AGE_MS:
        return failure_type, severity, reason
    exp_lines = {ln.strip() for ln in str(expected_screen or "").splitlines() if ln.strip()}
    obs_lines = {ln.strip() for ln in str(observed_screen or "").splitlines() if ln.strip()}
    if not exp_lines or not obs_lines or (exp_lines & obs_lines):
        return failure_type, severity, reason
    return (
        failure_type,
        "low",
        "tela de referência da captura desatualizada "
        f"(snapshot de {age_ms / 1000:.0f}s antes do input) — divergência de contexto, não funcional",
    )


def _legacy_checkpoint_expected(sig: str) -> dict:
    return {"screen_sig": str(sig or "")}


# Helpers compartilhados com replay (dakota_gateway.replay_compare).
_expected_snapshot_from_event = expected_snapshot_from_event
_observed_snapshot_from_session = observed_snapshot_from_session


def _event_requires_deterministic_comparison(
    ev: dict,
    params: dict | None,
    *,
    session_config: ReplayConfig | SessionReplayState | dict | None = None,
    replay_config: ReplayConfig | dict | None = None,
) -> bool:
    mode = resolve_comparison_mode(event=ev, session=session_config, replay=replay_config or params)["comparison_mode"]
    return event_requires_comparison(ev, mode=mode)


def _match_failure_values(match: dict, expected_snapshot: dict, observed_snapshot: dict) -> tuple[str, str]:
    expected_sig = str(match.get("expected_sig") or expected_snapshot.get("screen_sig") or "")
    observed_sig = str(match.get("observed_sig") or observed_snapshot.get("screen_sig") or "")
    return expected_sig, observed_sig


def _session_start_by_id(events: list[dict], sid: str) -> dict:
    for ev in events:
        if ev.get("type") == "session_start" and str(ev.get("session_id") or "") == sid:
            return ev
    return {"session_id": sid}


def _state_for_session(cfg: ReplayConfig, sid: str, ev: dict | None = None) -> SessionReplayState:
    session_cfg = _session_config_from_event(cfg, ev or {"session_id": sid})
    return SessionReplayState(
        session_id=sid,
        config=session_cfg,
        rows=session_cfg.rows,
        cols=session_cfg.cols,
        term=session_cfg.term,
        encoding=session_cfg.encoding,
        comparison_mode=session_cfg.comparison_mode,
    )


def compare_expected_observed(
    expected_snapshot: dict,
    observed_snapshot: dict,
    params: dict | None,
    *,
    event: dict | None = None,
    session_config: ReplayConfig | SessionReplayState | dict | None = None,
    replay_config: ReplayConfig | dict | None = None,
    recent_keys: list | tuple | None = None,
) -> dict:
    match = compare_signatures(
        expected_snapshot,
        observed_snapshot,
        mode=resolve_comparison_mode(event=event, session=session_config, replay=replay_config or params)["comparison_mode"],
        legacy_expected_screen_sig=str(expected_snapshot.get("screen_sig") or ""),
        legacy_observed_screen_sig=str(observed_snapshot.get("screen_sig") or ""),
    )
    match = apply_volatile_mask_fallback(
        match,
        expected_event=event,
        observed_snapshot=observed_snapshot,
        session_config=session_config,
    )
    match = apply_input_echo_fallback(
        match,
        expected_event=event,
        observed_snapshot=observed_snapshot,
        session_config=session_config,
        recent_keys=recent_keys,
    )
    return apply_synthetic_substitution_fallback(
        match,
        expected_event=event,
        observed_snapshot=observed_snapshot,
        session_config=session_config,
        substitutions=_substitution_pairs_from_params(params),
    )


def _wait_for_expected_observed(
    *,
    session: _TargetSession,
    selector: selectors.BaseSelector,
    expected_event: dict,
    params: dict | None,
    should_pause_or_cancel,
    checkpoint_quiet_ms: int,
    checkpoint_timeout_ms: int,
    session_config: ReplayConfig | SessionReplayState | dict | None = None,
    replay_config: ReplayConfig | dict | None = None,
    recent_keys: list | tuple | None = None,
) -> tuple[bool, dict, dict]:
    expected_snapshot = _expected_snapshot_from_event(expected_event)

    def compare(observed: dict) -> dict:
        return compare_expected_observed(
            expected_snapshot,
            observed,
            params,
            event=expected_event,
            session_config=session_config,
            replay_config=replay_config,
            recent_keys=recent_keys,
        )

    return wait_for_signature_match(
        session,
        selector,
        compare=compare,
        checkpoint_quiet_ms=checkpoint_quiet_ms,
        checkpoint_timeout_ms=checkpoint_timeout_ms,
        should_pause_or_cancel=should_pause_or_cancel,
    )


def _should_apply_deterministic_input(on_failure, failure: dict, *, params: dict | None) -> bool:
    on_failure(failure)
    mode = _on_deterministic_mismatch(params)
    if mode == "skip":
        return False
    if mode == "send-anyway":
        return True
    raise ReplayError(str(failure.get("message") or "deterministic replay mismatch"))
