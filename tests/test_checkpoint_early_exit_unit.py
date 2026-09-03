"""Testes da saída antecipada da espera de checkpoint em mismatch estável.

Regressão da otimização do replay sintético send-anyway: quando a tela
observada já estabilizou e diverge da esperada (dado substituído de
propósito), esperar o timeout cheio de 5s é desperdício — a tela não vai
virar a esperada. Medido nas runs 59-61 da captura 81 (AIX): 656 falhas ×
5s ≈ 55 min dos ~64 min da run eram espera pura.
"""
from __future__ import annotations

import selectors
import threading
import time
from unittest.mock import patch

from dakota_gateway import replay_compare
from dakota_gateway.replay_compare import wait_for_signature_match
from dakota_gateway.replay_control import deterministic


def _now_ms() -> int:
    return int(time.time() * 1000)


class _FakeSession:
    """Sessão mínima para a máquina de espera (last_out_ms + read_out)."""

    def __init__(self, text: str = "tela inicial"):
        self.text = text
        self.last_out_ms = _now_ms()

    def read_out(self) -> None:  # sem saída nova no seletor vazio
        return None


def _observed(session: _FakeSession) -> dict:
    return {"screen_text": session.text}


def _run_wait(session, compare, *, quiet_ms=100, timeout_ms=4000, **kwargs):
    selector = selectors.DefaultSelector()
    try:
        with patch.object(replay_compare, "observed_snapshot_from_session", _observed):
            start = time.monotonic()
            result = wait_for_signature_match(
                session,
                selector,
                compare=compare,
                checkpoint_quiet_ms=quiet_ms,
                checkpoint_timeout_ms=timeout_ms,
                **kwargs,
            )
            return result, time.monotonic() - start
    finally:
        selector.close()


def test_mismatch_estavel_send_anyway_sai_cedo():
    """Tela estável que nunca casa: sai na janela de carência, não no timeout."""
    session = _FakeSession()
    never = lambda observed: {"matched": False}  # noqa: E731
    (matched, _, _), elapsed = _run_wait(
        session,
        never,
        timeout_ms=4000,
        early_exit_on_stable_mismatch=True,
    )
    assert matched is False
    # quiet (100ms) + carência (>=500ms) — bem abaixo do timeout de 4s.
    assert elapsed < 2.0, f"esperou {elapsed:.1f}s — timeout cheio não foi evitado"


def test_sem_flag_mantem_espera_ate_o_timeout():
    """Comportamento default inalterado: sem a flag, espera o timeout cheio."""
    session = _FakeSession()
    never = lambda observed: {"matched": False}  # noqa: E731
    (matched, _, _), elapsed = _run_wait(session, never, timeout_ms=800)
    assert matched is False
    assert elapsed >= 0.7, f"saiu em {elapsed:.2f}s — o default não pode encurtar"


def test_saida_tardia_reseta_carencia_e_ainda_casa():
    """Eco que chega durante a carência reseta a janela e o match é capturado."""
    session = _FakeSession()

    def late_output():
        time.sleep(0.4)
        session.text = "Digite a sua opcao: 0"
        session.last_out_ms = _now_ms()

    threading.Thread(target=late_output, daemon=True).start()
    compare = lambda observed: {"matched": str(observed.get("screen_text") or "").endswith("0")}  # noqa: E731
    (matched, _, _), elapsed = _run_wait(
        session,
        compare,
        timeout_ms=4000,
        early_exit_on_stable_mismatch=True,
    )
    assert matched is True
    assert elapsed < 2.0


def test_wait_deterministico_flag_apenas_em_send_anyway_ou_skip():
    """_wait_for_expected_observed liga a saída antecipada só em send-anyway/skip."""
    captured: list[dict] = []

    def fake_wait(*args, **kwargs):
        captured.append(kwargs)
        return False, {"matched": False}, {}

    session = _FakeSession()
    selector = selectors.DefaultSelector()
    try:
        with patch.object(deterministic, "wait_for_signature_match", fake_wait):
            for params, expected in (
                ({"on_deterministic_mismatch": "send-anyway"}, True),
                ({"on_deterministic_mismatch": "skip"}, True),
                ({"on_deterministic_mismatch": "fail-fast"}, False),
                (None, False),
            ):
                captured.clear()
                deterministic._wait_for_expected_observed(
                    session=session,
                    selector=selector,
                    expected_event={"type": "deterministic_input", "screen_sample": "x"},
                    params=params,
                    should_pause_or_cancel=None,
                    checkpoint_quiet_ms=100,
                    checkpoint_timeout_ms=1000,
                )
                assert captured, "wait_for_signature_match não foi chamado"
                assert captured[0].get("early_exit_on_stable_mismatch") is expected, params
    finally:
        selector.close()



