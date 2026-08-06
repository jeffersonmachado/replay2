"""Testes unitários do validador de cenários operacionais (S2).

`scenario_validator.py` foi extraído de `operational_scenario_service.py`:
validação pura de payload, sem banco. Cobre defaults, erros de domínio e
normalização de limites SLA/tags.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATEWAY_DIR = ROOT / "gateway"
if str(GATEWAY_DIR) not in sys.path:
    sys.path.insert(0, str(GATEWAY_DIR))

from control.services.scenario_validator import normalize_operational_scenario_payload


class ScenarioValidatorUnitTests(unittest.TestCase):
    def test_defaults_payload_vazio(self):
        clean = normalize_operational_scenario_payload(None)
        self.assertEqual(clean["scenario_type"], "replay")
        self.assertEqual(clean["mode"], "strict-global")
        self.assertEqual(clean["name"], "")
        self.assertIsNone(clean["sla_max_failure_rate_pct"])
        self.assertIsNone(clean["sla_max_criticality_score"])
        self.assertIsNone(clean["target_env_id"])
        self.assertEqual(clean["tags"], [])
        self.assertEqual(clean["params"], {})

    def test_scenario_type_invalido(self):
        with self.assertRaises(ValueError):
            normalize_operational_scenario_payload({"scenario_type": "chaos"})

    def test_mode_invalido(self):
        with self.assertRaises(ValueError):
            normalize_operational_scenario_payload({"mode": "random"})

    def test_sla_pct_fora_da_faixa(self):
        with self.assertRaises(ValueError):
            normalize_operational_scenario_payload({"sla_max_failure_rate_pct": 101})
        with self.assertRaises(ValueError):
            normalize_operational_scenario_payload({"sla_max_failure_rate_pct": -1})

    def test_sla_score_fora_da_faixa(self):
        with self.assertRaises(ValueError):
            normalize_operational_scenario_payload({"sla_max_criticality_score": 100.1})

    def test_normalizacao_completa(self):
        clean = normalize_operational_scenario_payload({
            "name": "  Pedido de venda  ",
            "scenario_type": "STRESS",
            "mode": "parallel-sessions",
            "tags": "vendas, vendas, , critico",
            "sla_max_failure_rate_pct": "5.04",
            "sla_max_criticality_score": "80",
            "target_env_id": "9",
            "params": {"concurrency": 4},
        })
        self.assertEqual(clean["name"], "Pedido de venda")
        self.assertEqual(clean["scenario_type"], "stress")
        self.assertEqual(clean["mode"], "parallel-sessions")
        self.assertEqual(clean["tags"], ["vendas", "critico"])
        self.assertEqual(clean["sla_max_failure_rate_pct"], 5.0)
        self.assertEqual(clean["sla_max_criticality_score"], 80.0)
        self.assertEqual(clean["target_env_id"], 9)
        self.assertEqual(clean["params"], {"concurrency": 4})

    def test_params_nao_dict_vira_vazio(self):
        clean = normalize_operational_scenario_payload({"params": "abc"})
        self.assertEqual(clean["params"], {})


if __name__ == "__main__":
    unittest.main()
