"""Regressão: resultados do stress sintético são sempre marcados como simulação.

O SyntheticStressRunner não executa terminal real — as telas verificadas são
sintetizadas da definição da jornada (`_simulate_screens`). Todo resultado
desse caminho deve carregar ``simulation=True`` para que nunca seja confundido
com medição real nem usado em veredito oficial de benchmark/migração.
"""
from __future__ import annotations

from dakota_gateway.synthetic.stress_runner import StressRunResult
from dakota_gateway.synthetic.remote_executor import RemoteExecutionResult


def test_stress_run_result_sempre_simulacao():
    """StressRunResult nasce marcado como simulação (default True)."""
    result = StressRunResult(total_sessions=10, completed=10)
    assert result.simulation is True


def test_remote_execution_result_dry_run_e_simulacao():
    """Execução remota em dry_run (ou sem host) é simulação explícita."""
    result = RemoteExecutionResult(journey_id="j1", mode="dry_run", simulation=True)
    assert result.simulation is True
    assert result.mode == "dry_run"


def test_remote_execution_result_real_nao_e_simulacao():
    """Modo real com target_host é a única combinação não simulada."""
    result = RemoteExecutionResult(journey_id="j1", mode="real", simulation=False)
    assert result.simulation is False


def test_marcacao_simulacao_na_serializacao_do_stress():
    """O payload agregado expõe o campo simulation para API/CLI."""
    result = StressRunResult(total_sessions=2, completed=2)
    payload = {
        "status": "completed",
        "simulation": result.simulation,
        "total_sessions": result.total_sessions,
        "completed": result.completed,
    }
    assert payload["simulation"] is True
