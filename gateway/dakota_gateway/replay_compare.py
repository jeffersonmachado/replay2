from __future__ import annotations

import time


def expected_snapshot_from_event(ev: dict, *, legacy_sig: str = "") -> dict:
    """Extrai as assinaturas esperadas (canônicas + legada) de um evento de auditoria."""
    return {
        "text_sig": str(ev.get("expected_text_sig") or ev.get("text_sig") or ""),
        "visual_sig": str(ev.get("expected_visual_sig") or ev.get("visual_sig") or ""),
        "semantic_sig": str(ev.get("expected_semantic_sig") or ev.get("semantic_sig") or ""),
        "screen_sig": str(legacy_sig or ev.get("screen_sig") or ev.get("sig") or ""),
    }


def event_requires_comparison(ev: dict, *, mode: str) -> bool:
    """Indica se o evento possui assinatura esperada para o modo de comparação."""
    expected = expected_snapshot_from_event(ev)
    # Check mode-specific signature first
    if mode == "visual":
        has_canonical = bool(expected.get("visual_sig"))
    elif mode == "text":
        has_canonical = bool(expected.get("text_sig"))
    elif mode == "semantic":
        has_canonical = bool(expected.get("semantic_sig"))
    else:
        has_canonical = any(bool(expected.get(key)) for key in ("visual_sig", "text_sig", "semantic_sig"))
    # Legacy screen_sig also triggers comparison (backward compat)
    return has_canonical or bool(expected.get("screen_sig"))


def normalize_deterministic_mismatch_mode(value: str) -> str:
    """Normaliza o modo de tratamento de divergência determinística."""
    mode = str(value or "fail-fast").strip().lower()
    return mode if mode in {"fail-fast", "skip", "send-anyway"} else "fail-fast"


def observed_snapshot_from_session(session) -> dict:
    """Retorna as assinaturas canônicas do estado atual da sessão (ou vazio)."""
    if hasattr(session, "canonical_snapshot_now"):
        return session.canonical_snapshot_now()
    return {"text_sig": "", "visual_sig": "", "semantic_sig": "", "screen_sig": ""}


def wait_for_signature_match(
    session,
    selector,
    *,
    compare,
    checkpoint_quiet_ms: int,
    checkpoint_timeout_ms: int,
    should_pause_or_cancel=None,
    drain_event=None,
    return_first_result: bool = False,
) -> tuple[bool, dict, dict]:
    """Máquina de espera de checkpoint compartilhada.

    Aguarda a tela estabilizar (quiet >= checkpoint_quiet_ms) e avalia
    compare(observed) -> dict com chave "matched". Por padrão repete até o
    timeout; com return_first_result=True retorna na primeira estabilização
    (semântica do replay não-controlado). should_pause_or_cancel (opcional) é
    chamado a cada iteração. drain_event(key) (opcional) consome eventos
    legíveis do seletor; o default é session.read_out().
    Retorna (matched, match, observed).
    """
    deadline = int(time.time() * 1000) + checkpoint_timeout_ms
    last_observed: dict = {}
    last_match = compare({})
    while int(time.time() * 1000) < deadline:
        if should_pause_or_cancel is not None:
            should_pause_or_cancel()
        for key, _ in selector.select(timeout=0.05):
            try:
                if drain_event is not None:
                    drain_event(key)
                else:
                    session.read_out()
            except Exception:
                pass
        quiet = int(time.time() * 1000) - session.last_out_ms
        if quiet >= checkpoint_quiet_ms:
            observed = observed_snapshot_from_session(session)
            last_observed = observed
            last_match = compare(observed)
            if last_match.get("matched") or return_first_result:
                return bool(last_match.get("matched")), last_match, observed
        time.sleep(0.02)
    observed = last_observed or observed_snapshot_from_session(session)
    return False, compare(observed), observed
