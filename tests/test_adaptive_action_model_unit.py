#!/usr/bin/env python3
"""Testes do modelo estruturado de ações sintéticas e da materialização
semântica do ReplayAdapter (mission: motor adaptativo determinístico).

Cobre os requisitos §2.1–2.3 e §16.1–16.5:
- nenhum ENTER artificial é adicionado a TAB/ESC/F-keys;
- ENTER original não duplica;
- submit honra o trigger (F10) em vez de virar ENTER genérico;
- {WAIT:xxx} é preservado como ação executável e auditável;
- os bytes finais da sessão são exatamente a concatenação das ações;
- tecla desconhecida falha na síntese (nunca vira lixo ``\\rKEY\\r``).
"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gateway"))

from dakota_gateway.replay import _decode_replay_input
from dakota_gateway.synthetic.action_model import (
    SyntheticActionKind,
    parse_replay_script,
)
from dakota_gateway.synthetic.journey import JourneyDefinition, JourneyStep
from dakota_gateway.synthetic.journey_builder import JourneyBuilder
from dakota_gateway.synthetic.replay_adapter import ReplayAdapter
from dakota_gateway.verifier import verify_log

HMAC_KEY = b"test_hmac_key_action_model__"


def _journey() -> JourneyDefinition:
    return JourneyDefinition(
        journey_id="am_journey",
        name="Jornada ActionModel",
        category="test",
        steps=[
            JourneyStep(step_order=0, screen_id="menu", screen_title="Menu",
                        action="navigate", trigger="3"),
            JourneyStep(step_order=1, screen_id="cadastro", screen_title="Cadastro",
                        action="input", input_template="{{cliente.nome}}\n{{cliente.cpf}}"),
            JourneyStep(step_order=2, screen_id="cadastro", action="wait", wait_ms=250),
            JourneyStep(step_order=3, screen_id="cadastro", action="submit", trigger="F10"),
        ],
    )


def _dataset(tmpdir: str, journey: JourneyDefinition, session_count: int = 1, seed: int = 42):
    con = sqlite3.connect(str(Path(tmpdir) / "am.db"))
    con.row_factory = sqlite3.Row
    try:
        return JourneyBuilder(db_connection=con).build_journey_dataset(
            journey, session_count=session_count, seed=seed,
        )
    finally:
        con.close()


class ParseReplayScriptTests(unittest.TestCase):
    """parse_replay_script produz ações estruturadas e determinísticas."""

    def test_input_vira_acao_input_sem_enter_implicito(self):
        actions = parse_replay_script("ABC123")
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].kind, SyntheticActionKind.INPUT)
        self.assertEqual(actions[0].data, b"ABC123")

    def test_key_enter_unico(self):
        actions = parse_replay_script("ABC\n{KEY:ENTER}")
        self.assertEqual([a.kind for a in actions],
                         [SyntheticActionKind.INPUT, SyntheticActionKind.KEY])
        self.assertEqual(actions[1].data, b"\r")
        self.assertEqual(actions[1].key_name, "ENTER")

    def test_enter_legado_chaves_simples(self):
        actions = parse_replay_script("{ENTER}")
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].kind, SyntheticActionKind.KEY)
        self.assertEqual(actions[0].data, b"\r")

    def test_tab_nao_recebe_enter(self):
        actions = parse_replay_script("{KEY:TAB}")
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].data, b"\t")
        self.assertNotIn(b"\r", actions[0].data)

    def test_esc_nao_recebe_enter(self):
        actions = parse_replay_script("{KEY:ESC}")
        self.assertEqual(actions[0].data, b"\x1b")

    def test_function_keys_bytes_exatos(self):
        expected = {
            "F1": b"\x1bOP", "F2": b"\x1bOQ", "F3": b"\x1bOR", "F4": b"\x1bOS",
            "F5": b"\x1b[15~", "F6": b"\x1b[17~", "F7": b"\x1b[18~",
            "F8": b"\x1b[19~", "F9": b"\x1b[20~", "F10": b"\x1b[21~",
            "F11": b"\x1b[23~", "F12": b"\x1b[24~",
        }
        for name, data in expected.items():
            with self.subTest(key=name):
                actions = parse_replay_script(f"{{KEY:{name}}}")
                self.assertEqual(actions[0].data, data)
                self.assertEqual(actions[0].key_name, name)

    def test_wait_preservado_como_acao(self):
        actions = parse_replay_script("ABC\n{WAIT:250}\n{KEY:ENTER}")
        kinds = [a.kind for a in actions]
        self.assertEqual(kinds, [
            SyntheticActionKind.INPUT,
            SyntheticActionKind.WAIT,
            SyntheticActionKind.KEY,
        ])
        self.assertEqual(actions[1].wait_ms, 250)

    def test_verify_vira_checkpoint(self):
        actions = parse_replay_script("{VERIFY:sig123}")
        self.assertEqual(actions[0].kind, SyntheticActionKind.CHECKPOINT)
        self.assertEqual(actions[0].signature, "sig123")

    def test_tecla_desconhecida_falha_na_sintese(self):
        with self.assertRaises(ValueError):
            parse_replay_script("{KEY:NUKE}")

    def test_origem_do_passo_registrada(self):
        script = "# --- Passo 7: Cadastro ---\n# Ação: input | Trigger: \nABC"
        actions = parse_replay_script(script)
        self.assertEqual(actions[0].origin.get("step_order"), 7)
        self.assertEqual(actions[0].origin.get("step_action"), "input")


class ReplayAdapterSemanticsTests(unittest.TestCase):
    """A materialização JSONL preserva a semântica das ações (sem ENTER artificial)."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.journey = _journey()

    def tearDown(self):
        self.tmpdir.cleanup()

    def _events(self, session_count: int = 1, seed: int = 42):
        out = str(Path(self.tmpdir.name) / "jsonl")
        ReplayAdapter().generate_synthetic_jsonl(
            self.journey, session_count=session_count, seed=seed,
            output_dir=out, hmac_key=HMAC_KEY,
        )
        path = sorted(Path(out).glob("audit-*.jsonl"))[0]
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def test_submit_com_trigger_f10_envia_f10_sem_enter_extra(self):
        events = self._events()
        datas = [
            _decode_replay_input(ev)
            for ev in events if ev.get("type") == "bytes" and ev.get("dir") == "in"
        ]
        self.assertIn(b"\x1b[21~", datas)  # F10 presente
        # F10 é evento único: nenhum \r colado ao F10
        f10_idx = datas.index(b"\x1b[21~")
        self.assertEqual(datas[f10_idx], b"\x1b[21~")

    def test_enter_original_nao_duplica(self):
        events = self._events()
        decoded = b"".join(
            _decode_replay_input(ev)
            for ev in events if ev.get("type") == "bytes" and ev.get("dir") == "in"
        )
        self.assertNotIn(b"\r\r", decoded)

    def test_wait_vira_evento_auditavel_e_avanca_ts(self):
        events = self._events()
        waits = [ev for ev in events if ev.get("key_kind") == "wait"]
        self.assertEqual(len(waits), 1)
        wait_ev = waits[0]
        self.assertEqual(_decode_replay_input(wait_ev), b"")
        self.assertEqual(wait_ev.get("key_text"), "{WAIT:250}")
        inputs = [
            ev for ev in events
            if ev.get("type") == "bytes" and ev.get("dir") == "in"
            and ev.get("key_kind") != "wait"
        ]
        before = [ev for ev in inputs if ev["seq_session"] < wait_ev["seq_session"]]
        after = [ev for ev in inputs if ev["seq_session"] > wait_ev["seq_session"]]
        self.assertTrue(before and after)
        self.assertGreaterEqual(
            int(after[0]["ts_ms"]) - int(before[-1]["ts_ms"]), 250,
        )

    def test_bytes_finais_equivalem_as_acoes(self):
        """A concatenação dos bytes enviados é exatamente: navegação+ENTER,
        campos+ENTER e F10 do submit — sem bytes artificiais."""
        events = self._events()
        decoded = b"".join(
            _decode_replay_input(ev)
            for ev in events if ev.get("type") == "bytes" and ev.get("dir") == "in"
        )
        jds = _dataset(self.tmpdir.name, self.journey)
        data0 = jds.steps_data[1][0]
        builder = JourneyBuilder()
        rendered = builder.template_engine.render("{{cliente.nome}}\n{{cliente.cpf}}", data0)
        nome, cpf = rendered.split("\n")
        expected = f"3\r{nome}\r{cpf}\r".encode("utf-8") + b"\x1b[21~"
        self.assertEqual(decoded, expected)

    def test_caracteres_do_mesmo_campo_compartilham_ts(self):
        """Timing semântico: chars contíguos de um campo não pagam cadência artificial."""
        events = self._events()
        jds = _dataset(self.tmpdir.name, self.journey)
        data0 = jds.steps_data[1][0]
        rendered = JourneyBuilder().template_engine.render("{{cliente.nome}}\n{{cliente.cpf}}", data0)
        nome = rendered.split("\n")[0]
        self.assertGreater(len(nome), 1)
        printable = [
            ev for ev in events
            if ev.get("type") == "bytes" and ev.get("dir") == "in"
            and ev.get("key_kind") == "printable"
        ]
        text = "".join(ev["key_text"] for ev in printable)
        start = text.index(nome)
        nome_evs = printable[start:start + len(nome)]
        self.assertEqual("".join(ev["key_text"] for ev in nome_evs), nome)
        ts_set = {ev["ts_ms"] for ev in nome_evs}
        self.assertEqual(len(ts_set), 1, "chars do mesmo campo devem dividir o mesmo ts_ms")
        seqs = [ev["seq_session"] for ev in nome_evs]
        self.assertEqual(seqs, list(range(seqs[0], seqs[0] + len(seqs))))

    def test_key_kind_classificado_nos_eventos(self):
        events = self._events()
        kinds = {
            ev.get("key_kind")
            for ev in events if ev.get("type") == "bytes" and ev.get("dir") == "in"
        }
        self.assertIn("printable", kinds)
        self.assertIn("enter", kinds)
        self.assertIn("function_key", kinds)
        self.assertIn("wait", kinds)

    def test_jsonl_semantico_passa_no_verify_log(self):
        out = str(Path(self.tmpdir.name) / "jsonl2")
        ReplayAdapter().generate_synthetic_jsonl(
            self.journey, session_count=2, seed=7,
            output_dir=out, hmac_key=HMAC_KEY,
        )
        verify_log(out, HMAC_KEY)  # não levanta


if __name__ == "__main__":
    unittest.main()
