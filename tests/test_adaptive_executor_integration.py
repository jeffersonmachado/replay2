#!/usr/bin/env python3
"""Testes de integração do motor adaptativo no executor concorrente
(§8, §16.6–8, §16.12–13, §16.19–27 no nível do executor).

Usa sessões fake (sem SSH/PTY real) — o que se prova aqui é a política de
execução do Replay2: quais bytes são escritos, em quantos writes, com qual
pacing, e o que a telemetria registra.
"""
from __future__ import annotations

import base64
import json
import threading
import time
from pathlib import Path
from threading import Lock
from unittest import mock

import pytest

from dakota_gateway.replay import ReplayConfig, ReplayError
from dakota_gateway.replay_control import executors as executors_mod
from dakota_gateway.replay_control.adaptive_scheduler import (
    AdaptiveRuntime,
    POLICY_ADAPTIVE,
    POLICY_ADAPTIVE_SHADOW,
    POLICY_CONSERVATIVE,
)
from dakota_gateway.replay_control.execution_telemetry import RunTelemetry
from dakota_gateway.replay_control.executors import (
    LoadTestParams,
    replay_parallel_sessions_concurrent_controlled,
)


class _FakeSelector:
    def register(self, *args, **kwargs):
        return None

    def select(self, timeout=None):
        return []

    def close(self):
        return None


class _FakeSession:
    lock = threading.Lock()
    writes: dict[str, list[bytes]] = {}
    write_hook = None

    @classmethod
    def reset(cls, write_hook=None):
        with cls.lock:
            cls.writes = {}
            cls.write_hook = write_hook

    def __init__(self, cfg, sid, target_user_override=None):
        self.session_id = sid
        self.master_fd = 0
        self.last_out_ms = 0
        self.screen_state = object()

    def canonical_snapshot_now(self):
        return {"text_sig": "", "visual_sig": "", "semantic_sig": "", "screen_sig": ""}

    def read_out(self):
        return b""

    def write_in(self, data: bytes):
        cls = type(self)
        hook = cls.write_hook
        if hook is not None:
            hook(self.session_id, data)
        with cls.lock:
            cls.writes.setdefault(self.session_id, []).append(bytes(data))

    def close(self):
        return None


def _patch_sessions():
    return (
        mock.patch.object(executors_mod, "_TargetSession", _FakeSession),
        mock.patch.object(executors_mod.selectors, "DefaultSelector", _FakeSelector),
    )


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _write_capture(log_dir: Path, sessions: dict[str, list[dict]]) -> None:
    """Grava capture com eventos por sessão (dicts já prontos)."""
    lines: list[str] = []
    seq = 0
    for sid, events in sessions.items():
        seq += 1
        lines.append(json.dumps({
            "type": "session_start", "session_id": sid,
            "seq_global": seq, "seq_session": 1, "rows": 25, "cols": 80,
        }))
        for i, ev in enumerate(events):
            seq += 1
            ev = dict(ev)
            ev.setdefault("type", "bytes")
            ev.setdefault("dir", "in")
            ev["session_id"] = sid
            ev["seq_global"] = seq
            ev["seq_session"] = i + 2
            lines.append(json.dumps(ev))
    (log_dir / "audit-t.part001.jsonl").write_text("\n".join(lines), encoding="utf-8")


def _field_events(text: str, ts0: int = 1000, delta: int = 0) -> list[dict]:
    return [
        {"ts_ms": ts0 + i * delta, "data_b64": _b64(ch.encode()), "key_kind": "printable"}
        for i, ch in enumerate(text)
    ]


def _run(log_dir: Path, *, policy: str, speed: float = 1.0, telemetry=None,
         synthetic: bool = False, evidence=None, sessions=2):
    results: list[tuple[str, str]] = []
    p1, p2 = _patch_sessions()
    rt = AdaptiveRuntime(
        policy=policy,
        telemetry=telemetry,
        synthetic_trail=synthetic,
        typeahead_evidence=evidence or {},
    )
    with p1, p2:
        replay_parallel_sessions_concurrent_controlled(
            ReplayConfig(log_dir=str(log_dir), target_host="local", checkpoint_quiet_ms=0),
            LoadTestParams(concurrency=sessions, ramp_up_per_sec=0, speed=speed),
            window_params={},
            should_pause_or_cancel=lambda: None,
            on_progress=lambda *a: None,
            on_session_result=lambda sid, st, msg: results.append((sid, st, msg)),
            on_failure=lambda f: None,
            adaptive=rt,
        )
    return results


