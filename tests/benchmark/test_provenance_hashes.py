"""FASE 4 — Hashes de proveniência do contrato: identidade dos artefatos.

O contrato carrega os SHA-256 dos artefatos comparados (jornada, dataset,
versão da aplicação, perfil de think time). Caso real v7: os três hashes
principais eram IDÊNTICOS sem justificativa e o think time usava
``"def1277b"`` + 56 zeros — placeholder manual. A decisão não pode comparar
o que não está identificado:

- placeholder evidente (não-hex de 64 chars, literal "unknown" ou sequência
  de ≥ 32 zeros) → ERRO na criação (``ContractViolation``);
- hashes válidos IGUAIS entre si sem justificativa registrada
  (``hash_justifications``) → ERRO na criação;
- manifestos legados carregam para auditoria (``load_contract`` não valida),
  mas a DECISÃO barra qualquer problema → INCONCLUSIVE;
- regressão v7: recálculo full-path dos artefatos oficiais → INCONCLUSIVE
  (proveniência + rede ausente), relatório sem "Equivalência funcional:
  OK"/"comprovada".

Estes testes DEVEM FALHAR antes da correção e PASSAR depois dela.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "gateway"))

from dakota_gateway.benchmark.comparison import (  # noqa: E402
    build_comparison,
    build_decision,
)
from dakota_gateway.benchmark.contract import (  # noqa: E402
    ContractViolation,
    StopConditions,
    ThinkTimeProfile,
    create_contract,
    load_contract,
    validate_provenance_hashes,
)
from dakota_gateway.benchmark.models import (  # noqa: E402
    EnvironmentRunResult,
    ExperimentResult,
    OperationSample,
)

BASELINE = "aix-power"
TARGET = "linux-x86"
V7_DIR = ROOT / "artifacts" / "benchmarks" / "cap13-aix-linux-oficial-v7"


def _contrato(**over):
    kwargs = {
        "experiment_id": "exp-prov",
        "journey_set_sha256": "a" * 64,
        "dataset_sha256": "b" * 64,
        "application_version_sha256": "c" * 64,
        "seed": 1, "terminal_geometry": "80x24",
        "concurrency_levels": (1,),
        "warmup_seconds": 0, "measurement_seconds": 30,
        "cooldown_seconds": 0, "iterations": 1,
        "think_time_profile": ThinkTimeProfile(type="none", sha256="d" * 64,
                                               params={}),
        "stop_conditions": StopConditions(),
        "environments": (BASELINE, TARGET),
    }
    kwargs.update(over)
    return create_contract(**kwargs)


class TestValidacaoHashes(unittest.TestCase):
    """Regras de validação unitária dos hashes de proveniência."""

    def test_hashes_validos_distintos_sem_problemas(self) -> None:
        self.assertEqual([], validate_provenance_hashes(_contrato()))

    def test_hash_vazio_tolerado_na_criacao_mas_nao_na_decisao(self) -> None:
        contrato = _contrato(application_version_sha256="")
        self.assertEqual([], validate_provenance_hashes(
            contrato, exigir_presenca=False))
        problemas = validate_provenance_hashes(contrato, exigir_presenca=True)
        self.assertTrue(any(p["campo"] == "application_version_sha256"
                            for p in problemas))

    def test_hash_nao_hex_e_erro_na_criacao(self) -> None:
        with self.assertRaises(ContractViolation):
            _contrato(dataset_sha256="não-é-um-hash")

    def test_hash_unknown_e_erro_na_criacao(self) -> None:
        with self.assertRaises(ContractViolation):
            _contrato(dataset_sha256="unknown")

    def test_hash_zeros_consecutivos_e_placeholder_na_criacao(self) -> None:
        """Caso real v7: think time "def1277b" + 56 zeros."""
        with self.assertRaises(ContractViolation):
            _contrato(think_time_profile=ThinkTimeProfile(
                type="deterministic", sha256="def1277b" + "0" * 56,
                params={}))

    def test_hashes_iguais_sem_justificativa_erro_na_criacao(self) -> None:
        """Caso real v7: os 3 hashes principais idênticos."""
        with self.assertRaises(ContractViolation):
            _contrato(journey_set_sha256="e" * 64, dataset_sha256="e" * 64)

    def test_hashes_iguais_com_justificativa_registrada_passam(self) -> None:
        contrato = _contrato(
            journey_set_sha256="e" * 64, dataset_sha256="e" * 64,
            hash_justifications={
                "dataset_sha256==journey_set_sha256": "mesma captura de "
                "origem (jornada derivada do dataset auditado)"})
        self.assertEqual([], validate_provenance_hashes(contrato))


def _amostra(env: str, idx: int) -> OperationSample:
    ns0 = 1_000_000_000 + idx * 20_000_000
    return OperationSample(
        experiment_id="exp-prov", environment_id=env, iteration=1,
        concurrency=1, virtual_user_id="vu-1", journey_id="j",
        step_id=f"ev-{idx}", phase="MEASUREMENT", started_ns=ns0,
        finished_ns=ns0 + 10_000_000, latency_ms=10.0,
        success=True, timeout=False, functional_divergence=False,
        error_code=None, screen_sig_checked=True,
        expected_screen_sig="sha256:x", observed_screen_sig="sha256:x")


def _host_completo(ts_ms: int) -> dict:
    return {"ts_ms": ts_ms, "cpu_pct": 12.0, "mem_pct": 30.0,
            "swap_pct": 0.5, "disk_latency_ms": 2.0, "iops": 40.0,
            "disk_busy_pct": 5.0, "load1": 0.4,
            "net_rx_kbs": 120.0, "net_tx_kbs": 90.0}


def _resultado_valido(tmp: Path) -> ExperimentResult:
    """Resultado com TODOS os demais gates verdes — só a proveniência manda."""
    for nome in ("host-base.jsonl", "host-alvo.jsonl"):
        (tmp / nome).write_text(
            "".join(json.dumps(_host_completo(1000 + i * 5000)) + "\n"
                    for i in range(4)), encoding="utf-8")
    runs = []
    for env, nome in ((BASELINE, "host-base.jsonl"),
                      (TARGET, "host-alvo.jsonl")):
        run = EnvironmentRunResult(
            environment_id=env, iteration=1, concurrency=1,
            status="COMPLETED",
            samples=[_amostra(env, i) for i in range(20)],
            host_samples_path=str(tmp / nome))
        run.host_clock_offset_ms = 0
        run.host_clock_offset_measured = True
        run.checkpoints_executed = 20
        run.checkpoints_checked = 20
        runs.append(run)
    return ExperimentResult(
        contract_sha256="c" * 64, status="COMPLETED", runs=runs)


class TestGateProvenienciaNaDecisao(unittest.TestCase):
    """A decisão barra problemas de proveniência (INCONCLUSIVE)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def _contrato_legado_placeholder(self) -> object:
        """Manifesto legado (v7-like): carrega, mas a decisão barra."""
        manifesto = self.tmp / "manifest.json"
        manifesto.write_text(json.dumps({
            "schema_version": "1.0",
            "experiment_id": "exp-legado",
            "created_at": "2026-08-30T00:00:00Z",
            "journey_set_sha256": "61630c24" + "ab" * 28,
            "dataset_sha256": "61630c24" + "ab" * 28,
            "application_version_sha256": "61630c24" + "ab" * 28,
            "seed": 1, "terminal_geometry": "80x24",
            "concurrency_levels": [1], "warmup_seconds": 0,
            "measurement_seconds": 30, "cooldown_seconds": 0,
            "iterations": 1,
            "think_time_profile": {"type": "deterministic",
                                   "sha256": "def1277b" + "0" * 56,
                                   "params": {}},
            "stop_conditions": {},
            "environments": [BASELINE, TARGET],
        }), encoding="utf-8")
        return load_contract(manifesto)

    def test_decisao_inconclusive_com_problemas_de_proveniencia(self) -> None:
        contrato = self._contrato_legado_placeholder()
        resultado = _resultado_valido(self.tmp)
        comparison = build_comparison(
            resultado, baseline_env=BASELINE, target_env=TARGET,
            contract=contrato)
        self.assertTrue(comparison["provenance_problems"])
        decision = build_decision(resultado, comparison)
        self.assertEqual("INCONCLUSIVE", decision.verdict)
        self.assertIsNone(decision.recommendation)
        self.assertIn("proveni", " ".join(decision.reasons))

    def test_sem_contrato_sem_gate_de_proveniencia(self) -> None:
        resultado = _resultado_valido(self.tmp)
        comparison = build_comparison(
            resultado, baseline_env=BASELINE, target_env=TARGET)
        self.assertEqual([], comparison["provenance_problems"])
        decision = build_decision(resultado, comparison)
        self.assertEqual("PASS", decision.verdict)

    def test_contrato_limpo_passa(self) -> None:
        resultado = _resultado_valido(self.tmp)
        comparison = build_comparison(
            resultado, baseline_env=BASELINE, target_env=TARGET,
            contract=_contrato())
        decision = build_decision(resultado, comparison)
        self.assertEqual("PASS", decision.verdict)


