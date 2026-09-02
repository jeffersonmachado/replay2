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
    #: Sonda de recuperação pós-carga (FASE 3): > 0 habilita a medição REAL
    #: de recuperação (janela em segundos após o fim da carga). 0 = desligada
    #: (default — compatível com manifestos antigos).
    recovery_probe_seconds: int = 0
    #: Justificativas registradas quando dois hashes de proveniência são
    #: iguais (ex.: {"journey_set_sha256==dataset_sha256": "mesma captura
    #: de origem"}) — exigidas na criação quando hashes coincidem, pois a
    #: igualdade só é legítima com confirmação explícita e auditável.
    hash_justifications: dict = field(default_factory=dict)

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
            "recovery_probe_seconds": self.recovery_probe_seconds,
            "hash_justifications": dict(self.hash_justifications),
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

    Proveniência (FASE 4): hashes placeholder (vazio NÃO é rejeitado aqui
    por compatibilidade de criação via UI, mas vira problema na decisão) são
    rejeitados na criação; hashes iguais entre si exigem justificativa
    registrada (``hash_justifications``). ``_validar_proveniencia=False`` é
    reservado ao ``load_contract`` (manifestos legados carregam para
    auditoria — a decisão ainda barra os problemas).
    """
    validar = bool(kwargs.pop("_validar_proveniencia", True))
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
    if "hash_justifications" in kwargs and isinstance(kwargs["hash_justifications"], dict):
        kwargs["hash_justifications"] = dict(kwargs["hash_justifications"])
    contrato = ExperimentContract(**kwargs)
    if validar:
        problemas = validate_provenance_hashes(
            contrato, exigir_presenca=False)
        duros = [p for p in problemas if p["severidade"] == "erro"]
        if duros:
            raise ContractViolation(
                "hashes de proveniência inválidos: "
                + "; ".join(p["problema"] for p in duros))
    return contrato


def load_contract(manifest_path: Path) -> ExperimentContract:
    """Recarrega o contrato de um ``experiment-manifest.json`` (round-trip).

    Manifestos legados podem conter hashes placeholder (ex.: o v7 usou
    ``"def1277b"`` + zeros no think time): o carregamento NÃO valida a
    proveniência para permitir auditoria/recálculo — quem barra é a decisão
    (``build_comparison``/``decide`` com o contrato em mãos).
    """
    dados = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    return create_contract(_validar_proveniencia=False, **dados)


#: Campos de proveniência cujos hashes identificam os artefatos comparados.
_CAMPOS_HASH = (
    "journey_set_sha256",
    "dataset_sha256",
    "application_version_sha256",
)


def _hash_do_campo(contrato: ExperimentContract, campo: str) -> str:
    """Lê o hash de um campo de proveniência (inclui o do think time)."""
    if campo == "think_time_profile.sha256":
        return contrato.think_time_profile.sha256 or ""
    return getattr(contrato, campo, "") or ""


def validate_provenance_hashes(
    contrato: ExperimentContract, *, exigir_presenca: bool = True
) -> list[dict]:
    """Valida os hashes de proveniência do contrato (FASE 4).

    Devolve uma lista de problemas ``{"campo", "problema", "severidade"}``
    (``severidade`` = ``"erro"`` | ``"aviso"``). Regras:

    - vazio/ausente: tolerado com ``exigir_presenca=False`` (criação via UI
      ainda não conhece todos os artefatos); com ``True`` vira erro — a
      decisão não pode comparar o que não está identificado;
    - formato que não é sha256 hex de 64 chars → erro;
    - placeholder evidente (literal "unknown" ou sequência de ≥ 32 zeros
      consecutivos, padrão dos manifestos gerados à mão como o v7) → erro;
    - dois hashes VÁLIDOS iguais entre si sem justificativa registrada em
      ``hash_justifications`` (chave ``"campoA==campoB"``) → erro: a
      igualdade só é legítima com confirmação explícita e auditável.
    """
    campos = list(_CAMPOS_HASH) + ["think_time_profile.sha256"]
    problemas: list[dict] = []
    validos: dict[str, str] = {}
    for campo in campos:
        valor = _hash_do_campo(contrato, campo).strip()
        if not valor:
            if exigir_presenca:
                problemas.append({
                    "campo": campo,
                    "problema": "hash de proveniência ausente/vazio",
                    "severidade": "erro",
                })
            continue
        if valor.lower() == "unknown":
            problemas.append({
                "campo": campo,
                "problema": "hash de proveniência inválido ('unknown')",
                "severidade": "erro",
            })
            continue
        if len(valor) != 64 or any(
            c not in "0123456789abcdefABCDEF" for c in valor
        ):
            problemas.append({
                "campo": campo,
                "problema": "formato inválido (não é sha256 hex de 64 "
                            "caracteres)",
                "severidade": "erro",
            })
            continue
        if "0" * 32 in valor:
            problemas.append({
                "campo": campo,
                "problema": "hash de proveniência fictício (sequência de "
                            "≥ 32 zeros consecutivos)",
                "severidade": "erro",
            })
            continue
        validos[campo] = valor.lower()
    # igualdade entre hashes válidos exige justificativa registrada
    itens = sorted(validos.items())
    for i, (campo_a, hash_a) in enumerate(itens):
        for campo_b, hash_b in itens[i + 1:]:
            if hash_a != hash_b:
                continue
            chave = f"{campo_a}=={campo_b}"
            if chave not in (contrato.hash_justifications or {}):
                problemas.append({
                    "campo": chave,
                    "problema": "hashes idênticos sem justificativa "
                                "registrada no contrato",
                    "severidade": "erro",
                })
    return problemas


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