class AdaptiveBatchingTests(unittest := __import__("unittest").TestCase):
    def setUp(self):
        _FakeSession.reset()

    def test_conservative_envia_evento_a_evento(self):
        """§16.27: política default conservadora = comportamento histórico."""
        _write_capture(self._tmp(), {"s0": _field_events("ABCDE", delta=0)})
        self._run_policy(POLICY_CONSERVATIVE)
        self.assertEqual(len(_FakeSession.writes["s0"]), 5)

    def test_adaptive_funde_campo_em_um_write_mesmos_bytes(self):
        """§16.6/16.7: batch seguro — um write, bytes finais idênticos."""
        _write_capture(self._tmp(), {"s0": _field_events("ABCDE", delta=0)})
        tel = RunTelemetry()
        self._run_policy(POLICY_ADAPTIVE, telemetry=tel)
        writes = _FakeSession.writes["s0"]
        self.assertEqual(writes, [b"ABCDE"])
        snap = tel.snapshot()
        self.assertEqual(snap["batch_count"], 1)
        self.assertEqual(snap["batched_action_count"], 5)

    def test_batch_nao_atravessa_checkpoint(self):
        """§16.8: checkpoint é fronteira dura mesmo em adaptive."""
        events = (
            _field_events("AB", ts0=1000)
            + [{"type": "checkpoint", "ts_ms": 1100}]
            + _field_events("CD", ts0=1200)
        )
        _write_capture(self._tmp(), {"s0": events})
        self._run_policy(POLICY_ADAPTIVE)
        writes = _FakeSession.writes["s0"]
        self.assertEqual(writes, [b"AB", b"CD"])

    def test_tecla_especial_e_barreira(self):
        """§16.9: ENTER/F-key quebram o batch."""
        events = (
            _field_events("AB", ts0=1000)
            + [{"ts_ms": 1100, "data_b64": _b64(b"\r"), "key_kind": "enter"}]
            + _field_events("CD", ts0=1200)
        )
        _write_capture(self._tmp(), {"s0": events})
        self._run_policy(POLICY_ADAPTIVE)
        writes = _FakeSession.writes["s0"]
        self.assertEqual(writes, [b"AB", b"\r", b"CD"])

    def test_delta_positivo_sem_evidencia_nao_colapsa(self):
        """§16.12: pacing positivo entre campos sem evidência → conservador."""
        _write_capture(self._tmp(), {"s0": _field_events("ABC", delta=500)})
        tel = RunTelemetry()
        self._run_policy(POLICY_ADAPTIVE, telemetry=tel)
        writes = _FakeSession.writes["s0"]
        self.assertEqual(writes, [b"A", b"B", b"C"])
        snap = tel.snapshot()
        self.assertEqual(snap["batch_count"], 0)
        self.assertGreaterEqual(snap["conservative_fallback_count"], 1)

    def test_delta_positivo_com_trilha_sintetica_colapsa(self):
        """Deltas artificiais de trilha sintética colapsam (§2.1, §12)."""
        _write_capture(self._tmp(), {"s0": _field_events("ABC", delta=50)})
        tel = RunTelemetry()
        self._run_policy(POLICY_ADAPTIVE, telemetry=tel, synthetic=True)
        writes = _FakeSession.writes["s0"]
        self.assertEqual(writes, [b"ABC"])
        self.assertEqual(tel.snapshot()["batching_saved_ms"], 100)

    def test_delta_positivo_com_evidencia_typeahead_colapsa(self):
        """§16.11: evidência segura de type-ahead libera o colapso."""
        _write_capture(self._tmp(), {"s0": _field_events("ABC", delta=60)})
        # seqs 3 e 4 (B e C) são os inputs type-ahead
        tel = RunTelemetry()
        self._run_policy(POLICY_ADAPTIVE, telemetry=tel,
                         evidence={"s0": [3, 4]})
        writes = _FakeSession.writes["s0"]
        self.assertEqual(writes, [b"ABC"])

    def test_batching_nao_mistura_sessoes(self):
        """§16.22: buffers são locais ao worker; bytes nunca cruzam sessões."""
        _write_capture(self._tmp(), {
            "s0": _field_events("AAAA", ts0=1000),
            "s1": _field_events("BBBB", ts0=1000),
        })
        self._run_policy(POLICY_ADAPTIVE, sessions=2)
        self.assertEqual(_FakeSession.writes["s0"], [b"AAAA"])
        self.assertEqual(_FakeSession.writes["s1"], [b"BBBB"])

    def test_ordem_dentro_da_sessao_preservada(self):
        """§16.21: ordem dos bytes dentro da sessão é idêntica à captura."""
        events = (
            _field_events("12", ts0=1000)
            + [{"ts_ms": 1100, "data_b64": _b64(b"\r"), "key_kind": "enter"}]
            + _field_events("34", ts0=1200)
            + [{"ts_ms": 1300, "data_b64": _b64(b"\t"), "key_kind": "tab"}]
            + _field_events("56", ts0=1400)
        )
        _write_capture(self._tmp(), {"s0": events})
        self._run_policy(POLICY_ADAPTIVE)
        joined = b"".join(_FakeSession.writes["s0"])
        self.assertEqual(joined, b"12\r34\t56")

    def test_shadow_executa_conservador_e_preve_economia(self):
        """§19: shadow não altera execução, mas registra decisões/economia."""
        _write_capture(self._tmp(), {"s0": _field_events("ABCD", delta=50)})
        tel = RunTelemetry()
        self._run_policy(POLICY_ADAPTIVE_SHADOW, telemetry=tel, synthetic=True)
        writes = _FakeSession.writes["s0"]
        # execução conservadora: um write por evento
        self.assertEqual(len(writes), 4)
        snap = tel.snapshot()
        self.assertIn("shadow", snap)
        shadow = snap["shadow"]
        self.assertEqual(shadow["total_actions"], 4)
        self.assertEqual(shadow["safe_candidates"], 1)
        self.assertEqual(shadow["potential_saved_ms"], 150)
        self.assertEqual(shadow["false_safe_decisions"], 0)

    def test_cancel_dentro_de_batch_para_a_sessao(self):
        """§16.23: cancelamento cooperativo continua funcionando."""
        _write_capture(self._tmp(), {"s0": _field_events("ABCDE", delta=200)})

        calls = {"n": 0}

        def cancel_after_first():
            calls["n"] += 1
            if calls["n"] > 2:
                raise ReplayError("cancelled")

        results: list = []
        p1, p2 = _patch_sessions()
        rt = AdaptiveRuntime(policy=POLICY_ADAPTIVE, telemetry=RunTelemetry())
        with p1, p2:
            with pytest.raises(ReplayError):
                replay_parallel_sessions_concurrent_controlled(
                    ReplayConfig(log_dir=str(self._tmp_path), target_host="local",
                                 checkpoint_quiet_ms=0),
                    LoadTestParams(concurrency=1, ramp_up_per_sec=0, speed=1.0),
                    window_params={},
                    should_pause_or_cancel=cancel_after_first,
                    on_progress=lambda *a: None,
                    on_session_result=lambda sid, st, msg: results.append((sid, st)),
                    on_failure=lambda f: None,
                    adaptive=rt,
                )

    # helpers -------------------------------------------------------------
    _tmp_path = None

    def _tmp(self) -> Path:
        import tempfile
        if self._tmp_path is None:
            self._tmp_path = Path(tempfile.mkdtemp())
        return self._tmp_path

    def _run_policy(self, policy, telemetry=None, synthetic=False, evidence=None, sessions=2):
        results: list = []
        p1, p2 = _patch_sessions()
        rt = AdaptiveRuntime(
            policy=policy, telemetry=telemetry,
            synthetic_trail=synthetic, typeahead_evidence=evidence or {},
        )
        with p1, p2:
            replay_parallel_sessions_concurrent_controlled(
                ReplayConfig(log_dir=str(self._tmp_path), target_host="local",
                             checkpoint_quiet_ms=0),
                LoadTestParams(concurrency=sessions, ramp_up_per_sec=0, speed=1.0),
                window_params={},
                should_pause_or_cancel=lambda: None,
                on_progress=lambda *a: None,
                on_session_result=lambda sid, st, msg: results.append((sid, st, msg)),
                on_failure=lambda f: None,
                adaptive=rt,
            )
        ok = [st for _, st, _ in results]
        self.assertTrue(all(st == "success" for st in ok), results)
        return results

    def tearDown(self):
        import shutil
        if self._tmp_path is not None:
            shutil.rmtree(self._tmp_path, ignore_errors=True)
            self._tmp_path = None


