"""FASE 3 — Classificação da parada da escada (stop_condition).

Uma falha de preâmbulo ou de admissão de sessão NÃO é prova automática de
saturação do servidor. A parada é classificada pela EVIDÊNCIA registrada:

- ``saturacao_comprovada``: stop_conditions MEDIDAS (host_cpu_pct,
  swap_growth_mb, p99_limit_ms, error_rate_pct) — há número medido;
- ``limite_licenca``: admissão com evidência de licença do runtime
  ("User limit exceeded", "license", "licença");
- ``falha_login``: admissão com evidência de autenticação
  ("Permission denied", "authentication");
- ``falha_launcher``: admissão com o launcher/comando não encontrado
  ("command not found", "ksh: ... not found");
- ``limite_orquestrador``: recurso local do orquestrador esgotado
  ("Cannot allocate memory", "too many open files");
- ``ambiente_inacessivel``: colapso de transporte
  (``environment_unreachable*``);
- ``capacidade_nao_determinada``: admissão sem nenhuma evidência específica
  (âncora não apareceu — caso real v7 conc20) — NÃO é saturação comprovada.

Estes testes DEVEM FALHAR antes da correção e PASSAR depois dela.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "gateway"))

from dakota_gateway.benchmark.comparison import (  # noqa: E402
    build_comparison,
    build_decision,
)
from dakota_gateway.benchmark.models import (  # noqa: E402
    EnvironmentRunResult,
    ExperimentResult,
    OperationSample,
)


def _amostra(env: str, idx: int) -> OperationSample:
    ns0 = 1_000_000_000 + idx * 20_000_000
    return OperationSample(
        experiment_id="exp-stop", environment_id=env, iteration=1,
        concurrency=1, virtual_user_id="vu-1", journey_id="j",
        step_id=f"ev-{idx}", phase="MEASUREMENT", started_ns=ns0,
        finished_ns=ns0 + 10_000_000, latency_ms=10.0,
        success=True, timeout=False, functional_divergence=False,
        error_code=None, screen_sig_checked=True,
        expected_screen_sig="sha256:x", observed_screen_sig="sha256:x")


def _resultado_com_stop(condition: str, erro_aborted: str,
                        *, concurrency: int = 20) -> ExperimentResult:
    """1 nível COMPLETED + nível ABORTED com stop_reason (padrão v7)."""
    ok = EnvironmentRunResult(
        environment_id="aix-power", iteration=1, concurrency=1,
        status="COMPLETED", samples=[_amostra("aix-power", i)
                                     for i in range(20)])
    abortada = EnvironmentRunResult(
        environment_id="aix-power", iteration=1, concurrency=concurrency,
        status="ABORTED", samples=[], error_reason=erro_aborted)
    return ExperimentResult(
        contract_sha256="c" * 64, status="COMPLETED", runs=[ok, abortada],
        stop_reason={"iteration": 1, "concurrency": concurrency,
                     "condition": condition, "value": float(concurrency),
                     "limit": float(concurrency)})


class TestClassificacaoParada(unittest.TestCase):
    """A classificação usa a evidência, nunca o rótulo genérico."""

    def test_stop_condition_medida_e_saturacao_comprovada(self) -> None:
        resultado = _resultado_com_stop("host_cpu_pct", "")
        comparison = build_comparison(resultado)
        classif = comparison["stop_classification"]
        self.assertEqual("saturacao_comprovada", classif["category"])

    def test_admissao_com_user_limit_exceeded_e_limite_licenca(self) -> None:
        resultado = _resultado_com_stop(
            "session_admission_limit",
            "start_session_failed: User limit exceeded")
        comparison = build_comparison(resultado)
        classif = comparison["stop_classification"]
        self.assertEqual("limite_licenca", classif["category"])

    def test_admissao_com_ancora_ausente_nao_e_saturacao(self) -> None:
        """Caso real v7 conc20: 'anchor Digite a sua opcao não apareceu' →
        capacidade NÃO determinada — jamais saturação comprovada."""
        resultado = _resultado_com_stop(
            "session_admission_limit",
            "start_session_failed: passo 0: anchor 'Digite a sua opcao:' "
            "não apareceu em 30s")
        comparison = build_comparison(resultado)
        classif = comparison["stop_classification"]
        self.assertEqual("capacidade_nao_determinada", classif["category"])
        self.assertNotEqual("saturacao_comprovada", classif["category"])

    def test_admissao_permission_denied_e_falha_login(self) -> None:
        resultado = _resultado_com_stop(
            "session_admission_limit",
            "start_session_failed: Permission denied (publickey)")
        comparison = build_comparison(resultado)
        self.assertEqual("falha_login",
                         comparison["stop_classification"]["category"])

    def test_admissao_comando_nao_encontrado_e_falha_launcher(self) -> None:
        resultado = _resultado_com_stop(
            "session_admission_limit",
            "start_session_failed: ksh: dbrt: not found")
        comparison = build_comparison(resultado)
        self.assertEqual("falha_launcher",
                         comparison["stop_classification"]["category"])

    def test_recurso_local_esgotado_e_limite_orquestrador(self) -> None:
        resultado = _resultado_com_stop(
            "session_admission_limit",
            "start_session_failed: fork: Cannot allocate memory")
        comparison = build_comparison(resultado)
        self.assertEqual("limite_orquestrador",
                         comparison["stop_classification"]["category"])

    def test_colapso_de_transporte_e_ambiente_inacessivel(self) -> None:
        resultado = ExperimentResult(
            contract_sha256="c" * 64, status="FAILED",
            reason="environment_unreachable_mid_run",
            runs=[EnvironmentRunResult(
                environment_id="aix-power", iteration=1, concurrency=1,
                status="FAILED",
                error_reason="start_session_failed: Network is unreachable")])
        comparison = build_comparison(resultado)
        self.assertEqual(
            "ambiente_inacessivel",
            comparison["stop_classification"]["category"])

    def test_classificacao_vai_para_a_razao_da_decisao(self) -> None:
        """O WARN da parada cita a categoria (licença), não 'saturação'."""
        resultado = _resultado_com_stop(
            "session_admission_limit",
            "start_session_failed: User limit exceeded")
        comparison = build_comparison(resultado)
        decision = build_decision(resultado, comparison)
        self.assertEqual("WARN", decision.verdict)
        razoes = " ".join(decision.reasons)
        self.assertIn("licen", razoes)

    def test_sem_parada_sem_classificacao(self) -> None:
        resultado = ExperimentResult(
            contract_sha256="c" * 64, status="COMPLETED",
            runs=[EnvironmentRunResult(
                environment_id="aix-power", iteration=1, concurrency=1,
                status="COMPLETED",
                samples=[_amostra("aix-power", i) for i in range(5)])])
        comparison = build_comparison(resultado)
        self.assertIsNone(comparison["stop_classification"])


if __name__ == "__main__":
    unittest.main()