@unittest.skipUnless((V7_DIR / "experiment-manifest.json").is_file(),
                     "artefatos oficiais v7 ausentes")
class TestRegressaoV7(unittest.TestCase):
    """Recálculo full-path do v7: INCONCLUSIVE e relatório honesto."""

    def test_recalculo_v7(self) -> None:
        from dakota_gateway.benchmark.report import write_experiment_artifacts
        from dakota_gateway.cli import _bench_rebuild_result

        contrato = load_contract(V7_DIR / "experiment-manifest.json")
        resultado = _bench_rebuild_result(V7_DIR, contrato)
        comparison = build_comparison(
            resultado, baseline_env=BASELINE, target_env=TARGET,
            contract=contrato)
        decision = build_decision(resultado, comparison)

        # veredito: NUNCA mais WARN/PASS com proveniência placeholder,
        # rede ausente e paridade per_env
        self.assertEqual("INCONCLUSIVE", decision.verdict)
        self.assertIsNone(decision.recommendation)
        razoes = " ".join(decision.reasons)
        self.assertIn("proveni", razoes)

        # proveniência: 3 hashes idênticos sem justificativa + think time
        # placeholder ("def1277b" + zeros)
        campos = {p["campo"] for p in comparison["provenance_problems"]}
        self.assertIn("think_time_profile.sha256", campos)
        self.assertTrue(any("==" in c for c in campos))

        # cobertura: rede ausente no sampler do v7 → gargalo não declarável
        for env in (BASELINE, TARGET):
            self.assertIn(
                "rede",
                comparison["collector_coverage"][env]["host"]
                ["grupos_ausentes"])
            self.assertFalse(comparison["bottleneck_evidence"][env]["ok"])

        # parada classificada pela evidência: âncora ausente NÃO é saturação
        self.assertEqual("capacidade_nao_determinada",
                         comparison["stop_classification"]["category"])

        # clock skew medido (caso real: AIX ~171 s atrasado)
        skew = comparison["clock_skew"][BASELINE]
        self.assertTrue(skew["measured"])
        self.assertGreater(skew["max_abs_offset_ms"], 100_000)
        self.assertFalse(skew["within_gate"])

        # relatório: sem "OK"/"comprovada", com status diferenciado
        with tempfile.TemporaryDirectory() as tmp:
            write_experiment_artifacts(
                Path(tmp), resultado, comparison, {}, decision)
            md = (Path(tmp) / "report.md").read_text(encoding="utf-8")
            rep = json.loads(
                (Path(tmp) / "report.json").read_text(encoding="utf-8"))
        self.assertNotIn("Equivalência funcional: OK", md)
        self.assertNotIn("Equivalência funcional comprovada", md)
        self.assertEqual("paridade_nao_comprovada",
                         rep["functional_validation"]["status"])
        self.assertIn("Cobertura por coletor", md)


if __name__ == "__main__":
    unittest.main()
