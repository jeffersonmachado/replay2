#!/usr/bin/env python3
"""Testes do perfil de latência local (§13, §14, §16.16–17)."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gateway"))

from dakota_gateway.replay_control.latency_profile import LatencyProfile


class LatencyProfileTests(unittest.TestCase):
    def test_estatisticas_basicas(self):
        p = LatencyProfile()
        for v in (100.0, 200.0, 300.0, 400.0, 500.0):
            p.record("aix-mig24", "checkpoint", "est361", v)
        stats = p.stats("aix-mig24", "checkpoint", "est361")
        self.assertEqual(stats["count"], 5)
        self.assertEqual(stats["min"], 100.0)
        self.assertEqual(stats["max"], 500.0)
        self.assertEqual(stats["mean"], 300.0)
        self.assertGreater(stats["variance"], 0)
        self.assertIn("p50", stats)
        self.assertIn("p95", stats)
        self.assertIn("p99", stats)
        self.assertIn("last_updated", stats)

    def test_percentis_corretos(self):
        p = LatencyProfile()
        for v in range(1, 101):
            p.record("env", "ac", "ctx", float(v))
        stats = p.stats("env", "ac", "ctx")
        self.assertEqual(stats["p50"], 50.5)
        self.assertGreaterEqual(stats["p95"], 94.0)
        self.assertGreaterEqual(stats["p99"], 98.0)

    def test_perfis_separados_por_ambiente(self):
        """§16.16: AIX e Linux nunca misturam amostras."""
        p = LatencyProfile()
        p.record("aix-mig24", "checkpoint", "est361", 420.0)
        p.record("linux-x86", "checkpoint", "est361", 130.0)
        aix = p.stats("aix-mig24", "checkpoint", "est361")
        lin = p.stats("linux-x86", "checkpoint", "est361")
        self.assertEqual(aix["mean"], 420.0)
        self.assertEqual(lin["mean"], 130.0)
        self.assertEqual(aix["count"], 1)
        self.assertEqual(lin["count"], 1)

    def test_timeout_sugerido_usa_cauda_nao_p50(self):
        p = LatencyProfile()
        for v in (100.0,) * 50 + (900.0,):
            p.record("env", "ac", "", v)
        timeout = p.suggested_timeout_ms("env", "ac")
        stats = p.stats("env", "ac", "")
        self.assertGreater(timeout, stats["p50"])
        self.assertGreaterEqual(timeout, stats["p99"])

    def test_timeout_sem_dados_usa_floor(self):
        p = LatencyProfile()
        self.assertEqual(p.suggested_timeout_ms("env", "ac", floor_ms=5000), 5000)

    def test_persistencia_json_offline(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "lat.json")
            p = LatencyProfile(path=path)
            p.record("aix-mig24", "checkpoint", "est361", 420.0)
            p.save()
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
            self.assertIn("aix-mig24", json.dumps(raw))
            p2 = LatencyProfile(path=path)
            self.assertEqual(p2.stats("aix-mig24", "checkpoint", "est361")["mean"], 420.0)

    def test_amostras_limitadas(self):
        p = LatencyProfile(max_samples=100)
        for v in range(500):
            p.record("env", "ac", "", float(v))
        self.assertLessEqual(p.stats("env", "ac", "")["count"], 500)
        # reservoir limitado: nunca cresce além de max_samples armazenados
        self.assertLessEqual(p.sample_count("env", "ac", ""), 100)


if __name__ == "__main__":
    unittest.main()
