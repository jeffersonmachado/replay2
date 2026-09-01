"""Executores de replay do replay_control (decomposição do módulo monolítico)."""
from __future__ import annotations

import hashlib
import random
import re
import selectors
import time
from dataclasses import dataclass
from threading import Lock, Semaphore, Thread

from ..replay import ReplayConfig, ReplayError, SessionReplayState, _TargetSession, _decode_replay_input  # type: ignore
from ..replay_compare import (
    expected_screen_text_from_event,
    observed_screen_text_from_session,
    wait_for_signature_match,
)
from ..replay_failures import (
    build_failure_record,
    classify_checkpoint_failure,
    evaluate_checkpoint_match,
)

from .deterministic import (
    _deterministic_failure,
    _event_requires_deterministic_comparison,
    _expected_snapshot_from_event,
    _match_failure_values,
    _observed_snapshot_from_session,
    _session_start_by_id,
    _should_apply_deterministic_input,
    _state_for_session,
    _wait_for_expected_observed,
    compare_expected_observed,
    stale_reference_override,
    context_switch_override,
    content_present_override,
    synthetic_swap_override,
)
from .window import _is_replay_input_event, _on_deterministic_mismatch, _replay_input_mode, _selected_events

_MAX_RECENT_KEYS = 3


def _remember_key(recent: list, data: bytes) -> None:
    """Guarda o texto da tecla recém-enviada (janela curta para tolerar o eco)."""
    text = data.decode("utf-8", errors="ignore")
    if text:
        recent.append(text)
        del recent[:-_MAX_RECENT_KEYS]


