"""Testes do override de log_dir no replay da sessão (trilha sintética)."""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from control.services.capture_service import resolve_replay_log_dir


class ResolveReplayLogDirTests(unittest.TestCase):
    def test_sem_override_retorna_o_da_captura(self):
        self.assertEqual(
            resolve_replay_log_dir("/cap/abc", ""),
            str(Path("/cap/abc").resolve()),
        )

    def test_trilha_sintetica_dentro_da_captura_e_aceita(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp) / "cap"
            trail = base / "synthetic" / "capture-13-replay" / "trail"
            trail.mkdir(parents=True)
            self.assertEqual(
                resolve_replay_log_dir(str(base), str(trail)),
                str(trail.resolve()),
            )

    def test_o_proprio_log_dir_e_aceito(self):
        with TemporaryDirectory() as tmp:
            self.assertEqual(
                resolve_replay_log_dir(tmp, tmp),
                str(Path(tmp).resolve()),
            )

    def test_caminho_fora_da_captura_e_rejeitado(self):
        with TemporaryDirectory() as a, TemporaryDirectory() as b:
            with self.assertRaises(ValueError):
                resolve_replay_log_dir(a, b)

    def test_prefixo_parecido_nao_engana(self):
        """/cap/abc-vsl não está dentro de /cap/abc."""
        with TemporaryDirectory() as tmp:
            base = Path(tmp) / "abc"
            base.mkdir()
            irmao = Path(tmp) / "abc-malicioso"
            irmao.mkdir()
            with self.assertRaises(ValueError):
                resolve_replay_log_dir(str(base), str(irmao))

    def test_path_traversal_e_rejeitado(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp) / "cap"
            base.mkdir()
            with self.assertRaises(ValueError):
                resolve_replay_log_dir(str(base), str(base / ".." / ".." / "etc"))


if __name__ == "__main__":
    unittest.main()
