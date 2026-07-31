"""Benchmark real AIX vs Linux — pacote oficial (contrato dev/benchmark-api-contract.md).

Replay real, pareado, com contrato imutável e evidência auditável. Este
``__init__`` apenas re-exporta a API pública dos módulos do pacote:

- ``contract`` — ExperimentContract imutável, manifesto canônico, paridade;
- ``environments`` — modelos de ambiente AIX/Linux (§7);
- ``stats`` — percentis com interpolação linear, stats populacionais, pooling;
- ``models`` — OperationSample / EnvironmentRunResult / ExperimentResult (§9);
- ``adapters`` — Protocol EnvironmentExecutionAdapter + SSHReplayAdapter real;
- ``executor`` — fases PREFLIGHT→…→COMPLETED, warmup excluído do agregado;
- ``degradation`` — escada de saturação, limites seguro/máximo (§18);
- ``normalize`` — eficiência por vCPU/core/entitled/GB (§19);
- ``decision`` — portas funcional → desempenho, vereditos (§20);
- ``persistence`` — tabelas benchmark_* no SQLite (§17);
- ``comparison`` — agregação por pooling, comparação e decisão compostas;
- ``report`` — artefatos do experimento e evidence-manifest (§24).

Os nomes ``BenchmarkOrchestrator``/``BenchmarkConfig`` (e demais dataclasses
legadas) são re-exportados de ``dakota_gateway.benchmark_legacy`` SOMENTE
para compatibilidade do endpoint antigo ``/api/synthetic/benchmark`` — uma
rotina legada que não executa nada real (ver docstring daquele módulo). O
caminho oficial de benchmark NÃO passa por eles.
"""
from __future__ import annotations

from .adapters import EnvironmentExecutionAdapter, SSHReplayAdapter
from .comparison import build_capacity, build_comparison, build_decision
from .contract import (
    ContractViolation,
    ExperimentContract,
    StopConditions,
    ThinkTimeProfile,
    create_contract,
    load_contract,
    validate_environment_parity,
)
from .decision import VERDICTS, Decision, decide
from .degradation import (
    DegradationCriteria,
    DegradationReport,
    analyze_ladder,
)
from .environments import CpuModel, EnvironmentModel
from .executor import PHASES, BenchmarkExecutor
from .models import EnvironmentRunResult, ExperimentResult, OperationSample
from .normalize import NORMALIZATION_INCONCLUSIVE, normalize
from .persistence import (
    ensure_benchmark_tables,
    get_experiment,
    list_experiments,
    list_runs,
    save_app_samples,
    save_comparison,
    save_experiment,
    save_host_samples,
    save_run,
    update_experiment_status,
)
from .report import write_experiment_artifacts
from .stats import Stats, aggregate_samples, compute_stats, percentile

# ── Compatibilidade legada (endpoint antigo /api/synthetic/benchmark) ─────
# Re-export intencional: o módulo legado vive FORA deste pacote porque a
# varredura estática dos testes proíbe rotinas não reais no pacote oficial.
from ..benchmark_legacy import (  # noqa: E402
    BenchmarkComparison,
    BenchmarkConfig,
    BenchmarkOrchestrator,
    BenchmarkResult,
    EnvironmentMetrics,
)

__all__ = [
    # contrato
    "ContractViolation", "ExperimentContract", "StopConditions",
    "ThinkTimeProfile", "create_contract", "load_contract",
    "validate_environment_parity",
    # ambientes
    "CpuModel", "EnvironmentModel",
    # estatística
    "Stats", "aggregate_samples", "compute_stats", "percentile",
    # modelos
    "EnvironmentRunResult", "ExperimentResult", "OperationSample",
    # adaptadores
    "EnvironmentExecutionAdapter", "SSHReplayAdapter",
    # executor
    "PHASES", "BenchmarkExecutor",
    # degradação / normalização / decisão
    "DegradationCriteria", "DegradationReport", "analyze_ladder",
    "NORMALIZATION_INCONCLUSIVE", "normalize",
    "VERDICTS", "Decision", "decide",
    # persistência
    "ensure_benchmark_tables", "get_experiment", "list_experiments",
    "list_runs", "save_app_samples", "save_comparison", "save_experiment",
    "save_host_samples", "save_run", "update_experiment_status",
    # comparação / relatório
    "build_capacity", "build_comparison", "build_decision",
    "write_experiment_artifacts",
    # legado (compatibilidade — ver benchmark_legacy.py)
    "BenchmarkComparison", "BenchmarkConfig", "BenchmarkOrchestrator",
    "BenchmarkResult", "EnvironmentMetrics",
]