def _run_entry_preamble(s, sel, steps, fallback=None, *, should_pause_or_cancel=None) -> list[str]:
    """Executa os passos de entrada (login → primeira tela do sistema).

    Usado quando a trilha teve o preâmbulo de shell cortado
    (``synthetic_trail.detect_session_entry``): a sessão de replay começa no
    login do destino e precisa atravessar menu wrapper/shell até a primeira
    tela do sistema antes do primeiro checkpoint. Cada passo é um dict com
    ``wait_text`` (âncora na saída), ``send`` (teclas), ``wait_stable_ms``
    (drenar até ficar quieto), ``timeout_s`` e ``optional``. Um wait que
    estoura NÃO envia o ``send`` do passo (a tecla cairia no contexto errado)
    e o preamble segue para o próximo passo com warning — se a entrada falhar,
    o primeiro checkpoint registra a divergência com as telas na UI.

    ``fallback`` (``derive_module_entry``): entrada alternativa pelo módulo
    Recital, tentada quando a âncora final não apareceu — o comando gravado
    pode depender de artefato que já não existe no destino (ex.: ``dbrt
    ferblo`` sem ``ferblo.dbo`` → FATAL ERROR com Confirm, que um ENTER
    derruba de volta ao shell antes de enviar o comando do módulo).

    A saída lida alimenta a ``screen_state`` e a trilha observada da sessão
    normalmente (o preâmbulo fica visível no replay observado). Retorna a
    lista de avisos.
    """
    warnings: list[str] = []
    tail = bytearray()

    # A âncora derivada do texto normalizado da tela (espaços colapsados,
    # sem escapes ANSI — ex.: "Recital V8.0x" do banner do dbrt) não existe
    # literalmente no fluxo cru ("Recital         V8.0" + corner ACS 'x').
    # O wait casa primeiro no fluxo cru e, em seguida, na versão limpa —
    # sem isso a âncora final estourava e o fallback era digitado no
    # prompt '>' do dbrt (run 56, captura 79).
    _ansi_tail_re = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b[()][0-9A-Z]|\x1b.")

    def _clean(data: bytes) -> bytes:
        text = _ansi_tail_re.sub("", data.decode("utf-8", "replace"))
        return re.sub(r"\s+", " ", text).encode("utf-8")

    def pump(timeout: float) -> None:
        try:
            events = sel.select(timeout=max(0.0, timeout))
        except Exception:
            return
        for key, _ in events:
            if key.data != s.session_id:
                continue
            try:
                data = s.read_out()
            except Exception:
                data = b""
            if data:
                tail.extend(data)

    def wait_for(text: str, timeout_s: float) -> bool:
        needle = str(text).encode("utf-8", "replace")
        needle_clean = _clean(needle)
        deadline = time.monotonic() + max(0.2, float(timeout_s))
        while time.monotonic() < deadline:
            if needle in tail or (needle_clean and needle_clean in _clean(bytes(tail))):
                return True
            if should_pause_or_cancel is not None:
                should_pause_or_cancel()
            pump(min(0.2, max(0.02, deadline - time.monotonic())))
        return needle in tail or (bool(needle_clean) and needle_clean in _clean(bytes(tail)))

    def wait_stable(stable_ms: int, timeout_s: float) -> None:
        deadline = time.monotonic() + max(0.2, float(timeout_s))
        while time.monotonic() < deadline:
            quiet_ms = int(time.time() * 1000) - int(getattr(s, "last_out_ms", 0) or 0)
            if quiet_ms >= stable_ms:
                return
            if should_pause_or_cancel is not None:
                should_pause_or_cancel()
            pump(min(0.2, max(0.02, deadline - time.monotonic())))

    # A âncora final (último passo com wait_text) é a que decide o fallback.
    anchor_idx = max(
        (i for i, st in enumerate(steps or []) if isinstance(st, dict) and st.get("wait_text")),
        default=None,
    )
    anchor_ok = True
    for idx, step in enumerate(steps or []):
        if not isinstance(step, dict):
            continue
        timeout_s = float(step.get("timeout_s") or 20)
        ok = True
        wait_text = str(step.get("wait_text") or "").strip()
        if wait_text:
            ok = wait_for(wait_text, timeout_s)
            if not ok:
                warnings.append(
                    f"entrada automática: âncora {wait_text!r} não apareceu em {timeout_s:.0f}s"
                )
        if idx == anchor_idx:
            anchor_ok = ok
        send = step.get("send")
        if send and ok:
            try:
                s.write_in(str(send).encode("utf-8"))
            except Exception:
                warnings.append("entrada automática: falha ao enviar teclas do passo")
        stable_ms = int(step.get("wait_stable_ms") or 0)
        if stable_ms and ok:
            wait_stable(stable_ms, timeout_s)

    if fallback and anchor_idx is not None and not anchor_ok:
        label = str(fallback.get("label") or "entrada do módulo")
        warnings.append(f"entrada automática: caminho gravado falhou — tentando {label}")
        # Um Confirm de FATAL ERROR (programa ausente no home) segura a
        # sessão; ENTER o derruba de volta ao shell.
        if b"onfirm" in bytes(tail):
            try:
                s.write_in(b"\r")
            except Exception:
                pass
            prompt = str(fallback.get("prompt") or "")
            if prompt:
                wait_for(prompt, 10)
        try:
            s.write_in(str(fallback.get("send") or "").encode("utf-8"))
        except Exception:
            warnings.append("entrada automática: falha ao enviar a entrada do módulo")
            return warnings
        fb_wait = str(fallback.get("wait_text") or "").strip()
        if fb_wait and wait_for(fb_wait, float(fallback.get("timeout_s") or 60)):
            warnings.append(f"entrada automática: sistema aberto via {label}")
        else:
            warnings.append(f"entrada automática: {label} também não abriu o sistema")
    return warnings

