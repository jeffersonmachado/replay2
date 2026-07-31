"""Modelos de dados do benchmark real (contrato §9).

``OperationSample`` é uma linha de ``application-samples.jsonl``: UMA operação
real medida (envio de input + resposta do ambiente), cronometrada com
``time.monotonic_ns`` pelo adaptador de execução.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class OperationSample:
    """Uma operação real medida em um ambiente (§9)."""

    experiment_id: str
    environment_id: str
    iteration: int
    concurrency: int
    virtual_user_id: str
    journey_id: str
    step_id: str
    phase: str  # "WARMUP" | "MEASUREMENT" | "COOLDOWN"
    started_ns: int
    finished_ns: int
    latency_ms: float
    success: bool
    timeout: bool
    functional_divergence: bool
    error_code: str | None
    # Validação funcional (porta 1): preenchidos quando o passo tinha
    # assinatura de tela esperada e a engine canônica conseguiu calcular a
    # observada — ``screen_sig_checked`` é a evidência de que a comparação
    # de fato aconteceu (sem ela, equivalência não pode ser "comprovada").
    screen_sig_checked: bool = False
    expected_screen_sig: str = ""
    observed_screen_sig: str = ""

    def to_jsonl(self) -> str:
        """Serializa como uma linha JSON (application-samples.jsonl)."""
        return json.dumps(self.__dict__, sort_keys=True, ensure_ascii=False)


@dataclass
class EnvironmentRunResult:
    """Resultado de uma run (ambiente × iteração × nível de concorrência).

    ``samples`` contém APENAS amostras da fase MEASUREMENT (agregado oficial,
    §12); warmup/cooldown ficam nos campos próprios, para auditoria.
    """

    environment_id: str
    iteration: int
    concurrency: int
    status: str  # "COMPLETED" | "FAILED" | "ABORTED"
    samples: list[OperationSample] = field(default_factory=list)
    warmup_samples: list[OperationSample] = field(default_factory=list)
    cooldown_samples: list[OperationSample] = field(default_factory=list)
    host_samples_path: str = ""
    database_metrics: dict = field(default_factory=dict)
    error_reason: str = ""


@dataclass
class ExperimentResult:
    """Resultado consolidado do experimento (todos os ambientes)."""

    contract_sha256: str
    status: str  # "COMPLETED" | "FAILED"
    runs: list[EnvironmentRunResult] = field(default_factory=list)
    verdict: str = "INCONCLUSIVE"
    reason: str = ""