if __name__ == "__main__":
    unittest.main()


class SyntheticParamsThreadingTests(__import__('unittest').TestCase):
    """Os params sintéticos da run precisam chegar ao worker concorrente.

    Regressão: o runner montava LoadTestParams só com campos de carga —
    ``synthetic``/``synthetic_substitutions``/``synthetic_swap_fast_exit``
    sumiam no caminho parallel/concurrent (load_params.__dict__ é o params
    dos waits/comparações). Sem eles o swap do de→para nunca era detectado
    e o fast path da carência ficava morto fora do strict-global.
    """

    _tmp_path = None

    def _tmp(self) -> Path:
        import tempfile
        if self._tmp_path is None:
            self._tmp_path = Path(tempfile.mkdtemp())
        return self._tmp_path

    def tearDown(self):
        import shutil
        if self._tmp_path is not None:
            shutil.rmtree(self._tmp_path, ignore_errors=True)
            self._tmp_path = None

    def test_builder_propaga_params_sinteticos(self):
        from dakota_gateway.replay_control.executors import load_test_params_from_dict
        lp = load_test_params_from_dict({
            "concurrency": 2,
            "speed": 8,
            "input_mode": "deterministic",
            "on_deterministic_mismatch": "send-anyway",
            "synthetic": True,
            "synthetic_substitutions": [["229,9", "763,0"]],
            "synthetic_swap_fast_exit": "1",
        })
        self.assertEqual(lp.concurrency, 2)
        self.assertEqual(lp.speed, 8.0)
        self.assertTrue(lp.__dict__["synthetic"])
        self.assertEqual(
            lp.__dict__["synthetic_substitutions"], [["229,9", "763,0"]]
        )
        from dakota_gateway.replay_control.deterministic import (
            _substitution_pairs_from_params,
            _synthetic_swap_fast_exit,
        )
        self.assertTrue(_synthetic_swap_fast_exit(lp.__dict__))
        self.assertEqual(
            _substitution_pairs_from_params(lp.__dict__), [["229,9", "763,0"]]
        )

    def test_builder_default_run_real_nao_liga_fast_exit(self):
        from dakota_gateway.replay_control.executors import load_test_params_from_dict
        from dakota_gateway.replay_control.deterministic import (
            _synthetic_swap_fast_exit,
        )
        lp = load_test_params_from_dict({"concurrency": 2})
        self.assertFalse(_synthetic_swap_fast_exit(lp.__dict__))

    def test_worker_concorrente_recebe_params_sinteticos(self):
        """End-to-end no worker: o params dos waits carrega os campos."""
        events = [
            {"type": "deterministic_input", "ts_ms": 1000,
             "key_b64": _b64(b"1"), "key_kind": "printable", "screen_sig": "sig1"},
            {"type": "deterministic_input", "ts_ms": 1400,
             "key_b64": _b64(b"2"), "key_kind": "printable", "screen_sig": "sig2"},
        ]
        _write_capture(self._tmp(), {"s0": events})
        captured: list[dict] = []

        def fake_wait(*args, **kwargs):
            captured.append(dict(kwargs.get("params") or {}))
            return False, {"matched": False, "synthetic_substitution": True}, {}

        _FakeSession.reset()
        p1, p2 = _patch_sessions()
        rt = AdaptiveRuntime(policy=POLICY_ADAPTIVE, telemetry=RunTelemetry(),
                             synthetic_trail=True)
        lp = LoadTestParams(
            concurrency=1, ramp_up_per_sec=0, speed=1.0,
            input_mode="deterministic", on_deterministic_mismatch="send-anyway",
        )
        lp.synthetic = True
        lp.synthetic_substitutions = [["229,9", "763,0"]]
        with p1, p2, \
             mock.patch.object(executors_mod, "_wait_for_expected_observed", fake_wait), \
             mock.patch.object(executors_mod, "_deterministic_failure", lambda **kw: {"message": "m"}), \
             mock.patch.object(executors_mod, "expected_screen_text_from_event", lambda *a, **kw: ""), \
             mock.patch.object(executors_mod, "observed_screen_text_from_session", lambda *a, **kw: ""):
            replay_parallel_sessions_concurrent_controlled(
                ReplayConfig(log_dir=str(self._tmp_path), target_host="local",
                             checkpoint_quiet_ms=0),
                lp,
                window_params={},
                should_pause_or_cancel=lambda: None,
                on_progress=lambda *a: None,
                on_session_result=lambda *a: None,
                on_failure=lambda f: None,
                adaptive=rt,
            )
        self.assertTrue(captured, "nenhum wait de checkpoint aconteceu")
        from dakota_gateway.replay_control.deterministic import (
            _synthetic_swap_fast_exit,
        )
        for params in captured:
            self.assertTrue(params.get("synthetic"), params)
            self.assertTrue(_synthetic_swap_fast_exit(params), params)