def replay_strict_global_controlled(
    cfg: ReplayConfig,
    params: dict | None = None,
    *,
    should_pause_or_cancel,
    on_progress,
    on_failure,
    checkpoint_timeout_ms: int = 5000,
):
    sessions: dict[str, _TargetSession] = {}
    states: dict[str, SessionReplayState] = {}
    session_configs: dict[str, ReplayConfig] = {}
    recent_keys: dict[str, list] = {}
    sel = selectors.DefaultSelector()
    input_mode = _replay_input_mode(params)
    # Entrada automática no sistema (trilha com preâmbulo de shell cortado —
    # ver synthetic_trail.detect_session_entry): executada uma vez por sessão,
    # logo após a conexão, antes do primeiro checkpoint.
    preamble_steps = list((params or {}).get("entry_preamble") or [])
    preamble_fallback = (params or {}).get("entry_fallback") or None
    preamble_done: set[str] = set()

    def remember_session_start(sid: str, ev: dict) -> None:
        if sid not in session_configs:
            state = _state_for_session(cfg, sid, ev)
            states[sid] = state
            session_configs[sid] = state.config

    def get_sess(sid: str, ev: dict | None = None) -> _TargetSession:
        if sid not in sessions:
            if sid not in session_configs:
                state = _state_for_session(cfg, sid, ev)
                states[sid] = state
                session_configs[sid] = state.config
            s = _TargetSession(session_configs[sid], sid)
            states[sid].engine = s.screen_state
            sessions[sid] = s
            sel.register(s.master_fd, selectors.EVENT_READ, data=sid)
            if preamble_steps and sid not in preamble_done:
                preamble_done.add(sid)
                try:
                    warnings = _run_entry_preamble(
                        s, sel, preamble_steps, preamble_fallback,
                        should_pause_or_cancel=should_pause_or_cancel,
                    )
                except Exception as exc:
                    warnings = [f"entrada automática falhou: {exc}"]
                if warnings:
                    states[sid].warnings.extend(warnings)
        return sessions[sid]

    def drain_output(timeout: float = 0.05):
        events = sel.select(timeout=timeout)
        for key, _ in events:
            sid2 = key.data
            try:
                _ = sessions[sid2].read_out()
            except Exception:
                pass

    def wait_checkpoint(sid: str, expected_event: dict, seq_global: int, seq_session: int = 0, record_failure: bool = True):
        s = get_sess(sid)
        expected_snapshot = _expected_snapshot_from_event(expected_event)

        def compare(observed: dict) -> dict:
            return compare_expected_observed(expected_snapshot, observed, params, event=expected_event, session_config=session_configs.get(sid), replay_config=cfg, recent_keys=recent_keys.get(sid))

        matched, match, observed = wait_for_signature_match(
            s,
            sel,
            compare=compare,
            checkpoint_quiet_ms=cfg.checkpoint_quiet_ms,
            checkpoint_timeout_ms=checkpoint_timeout_ms,
            should_pause_or_cancel=should_pause_or_cancel,
            drain_event=lambda key: sessions[key.data].read_out(),
        )
        if matched:
            return
        expected_sig = match.get("expected_sig") or expected_snapshot.get("screen_sig") or ""
        got = match.get("observed_sig") or observed.get("screen_sig") or ""
        failure_type, severity, reason = classify_checkpoint_failure(
            expected_sig=expected_sig,
            observed_sig=got,
            params=params,
            timeout_reached=True,
            concurrent_mode=False,
        )
        expected_screen = expected_screen_text_from_event(expected_event, session_configs.get(sid) or cfg)
        observed_screen = observed_screen_text_from_session(s)
        swap = synthetic_swap_override(
            match, params, expected_screen=expected_screen, observed_screen=observed_screen
        )
        if swap:
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
            failure_type, severity, reason = context_switch_override(
                failure_type,
                severity,
                reason,
                expected_screen=expected_screen,
                observed_screen=observed_screen,
            )
            failure_type, severity, reason = content_present_override(
                failure_type,
                severity,
                reason,
                expected_screen=expected_screen,
                observed_screen=observed_screen,
            )
        if record_failure:
            on_failure(
                build_failure_record(
                    session_id=sid,
                    seq_global=seq_global,
                    seq_session=seq_session,
                    event_type="checkpoint",
                    failure_type=failure_type,
                    severity=severity,
                    expected_value=expected_sig,
                    observed_value=got,
                    message=f"{reason} session={sid}: expected={expected_sig!r} got={got!r}",
                    evidence={
                        "checkpoint_timeout_ms": checkpoint_timeout_ms,
                        "checkpoint_quiet_ms": cfg.checkpoint_quiet_ms,
                        "mode": "strict-global",
                        "match": match,
                        "expected_screen": expected_screen,
                        "observed_screen": observed_screen,
                        "observed_seq": int(getattr(s, "observed_seq", 0) or 0),
                    },
                )
            )
        raise ReplayError(f"{reason} session={sid}: expected={expected_sig!r} got={got!r}")

    try:
        for ev in _selected_events(cfg.log_dir, params):
            should_pause_or_cancel()
            seq_global = int(ev.get("seq_global") or 0)
            typ = ev.get("type") or ""
            sid = ev.get("session_id") or ""

            if typ == "session_start" and sid:
                remember_session_start(sid, ev)
                continue

            if _is_replay_input_event(ev, input_mode=input_mode) and sid:
                expected_sig = str(ev.get("screen_sig") or "") if input_mode == "deterministic" else ""
                expected_snapshot = _expected_snapshot_from_event(ev)
                if input_mode == "deterministic" and _event_requires_deterministic_comparison(ev, params, session_config=session_configs.get(sid), replay_config=cfg):
                    try:
                        # Sem registro aqui: o registro definitivo (com ação
                        # skip/send-anyway) é feito pelo _deterministic_failure
                        # no except — gravar nos dois pontos duplica a falha.
                        wait_checkpoint(sid, ev, seq_global, int(ev.get("seq_session") or 0), record_failure=False)
                    except ReplayError:
                        if input_mode != "deterministic":
                            raise
                        observed_snapshot = _observed_snapshot_from_session(get_sess(sid, ev))
                        match = compare_expected_observed(expected_snapshot, observed_snapshot, params, event=ev, session_config=session_configs.get(sid), replay_config=cfg, recent_keys=recent_keys.get(sid))
                        expected_failure_sig, observed_failure_sig = _match_failure_values(match, expected_snapshot, observed_snapshot)
                        failure = _deterministic_failure(
                            sid=sid,
                            seq_global=seq_global,
                            seq_session=int(ev.get("seq_session") or 0),
                            expected_sig=expected_failure_sig,
                            observed_sig=observed_failure_sig,
                            params=params,
                            checkpoint_timeout_ms=checkpoint_timeout_ms,
                            checkpoint_quiet_ms=cfg.checkpoint_quiet_ms,
                            mode_label="strict-global-deterministic",
                            concurrent_mode=False,
                            match=match,
                            expected_screen=expected_screen_text_from_event(ev, session_configs.get(sid) or cfg),
                            observed_screen=observed_screen_text_from_session(get_sess(sid, ev)),
                            expected_event=ev,
                            observed_seq=int(getattr(get_sess(sid, ev), "observed_seq", 0) or 0),
                        )
                        if not _should_apply_deterministic_input(on_failure, failure, params=params):
                            on_progress(seq_global, None)
                            continue
                data = _decode_replay_input(ev)
                if data:
                    s = get_sess(sid, ev)
                    s.write_in(data)
                    _remember_key(recent_keys.setdefault(sid, []), data)
                on_progress(seq_global, expected_sig or None)
                drain_output(0.0)
            elif typ == "checkpoint" and sid:
                if _event_requires_deterministic_comparison(ev, params, session_config=session_configs.get(sid), replay_config=cfg):
                    try:
                        wait_checkpoint(sid, ev, seq_global, int(ev.get("seq_session") or 0))
                    except ReplayError:
                        # Checkpoint avulso (sem deterministic_input associado):
                        # a divergência já foi registrada pelo wait_checkpoint.
                        # Em send-anyway a run segue — o trim de entrada pode
                        # deixar checkpoints de draws intermediários do init do
                        # ERP que o replay não reproduz (a sessão real chega ao
                        # menu já pintado — run 58, captura 81).
                        if _on_deterministic_mismatch(params) != "send-anyway":
                            raise
                    expected_snapshot = _expected_snapshot_from_event(ev)
                    on_progress(seq_global, expected_snapshot.get("screen_sig") or expected_snapshot.get("visual_sig") or expected_snapshot.get("text_sig") or expected_snapshot.get("semantic_sig") or None)

        end_deadline = time.time() + 0.25
        while time.time() < end_deadline:
            should_pause_or_cancel()
            drain_output(0.05)
    finally:
        try:
            sel.close()
        except Exception:
            pass
        for s in sessions.values():
            s.close()