# ---------------------------------------------------------------------------
# Regressão: o caminho strict-global (executor) também precisa da flag.
# Medido nas runs 62/64 da captura 81 (AIX, v0.9.1): a run ficou idêntica
# (~63 min) porque o wait_checkpoint do strict-global chamava
# wait_for_signature_match sem early_exit_on_stable_mismatch — a flag só
# estava ligada no _wait_for_expected_observed (parallel/concurrent).
# ---------------------------------------------------------------------------

import base64
import json
from unittest import mock

from dakota_gateway.replay import ReplayConfig
from dakota_gateway.replay_control import executors as executors_mod


class _ExecFakeSelector:
    def register(self, *args, **kwargs):
        return None

    def select(self, timeout=None):
        return []

    def close(self):
        return None


class _ExecFakeSession:
    """Sessão mínima para o executor strict-global com a espera stubada."""

    def __init__(self, cfg, sid, target_user_override=None):
        self.session_id = sid
        self.master_fd = 0
        self.last_out_ms = 0
        self.screen_state = object()

    def read_out(self):
        return b""

    def write_in(self, data: bytes):
        return None

    def close(self):
        return None


def _write_deterministic_capture(log_dir) -> None:
    key_b64 = base64.b64encode(b"0").decode("ascii")
    events = [
        {"type": "session_start", "session_id": "s1", "seq_global": 1,
         "seq_session": 1, "rows": 25, "cols": 80},
        {"type": "deterministic_input", "session_id": "s1", "seq_global": 2,
         "seq_session": 2, "ts_ms": 1000, "screen_sig": "sig-esperada",
         "key_b64": key_b64},
        {"type": "checkpoint", "session_id": "s1", "seq_global": 3,
         "seq_session": 3, "ts_ms": 1010, "screen_sig": "sig-esperada"},
    ]
    lines = [json.dumps(ev) for ev in events]
    (log_dir / "audit-early.part001.jsonl").write_text("\n".join(lines), encoding="utf-8")


def _run_strict_global(tmp_path, params, captured: list) -> None:
    def fake_wait(*args, **kwargs):
        captured.append(kwargs)
        return True, {"matched": True}, {}

    _write_deterministic_capture(tmp_path)
    cfg = ReplayConfig(log_dir=str(tmp_path), target_host="local", checkpoint_quiet_ms=0)
    with mock.patch.object(executors_mod, "_TargetSession", _ExecFakeSession), \
         mock.patch.object(executors_mod.selectors, "DefaultSelector", _ExecFakeSelector), \
         mock.patch.object(executors_mod, "wait_for_signature_match", fake_wait):
        executors_mod.replay_strict_global_controlled(
            cfg,
            params=params,
            should_pause_or_cancel=lambda: None,
            on_progress=lambda *a: None,
            on_failure=lambda f: None,
        )


def test_strict_global_send_anyway_liga_saida_antecipada(tmp_path):
    """Strict-global send-anyway: o wait_checkpoint sai cedo em mismatch estável."""
    captured: list[dict] = []
    _run_strict_global(
        tmp_path,
        {"input_mode": "deterministic", "on_deterministic_mismatch": "send-anyway"},
        captured,
    )
    # Um wait por deterministic_input + um por checkpoint avulso.
    assert len(captured) == 2, f"esperava 2 esperas, veio {len(captured)}"
    for kwargs in captured:
        assert kwargs.get("early_exit_on_stable_mismatch") is True, (
            "strict-global send-anyway sem early exit — timeout cheio por divergência"
        )


def test_strict_global_skip_liga_saida_antecipada(tmp_path):
    captured: list[dict] = []
    _run_strict_global(
        tmp_path,
        {"input_mode": "deterministic", "on_deterministic_mismatch": "skip"},
        captured,
    )
    assert captured
    for kwargs in captured:
        assert kwargs.get("early_exit_on_stable_mismatch") is True


def test_strict_global_fail_fast_mantem_timeout_cheio(tmp_path):
    """fail-fast (default): comportamento inalterado, sem saída antecipada."""
    captured: list[dict] = []
    _run_strict_global(
        tmp_path,
        {"input_mode": "deterministic"},
        captured,
    )
    assert captured
    for kwargs in captured:
        assert not kwargs.get("early_exit_on_stable_mismatch"), (
            "fail-fast não pode encurtar a espera do checkpoint"
        )