class ConvergencePacingSkipTests(__import__("unittest").TestCase):
    """§12/§24: pacing após convergência comprovada é espera artificial.

    Quando o wait de checkpoint do evento anterior convergiu (match ou
    divergência swap totalmente explicada pelo de→para), a tela está num
    estado conhecido e estável — o sleep de cadência (delta de ts_ms /
    speed) antes do próximo input é overhead puro do Replay2. Só vale na
    política adaptive; divergência real e runs conservadoras mantêm o
    pacing integral. Checkpoints e waits explícitos nunca são tocados.
    """

    _tmp_path = None

    def _tmp(self) -> Path:
        import tempfile
        if self._tmp_path is None:
            self._tmp_path = Path(tempfile.mkdtemp())
        return self._tmp_path

    def tearDown(self):
        import shutil
        if self._tmp_path is not None:
            shutil.rmtree(self._tmp_path, ignore_errors=True)
            self._tmp_path = None

    @staticmethod
    def _det_events(n: int, *, ts0: int = 1000, delta: int = 400) -> list[dict]:
        return [
            {"type": "deterministic_input", "ts_ms": ts0 + i * delta,
             "key_b64": _b64(str(i).encode()), "key_kind": "printable",
             "screen_sig": f"sig{i}"}
            for i in range(n)
        ]

    def _run_det(self, events, *, policy, wait_results, synthetic=False,
                 speed=1.0, telemetry=None):
        _write_capture(self._tmp(), {"s0": events})
        results_iter = iter(wait_results)

        def fake_wait(*args, **kwargs):
            try:
                matched, match = next(results_iter)
            except StopIteration:
                matched, match = True, {"matched": True}
            return matched, match, {}

        _FakeSession.reset()
        p1, p2 = _patch_sessions()
        rt = AdaptiveRuntime(policy=policy, telemetry=telemetry,
                             synthetic_trail=synthetic)
        lp = LoadTestParams(
            concurrency=1, ramp_up_per_sec=0, speed=speed,
            input_mode="deterministic", on_deterministic_mismatch="send-anyway",
        )
        if synthetic:
            lp.synthetic = True
            lp.synthetic_substitutions = [["1", "9"]]
        with p1, p2, \
             mock.patch.object(executors_mod, "_wait_for_expected_observed", fake_wait), \
             mock.patch.object(executors_mod, "_deterministic_failure", lambda **kw: {"message": "m"}), \
             mock.patch.object(executors_mod, "expected_screen_text_from_event", lambda *a, **kw: ""), \
             mock.patch.object(executors_mod, "observed_screen_text_from_session", lambda *a, **kw: ""):
            replay_parallel_sessions_concurrent_controlled(
                ReplayConfig(log_dir=str(self._tmp_path), target_host="local",
                             checkpoint_quiet_ms=0),
                lp,
                window_params={},
                should_pause_or_cancel=lambda: None,
                on_progress=lambda *a: None,
                on_session_result=lambda *a: None,
                on_failure=lambda f: None,
                adaptive=rt,
            )
        return _FakeSession.writes.get("s0", [])

    def test_adaptive_pula_pacing_apos_convergencia(self):
        """3 inputs com waits convergidos: só o 1º não tem pacing (delta do
        1º é zero por construção); os 400ms × 2 seguintes são pulados."""
        tel = RunTelemetry()
        writes = self._run_det(
            self._det_events(3), policy=POLICY_ADAPTIVE,
            wait_results=[(True, {"matched": True})] * 3, telemetry=tel,
        )
        self.assertEqual(writes, [b"0", b"1", b"2"])
        snap = tel.snapshot()
        self.assertLess(snap["pacing_ms"], 150, snap["pacing_ms"])
        self.assertEqual(snap["convergence_pacing_skip_count"], 2)
        self.assertGreaterEqual(snap["convergence_pacing_saved_ms"], 700)

    def test_conservative_mantem_pacing_mesmo_convergido(self):
        tel = RunTelemetry()
        self._run_det(
            self._det_events(3), policy=POLICY_CONSERVATIVE,
            wait_results=[(True, {"matched": True})] * 3, telemetry=tel,
        )
        snap = tel.snapshot()
        self.assertGreaterEqual(snap["pacing_ms"], 700, snap["pacing_ms"])
        self.assertEqual(snap.get("convergence_pacing_skip_count", 0), 0)

    def test_divergencia_real_nao_pula_pacing(self):
        """Mismatch sem explicação de swap: estado desconhecido → pacing."""
        tel = RunTelemetry()
        self._run_det(
            self._det_events(3), policy=POLICY_ADAPTIVE, synthetic=True,
            wait_results=[(False, {"matched": False})] * 3, telemetry=tel,
        )
        snap = tel.snapshot()
        self.assertGreaterEqual(snap["pacing_ms"], 700, snap["pacing_ms"])
        self.assertEqual(snap["convergence_pacing_skip_count"], 0)

    def test_swap_sintetico_converge_e_pula_pacing(self):
        """Divergência totalmente explicada pelo de→para = estado conhecido."""
        tel = RunTelemetry()
        self._run_det(
            self._det_events(3), policy=POLICY_ADAPTIVE, synthetic=True,
            wait_results=[(False, {"matched": False, "synthetic_substitution": True})] * 3,
            telemetry=tel,
        )
        snap = tel.snapshot()
        self.assertLess(snap["pacing_ms"], 150, snap["pacing_ms"])
        self.assertEqual(snap["convergence_pacing_skip_count"], 2)

    def test_swap_com_kill_switch_nao_pula(self):
        """synthetic_swap_fast_exit=0 desliga também o skip de pacing."""
        tel = RunTelemetry()
        _FakeSession.reset()
        events = self._det_events(3)
        _write_capture(self._tmp(), {"s0": events})
        results_iter = iter([(False, {"matched": False, "synthetic_substitution": True})] * 3)

        def fake_wait(*args, **kwargs):
            try:
                matched, match = next(results_iter)
            except StopIteration:
                matched, match = True, {"matched": True}
            return matched, match, {}

        p1, p2 = _patch_sessions()
        rt = AdaptiveRuntime(policy=POLICY_ADAPTIVE, telemetry=tel,
                             synthetic_trail=True)
        lp = LoadTestParams(
            concurrency=1, ramp_up_per_sec=0, speed=1.0,
            input_mode="deterministic", on_deterministic_mismatch="send-anyway",
        )
        lp.synthetic = True
        lp.synthetic_substitutions = [["1", "9"]]
        lp.synthetic_swap_fast_exit = "0"
        with p1, p2, \
             mock.patch.object(executors_mod, "_wait_for_expected_observed", fake_wait), \
             mock.patch.object(executors_mod, "_deterministic_failure", lambda **kw: {"message": "m"}), \
             mock.patch.object(executors_mod, "expected_screen_text_from_event", lambda *a, **kw: ""), \
             mock.patch.object(executors_mod, "observed_screen_text_from_session", lambda *a, **kw: ""):
            replay_parallel_sessions_concurrent_controlled(
                ReplayConfig(log_dir=str(self._tmp_path), target_host="local",
                             checkpoint_quiet_ms=0),
                lp,
                window_params={},
                should_pause_or_cancel=lambda: None,
                on_progress=lambda *a: None,
                on_session_result=lambda *a: None,
                on_failure=lambda f: None,
                adaptive=rt,
            )
        snap = tel.snapshot()
        self.assertGreaterEqual(snap["pacing_ms"], 700, snap["pacing_ms"])
        self.assertEqual(snap.get("convergence_pacing_skip_count", 0), 0)