def replay_parallel_sessions_controlled(
    cfg: ReplayConfig,
    params: dict | None = None,
    *,
    should_pause_or_cancel,
    on_progress,
    on_failure,
    checkpoint_timeout_ms: int = 5000,
):
    input_mode = _replay_input_mode(params)
    per_session: dict[str, list[dict]] = {}
    for ev in _selected_events(cfg.log_dir, params):
        sid = ev.get("session_id") or ""
        if sid:
            per_session.setdefault(sid, []).append(ev)

    for sid, events in per_session.items():
        should_pause_or_cancel()
        state = _state_for_session(cfg, sid, _session_start_by_id(events, sid))
        s = _TargetSession(state.config, sid)
        state.engine = s.screen_state
        sel = selectors.DefaultSelector()
        sel.register(s.master_fd, selectors.EVENT_READ, data=sid)
        recent_keys: list = []
        try:
            for ev in events:
                should_pause_or_cancel()
                seq_global = int(ev.get("seq_global") or 0)
                typ = ev.get("type") or ""
                if _is_replay_input_event(ev, input_mode=input_mode):
                    expected_sig = str(ev.get("screen_sig") or "") if input_mode == "deterministic" else ""
                    expected_snapshot = _expected_snapshot_from_event(ev)
                    if input_mode == "deterministic" and _event_requires_deterministic_comparison(ev, params, session_config=state.config, replay_config=cfg):
                        matched, match, observed = _wait_for_expected_observed(
                            session=s,
                            selector=sel,
                            expected_event=ev,
                            params=params,
                            should_pause_or_cancel=should_pause_or_cancel,
                            checkpoint_quiet_ms=cfg.checkpoint_quiet_ms,
                            checkpoint_timeout_ms=checkpoint_timeout_ms,
                            session_config=state.config,
                            replay_config=cfg,
                            recent_keys=recent_keys,
                        )
                        if not matched:
                            expected_failure_sig, got = _match_failure_values(match, expected_snapshot, observed)
                            failure = _deterministic_failure(
                                sid=sid,
                                seq_global=seq_global,
                                seq_session=int(ev.get("seq_session") or 0),
                                expected_sig=expected_failure_sig,
                                observed_sig=got,
                                params=params,
                                checkpoint_timeout_ms=checkpoint_timeout_ms,
                                checkpoint_quiet_ms=cfg.checkpoint_quiet_ms,
                                mode_label="parallel-sessions-deterministic",
                                concurrent_mode=False,
                                match=match,
                                expected_screen=expected_screen_text_from_event(ev, state.config),
                                observed_screen=observed_screen_text_from_session(s),
                                expected_event=ev,
                                observed_seq=int(getattr(s, "observed_seq", 0) or 0),
                            )
                            if not _should_apply_deterministic_input(on_failure, failure, params=params):
                                on_progress(seq_global, None)
                                continue
                    data = _decode_replay_input(ev)
                    if data:
                        s.write_in(data)
                        _remember_key(recent_keys, data)
                    on_progress(seq_global, expected_sig or None)
                elif typ == "checkpoint":
                    if _event_requires_deterministic_comparison(ev, params, session_config=state.config, replay_config=cfg):
                        matched, match, observed = _wait_for_expected_observed(
                            session=s,
                            selector=sel,
                            expected_event=ev,
                            params=params,
                            should_pause_or_cancel=should_pause_or_cancel,
                            checkpoint_quiet_ms=cfg.checkpoint_quiet_ms,
                            checkpoint_timeout_ms=checkpoint_timeout_ms,
                            session_config=state.config,
                            replay_config=cfg,
                            recent_keys=recent_keys,
                        )
                        if not matched:
                            expected_snapshot = _expected_snapshot_from_event(ev)
                            expected_sig = match.get("expected_sig") or expected_snapshot.get("screen_sig") or ""
                            got = match.get("observed_sig") or observed.get("screen_sig") or ""
                            failure_type, severity, reason = classify_checkpoint_failure(
                                expected_sig=expected_sig,
                                observed_sig=got,
                                params=params,
                                timeout_reached=True,
                                concurrent_mode=False,
                            )
                            expected_screen = expected_screen_text_from_event(ev, state.config)
                            observed_screen = observed_screen_text_from_session(s)
                            swap = synthetic_swap_override(
                                match, params, expected_screen=expected_screen, observed_screen=observed_screen
                            )
                            if swap:
                                match, failure_type, severity, reason = swap
                            else:
                                failure_type, severity, reason = stale_reference_override(
                                    failure_type,
                                    severity,
                                    reason,
                                    expected_event=ev,
                                    expected_screen=expected_screen,
                                    observed_screen=observed_screen,
                                )
                                failure_type, severity, reason = context_switch_override(
                                    failure_type,
                                    severity,
                                    reason,
                                    expected_screen=expected_screen,
                                    observed_screen=observed_screen,
                                )
                                failure_type, severity, reason = content_present_override(
                                    failure_type,
                                    severity,
                                    reason,
                                    expected_screen=expected_screen,
                                    observed_screen=observed_screen,
                                )
                            on_failure(
                                build_failure_record(
                                    session_id=sid,
                                    seq_global=seq_global,
                                    seq_session=int(ev.get("seq_session") or 0),
                                    event_type="checkpoint",
                                    failure_type=failure_type,
                                    severity=severity,
                                    expected_value=expected_sig,
                                    observed_value=got,
                                    message=f"{reason} session={sid}: expected={expected_sig!r} got={got!r}",
                                    evidence={
                                        "checkpoint_timeout_ms": checkpoint_timeout_ms,
                                        "checkpoint_quiet_ms": cfg.checkpoint_quiet_ms,
                                        "mode": "parallel-sessions",
                                        "match": match,
                                        "expected_screen": expected_screen,
                                        "observed_screen": observed_screen,
                                    },
                                )
                            )
                            raise ReplayError(f"{reason} session={sid}: expected={expected_sig!r} got={got!r}")
                        expected_snapshot = _expected_snapshot_from_event(ev)
                        expected_sig = match.get("expected_sig") or expected_snapshot.get("screen_sig") or expected_snapshot.get("visual_sig") or expected_snapshot.get("text_sig") or expected_snapshot.get("semantic_sig") or ""
                        on_progress(seq_global, expected_sig)
        finally:
            try:
                sel.close()
            except Exception:
                pass
            s.close()


