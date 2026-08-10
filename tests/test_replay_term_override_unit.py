#!/usr/bin/env python3
"""Regressão: param ``term`` da run deve vencer o ``term`` do session_start.

Bug (<=0.8.14): o replay copiava o TERM gravado na captura (terminal do
usuário, ex.: dk100 do TeraTerm) para a sessão headless. Termos com
sequências de porta auxiliar (ESC[5i/ESC[4i) fazem a aplicação remota
travar — a tela observada ficava vazia e todos os checkpoints falhavam com
timeout. O param ``term`` da run (ReplayConfig.term_override) agora tem
prioridade sobre o ``term`` do session_start.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gateway"))

from dakota_gateway.replay import ReplayConfig, _session_config_from_event  # noqa: E402


class TermOverrideUnitTests(unittest.TestCase):
    def _cfg(self, **kw) -> ReplayConfig:
        return ReplayConfig(log_dir="/tmp", target_host="127.0.0.1", **kw)

    def test_override_vence_term_da_captura(self):
        cfg = self._cfg(term="xterm", term_override="xterm")
        out = _session_config_from_event(cfg, {"session_id": "s1", "term": "dk100"})
        self.assertEqual(out.term, "xterm")

    def test_sem_override_mantem_term_da_captura(self):
        cfg = self._cfg(term="xterm")
        out = _session_config_from_event(cfg, {"session_id": "s1", "term": "dk100"})
        self.assertEqual(out.term, "dk100")

    def test_override_aplicado_mesmo_sem_term_no_evento(self):
        cfg = self._cfg(term="xterm", term_override="vt220")
        out = _session_config_from_event(cfg, {"session_id": "s1"})
        self.assertEqual(out.term, "vt220")


if __name__ == "__main__":
    unittest.main()
