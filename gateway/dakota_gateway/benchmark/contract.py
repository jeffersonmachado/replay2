"""Contrato de experimento do benchmark real (contrato §5.4/§6).

O ``ExperimentContract`` é a fonte única e IMUTÁVEL da configuração de um
experimento: jornada, dataset, versão da aplicação, seed, geometria, escada
de concorrência, durações das fases, perfil de think time, condições de parada
e ambientes comparados. Depois de gravado (``write_manifest``), qualquer
comparação posterior usa ``load_contract`` e confere o ``sha256`` — o
``canonical_json`` é determinístico (``sort_keys`` + separadores compactos),
portanto o hash detecta qualquer alteração de configuração.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = "1.0"

#: Eixos de paridade exigidos entre ambientes (§5.4): a comparação só é
#: válida se TODOS os ambientes executarem exatamente a mesma carga.
_PARITY_AXES = (
    "journey_set_sha256",
    "dataset_sha256",
    "seed",
    "concurrency_levels",
    "measurement_seconds",
)


class ContractViolation(Exception):
    """Configuração de experimento inválida ou não comparável entre ambientes."""


@dataclass(frozen=True)
class ThinkTimeProfile:
    """Perfil de think time entre operações (determinístico ou desligado)."""

    type: str  # "deterministic" | "none"
    sha256: str
    params: dict = field(default_factory=dict)


@dataclass(frozen=True)
class StopConditions:
    """Condições que interrompem a escada de carga (§11/§18)."""

    error_rate_pct: float = 5.0
    p99_limit_ms: float = 5000.0
    host_cpu_pct: float = 95.0
    swap_growth_mb: float = 512.0
    #: Nº de amostras CONSECUTIVAS acima de ``host_cpu_pct`` (na série de uma
    #: run) para declarar saturação — §17: "CPU PERMANECER saturada". Uma
    #: amostra isolada (pico transitório ou carga externa ao host) não
    #: interrompe a escada. Caso real: execução oficial v1 parou em conc5 por
    #: UMA amostra a 99% no AIX de produção.
    host_cpu_sustained_samples: int = 3


@dataclass(frozen=True)
class ExperimentContract:
    """Contrato imutável do experimento (§6)."""

    schema_version: str
    experiment_id: str
    created_at: str  # ISO8601 UTC
    journey_set_sha256: str
    dataset_sha256: str
    application_version_sha256: str
    seed: int
    terminal_geometry: str  # ex.: "80x24"
    concurrency_levels: tuple[int, ...]
    warmup_seconds: int
    measurement_seconds: int
    cooldown_seconds: int
    iterations: int
    think_time_profile: ThinkTimeProfile
    stop_conditions: StopConditions
    environments: tuple[str, ...]  # environment_ids

    def to_manifest_dict(self) -> dict:
        """Manifesto completo do experimento (todos os campos do §6)."""
        return {
            "schema_version": self.schema_version,
            "experiment_id": self.experiment_id,
            "created_at": self.created_at,
            "journey_set_sha256": self.journey_set_sha256,
            "dataset_sha256": self.dataset_sha256,
            "application_version_sha256": self.application_version_sha256,
            "seed": self.seed,
            "terminal_geometry": self.terminal_geometry,
            "concurrency_levels": list(self.concurrency_levels),
            "warmup_seconds": self.warmup_seconds,
            "measurement_seconds": self.measurement_seconds,
            "cooldown_seconds": self.cooldown_seconds,
            "iterations": self.iterations,
            "think_time_profile": {
                "type": self.think_time_profile.type,
                "sha256": self.think_time_profile.sha256,
                "params": dict(self.think_time_profile.params),
            },
            "stop_conditions": {
                "error_rate_pct": self.stop_conditions.error_rate_pct,
                "p99_limit_ms": self.stop_conditions.p99_limit_ms,
                "host_cpu_pct": self.stop_conditions.host_cpu_pct,
                "swap_growth_mb": self.stop_conditions.swap_growth_mb,
                "host_cpu_sustained_samples":
                    self.stop_conditions.host_cpu_sustained_samples,
            },
            "environments": list(self.environments),
        }

    def canonical_json(self) -> str:
        """JSON canônico determinístico (sort_keys + separadores compactos)."""
        return json.dumps(self.to_manifest_dict(), sort_keys=True,
                          separators=(",", ":"))

    def sha256(self) -> str:
        """SHA-256 hex do ``canonical_json`` — identidade do contrato."""
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    def write_manifest(self, experiment_dir: Path) -> Path:
        """Grava ``experiment-manifest.json`` no diretório do experimento.

        Depois de gravado, o contrato é considerado imutável: o dataclass é
        frozen e o ``sha256`` do manifesto identifica a configuração exata
        executada — qualquer reexecução com configuração diferente gera outro
        hash e outro experimento.
        """
        experiment_dir = Path(experiment_dir)
        experiment_dir.mkdir(parents=True, exist_ok=True)
        caminho = experiment_dir / "experiment-manifest.json"
        caminho.write_text(self.canonical_json(), encoding="utf-8")
        return caminho


def create_contract(**kwargs) -> ExperimentContract:
    """Cria o contrato preenchendo ``schema_version`` ("1.0") e ``created_at``.

    ``created_at`` pode ser informado explicitamente (reprodutibilidade);
    caso contrário usa o instante UTC atual em ISO8601.
    """
    kwargs.setdefault("schema_version", SCHEMA_VERSION)
    if not kwargs.get("created_at"):
        kwargs["created_at"] = (
            datetime.now(timezone.utc).isoformat(timespec="seconds")
            .replace("+00:00", "Z")
        )
    if "think_time_profile" in kwargs and isinstance(kwargs["think_time_profile"], dict):
        kwargs["think_time_profile"] = ThinkTimeProfile(**kwargs["think_time_profile"])
    if "stop_conditions" in kwargs and isinstance(kwargs["stop_conditions"], dict):
        kwargs["stop_conditions"] = StopConditions(**kwargs["stop_conditions"])
    if "concurrency_levels" in kwargs:
        kwargs["concurrency_levels"] = tuple(int(n) for n in kwargs["concurrency_levels"])
    if "environments" in kwargs:
        kwargs["environments"] = tuple(str(e) for e in kwargs["environments"])
    return ExperimentContract(**kwargs)


def load_contract(manifest_path: Path) -> ExperimentContract:
    """Recarrega o contrato de um ``experiment-manifest.json`` (round-trip)."""
    dados = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    return create_contract(**dados)


def validate_environment_parity(env_configs: list[dict]) -> None:
    """§5.4 — bloqueia ANTES da execução se os ambientes não forem comparáveis.

    ``env_configs`` é uma lista de dicts (um por ambiente) com as chaves
    ``journey_set_sha256``, ``dataset_sha256``, ``seed``,
    ``concurrency_levels`` e ``measurement_seconds``. Qualquer divergência
    entre ambientes — inclusive a ORDEM dos níveis de concorrência, que define
    a escada executada — levanta ``ContractViolation``.
    """
    if len(env_configs) < 2:
        return
    base = env_configs[0]
    base_env = base.get("environment_id", "env[0]")
    divergencias: list[str] = []
    for eixo in _PARITY_AXES:
        referencia = base.get(eixo)
        for outro in env_configs[1:]:
            valor = outro.get(eixo)
            if eixo == "concurrency_levels":
                # a ordem dos níveis importa: é a escada de carga executada
                divergiu = list(valor or []) != list(referencia or [])
            else:
                divergiu = valor != referencia
            if divergiu:
                divergencias.append(
                    f"{eixo}: {base_env}={referencia!r} != "
                    f"{outro.get('environment_id', '?')}={valor!r}"
                )
    if divergencias:
        raise ContractViolation(
            "paridade de contrato violada entre ambientes: "
            + "; ".join(divergencias)
        )