@dataclass
class LoadTestParams:
    concurrency: int = 10
    ramp_up_per_sec: float = 1.0
    speed: float = 1.0
    jitter_ms: int = 0
    on_checkpoint_mismatch: str = "continue"  # continue|fail-fast
    target_user_pool: list[str] | None = None
    match_mode: str = "strict"
    match_threshold: float = 0.92
    match_ignore_case: bool = False
    input_mode: str = "raw"
    on_deterministic_mismatch: str = "fail-fast"


def _soft_checkpoint_match(expected_sig: str, observed_sig: str, params: dict | None) -> dict | None:
    """Aplica match_mode não-estrito (contains/regex/fuzzy) sobre as assinaturas.

    Usa evaluate_checkpoint_match com match_mode/match_threshold/
    match_ignore_case dos params. Retorna o resultado quando o modo não é
    "strict" e o match é positivo; caso contrário, None (segue o fluxo de
    falha normal).
    """
    raw = params if isinstance(params, dict) else {}
    mode = str(raw.get("match_mode") or "strict").strip().lower()
    if mode == "strict":
        return None
    result = evaluate_checkpoint_match(expected_sig, observed_sig, raw)
    return result if result.get("matched") else None


def replay_parallel_sessions_concurrent_controlled(
    cfg: ReplayConfig,
    load_params: LoadTestParams,
    *,
    window_params: dict | None = None,
    should_pause_or_cancel,
    on_progress,
    on_session_result,
    on_failure,
    checkpoint_timeout_ms: int = 5000,
):
    """
    Replay por sessão com concorrência limitada e ramp-up.
    - Cada session_id roda em uma thread, preservando ordem por sessão.
    - Checkpoint mismatch pode falhar só a sessão (continue) ou o run inteiro (fail-fast).
    - speed/jitter controlam pacing entre eventos de input (bytes dir=in) baseado em ts_ms.
    - target_user_pool distribui sessões entre usuários no destino.
    """

    input_mode = _replay_input_mode(load_params.__dict__)
    per_session: dict[str, list[dict]] = {}
    for ev in _selected_events(cfg.log_dir, window_params):
        sid = ev.get("session_id") or ""
        if sid:
            per_session.setdefault(sid, []).append(ev)

    session_ids = sorted(per_session.keys())
    if load_params.concurrency < 1:
        load_params.concurrency = 1
    sem = Semaphore(load_params.concurrency)

    stop_all = {"flag": False, "err": ""}
    stop_lock = Lock()

    def pick_user(sid: str) -> str | None:
        pool = load_params.target_user_pool or []
        if not pool:
            return None
        # stable mapping by hash (sha256 é estável entre processos)
        idx = int(hashlib.sha256(str(sid).encode("utf-8")).hexdigest(), 16) % len(pool)
        return pool[idx]

    def worker(sid: str, events: list[dict]):
        nonlocal stop_all
        sem.acquire()
        try:
            should_pause_or_cancel()
            with stop_lock:
                if stop_all["flag"]:
                    on_session_result(sid, "skipped", stop_all["err"])
                    return

            user_override = pick_user(sid)
            state = _state_for_session(cfg, sid, _session_start_by_id(events, sid))
            s = _TargetSession(state.config, sid, target_user_override=user_override)
            state.engine = s.screen_state
            sel = selectors.DefaultSelector()
            sel.register(s.master_fd, selectors.EVENT_READ, data=sid)
            last_in_ts = None
            recent_keys: list = []
            try:
                for ev in events:
                    should_pause_or_cancel()
                    with stop_lock:
                        if stop_all["flag"]:
                            on_session_result(sid, "stopped", stop_all["err"])
                            return

                    seq_global = int(ev.get("seq_global") or 0)
                    typ = ev.get("type") or ""
                    if _is_replay_input_event(ev, input_mode=input_mode):
                        ts = int(ev.get("ts_ms") or 0)
                        if last_in_ts is not None and load_params.speed > 0:
                            delta = max(0, ts - last_in_ts)
                            scaled = int(delta / float(load_params.speed))
                            if load_params.jitter_ms > 0:
                                scaled += random.randint(0, load_params.jitter_ms)
                            # sleep is cooperative with pause/cancel (chunked)
                            end = time.time() + (scaled / 1000.0)
                            while time.time() < end:
                                should_pause_or_cancel()
                                time.sleep(min(0.05, end - time.time()))
                        last_in_ts = ts

                        expected_sig = str(ev.get("screen_sig") or "") if input_mode == "deterministic" else ""
                        expected_snapshot = _expected_snapshot_from_event(ev)
                        if input_mode == "deterministic" and _event_requires_deterministic_comparison(ev, load_params.__dict__, session_config=state.config, replay_config=cfg):
                            matched, match, observed = _wait_for_expected_observed(
                                session=s,
                                selector=sel,
                                expected_event=ev,
                                params=load_params.__dict__,
                                should_pause_or_cancel=should_pause_or_cancel,
                                checkpoint_quiet_ms=cfg.checkpoint_quiet_ms,
                                checkpoint_timeout_ms=checkpoint_timeout_ms,
                                session_config=state.config,
                                replay_config=cfg,
                                recent_keys=recent_keys,
                            )
                            if not matched:
                                expected_failure_sig, got = _match_failure_values(match, expected_snapshot, observed)
                                if _soft_checkpoint_match(expected_failure_sig, got, load_params.__dict__) is not None:
                                    matched = True
                            if not matched:
                                expected_failure_sig, got = _match_failure_values(match, expected_snapshot, observed)
                                failure = _deterministic_failure(
                                    sid=sid,
                                    seq_global=seq_global,
                                    seq_session=int(ev.get("seq_session") or 0),
                                    expected_sig=expected_failure_sig,
                                    observed_sig=got,
                                    params=load_params.__dict__,
                                    checkpoint_timeout_ms=checkpoint_timeout_ms,
                                    checkpoint_quiet_ms=cfg.checkpoint_quiet_ms,
                                    mode_label="parallel-sessions-concurrent-deterministic",
                                    concurrent_mode=True,
                                    match=match,
                                    expected_screen=expected_screen_text_from_event(ev, state.config),
                                    observed_screen=observed_screen_text_from_session(s),
                                    expected_event=ev,
                                    observed_seq=int(getattr(s, "observed_seq", 0) or 0),
                                )
                                msg = str(failure.get("message") or "")
                                try:
                                    should_apply = _should_apply_deterministic_input(on_failure, failure, params=load_params.__dict__)
                                except ReplayError:
                                    on_session_result(sid, "failed", msg)
                                    if load_params.on_checkpoint_mismatch == "fail-fast":
                                        with stop_lock:
                                            stop_all["flag"] = True
                                            stop_all["err"] = msg
                                    return
                                if not should_apply:
                                    on_progress(seq_global, None)
                                    continue

                        data = _decode_replay_input(ev)
                        if data:
                            s.write_in(data)
                            _remember_key(recent_keys, data)
                        on_progress(seq_global, expected_sig or None)
                    elif typ == "checkpoint":
                        if _event_requires_deterministic_comparison(ev, load_params.__dict__, session_config=state.config, replay_config=cfg):
                            matched, match, observed = _wait_for_expected_observed(
                                session=s,
                                selector=sel,
                                expected_event=ev,
                                params=load_params.__dict__,
                                should_pause_or_cancel=should_pause_or_cancel,
                                checkpoint_quiet_ms=cfg.checkpoint_quiet_ms,
                                checkpoint_timeout_ms=checkpoint_timeout_ms,
                                session_config=state.config,
                                replay_config=cfg,
                                recent_keys=recent_keys,
                            )
                            if not matched:
                                expected_snapshot = _expected_snapshot_from_event(ev)
                                expected_sig = match.get("expected_sig") or expected_snapshot.get("screen_sig") or ""
                                got = match.get("observed_sig") or observed.get("screen_sig") or ""
                                if _soft_checkpoint_match(expected_sig, got, load_params.__dict__) is not None:
                                    matched = True
                            if matched:
                                expected_snapshot = _expected_snapshot_from_event(ev)
                                expected_sig = match.get("expected_sig") or expected_snapshot.get("screen_sig") or ""
                                on_progress(seq_global, expected_sig)
                            else:
                                expected_snapshot = _expected_snapshot_from_event(ev)
                                expected_sig = match.get("expected_sig") or expected_snapshot.get("screen_sig") or ""
                                got = match.get("observed_sig") or observed.get("screen_sig") or ""
                                failure_type, severity, reason = classify_checkpoint_failure(
                                    expected_sig=expected_sig,
                                    observed_sig=got,
                                    params=load_params.__dict__,
                                    timeout_reached=True,
                                    concurrent_mode=True,
                                )
                                expected_screen = expected_screen_text_from_event(ev, state.config)
                                observed_screen = observed_screen_text_from_session(s)
                                swap = synthetic_swap_override(
                                    match, load_params.__dict__, expected_screen=expected_screen, observed_screen=observed_screen
                                )
                                if swap:
                                    match, failure_type, severity, reason = swap
                                else:
                                    failure_type, severity, reason = stale_reference_override(
                                        failure_type,
                                        severity,
                                        reason,
                                        expected_event=ev,
                                        expected_screen=expected_screen,
                                        observed_screen=observed_screen,
                                    )
                                    failure_type, severity, reason = context_switch_override(
                                        failure_type,
                                        severity,
                                        reason,
                                        expected_screen=expected_screen,
                                        observed_screen=observed_screen,
                                    )
                                    failure_type, severity, reason = content_present_override(
                                        failure_type,
                                        severity,
                                        reason,
                                        expected_screen=expected_screen,
                                        observed_screen=observed_screen,
                                    )
                                msg = f"{reason} session={sid}: expected={expected_sig!r} got={got!r}"
                                on_failure(
                                    build_failure_record(
                                        session_id=sid,
                                        seq_global=seq_global,
                                        seq_session=int(ev.get("seq_session") or 0),
                                        event_type="checkpoint",
                                        failure_type=failure_type,
                                        severity=severity,
                                        expected_value=expected_sig,
                                        observed_value=got,
                                        message=msg,
                                        evidence={
                                            "checkpoint_timeout_ms": checkpoint_timeout_ms,
                                            "checkpoint_quiet_ms": cfg.checkpoint_quiet_ms,
                                            "mode": "parallel-sessions-concurrent",
                                            "match": match,
                                            "expected_screen": expected_screen,
                                            "observed_screen": observed_screen,
                                        },
                                    )
                                )
                                on_session_result(sid, "failed", msg)
                                if load_params.on_checkpoint_mismatch == "fail-fast":
                                    with stop_lock:
                                        stop_all["flag"] = True
                                        stop_all["err"] = msg
                                return

                on_session_result(sid, "success", "")
            finally:
                try:
                    sel.close()
                except Exception:
                    pass
                s.close()
        except ReplayError as e:
            msg = str(e)
            on_session_result(sid, "failed", msg)
            if load_params.on_checkpoint_mismatch == "fail-fast":
                with stop_lock:
                    stop_all["flag"] = True
                    stop_all["err"] = msg
        finally:
            sem.release()

    threads: list[Thread] = []
    # ramp-up: start threads gradually
    interval = 0.0
    if load_params.ramp_up_per_sec and load_params.ramp_up_per_sec > 0:
        interval = 1.0 / float(load_params.ramp_up_per_sec)

    for idx, sid in enumerate(session_ids):
        should_pause_or_cancel()
        t = Thread(target=worker, args=(sid, per_session[sid]), daemon=True)
        threads.append(t)
        t.start()
        if interval > 0 and idx < len(session_ids) - 1:
            end = time.time() + interval
            while time.time() < end:
                should_pause_or_cancel()
                time.sleep(min(0.05, end - time.time()))

    # wait all
    for t in threads:
        while t.is_alive():
            should_pause_or_cancel()
            t.join(timeout=0.1)

    with stop_lock:
        if stop_all["flag"] and load_params.on_checkpoint_mismatch == "fail-fast":
            raise ReplayError(stop_all["err"])
