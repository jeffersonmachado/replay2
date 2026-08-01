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
    # Skew de segmentação por type-ahead (captura gravou o input seguinte
    # antes da resposta anterior terminar de desenhar): o passo divergiu do
    # segmento cortado da captura, mas o passo SEGUINTE convergiu — as duas
    # engines acumularam o mesmo byte stream completo. A evidência fica
    # marcada aqui; divergência que persiste NUNCA é rebaixada.
    segmentation_skew: bool = False
    # Origem da verificação de tela: "text" (checkpoint quiet point com
    # screen_raw — verdade de terreno; divergência de texto é REAL e nunca
    # é rebaixada por skew) ou "sig" (assinatura reconstruída do byte
    # stream — suscetível a skew de segmentação). Vazio = sem verificação.
    screen_check_kind: str = ""
    # Base da verificação de texto: "shared" (tela da captura compartilhada
    # pelos ambientes — prova de paridade com o mesmo dado) ou "env"
    # (baseline PRÓPRIO do ambiente, gerado de passada real quando os
    # datasets divergem — a decisão NUNCA deixa equivalência por baseline
    # próprio virar PASS: dados diferentes é INCONCLUSIVE/WARN, §20).
    screen_check_basis: str = ""
    # Lag transitório de checkpoint (pausas longas de atualização do ERP,
    # ex.: "aguarde. atualizando dados..." com silêncio > stable_ms): o
    # checkpoint de TEXTO divergiu, mas o PRÓXIMO CHECKPOINT reconvergiu —
    # evidência de atraso de apresentação, não de divergência funcional.
    # A marca fica registrada; divergência sem reconvergência NUNCA é
    # rebaixada.
    checkpoint_lag: bool = False

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
    # Parada da escada por stop_condition (§17: saturação/limite encontrado
    # é achado de CAPACIDADE, não falha do experimento): dict com
    # iteration/concurrency/condition/value/limit. A decisão limita o
    # veredito a WARN quando presente (a validação completa não ocorreu).
    stop_reason: dict | None = None
