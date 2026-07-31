"""Normalização de throughput por capacidade (contrato §5.10/§19).

Compara a EFICIÊNCIA dos ambientes normalizando o TPS medido pela capacidade
disponível (vCPU, core físico, entitled capacity, CPU consumida, GB de RAM).
Regras rígidas:

- campo aplicável AUSENTE ou ZERO (ex.: AIX com ``entitled_capacity=0`` ou
  ``memory_mb=0``) → métrica ``None`` + ``NORMALIZATION_INCONCLUSIVE`` no
  ambiente e no resultado global — NUNCA uma divisão por zero virando PASS;
- ``tps_per_entitled_capacity`` só é aplicável a AIX: em Linux fica ``None``
  SEM marcar inconclusão;
- ``tps_per_cpu_consumed`` e ``cost_per_1k_transactions`` dependem de dados de
  consumo/custo informados em ``env_results``; sem o dado, ficam ``None`` sem
  marcar inconclusão (o dado simplesmente não foi coletado);
- as fórmulas e os dados brutos sempre acompanham o resultado.
"""
from __future__ import annotations

from .environments import EnvironmentModel

NORMALIZATION_INCONCLUSIVE = "NORMALIZATION_INCONCLUSIVE"
NORMALIZATION_OK = "OK"

#: Fórmulas exibidas no relatório (transparência total do cálculo).
FORMULAS = {
    "tps_per_vcpu": "tps / virtual_processors",
    "tps_per_physical_core": "tps / physical_processors",
    "tps_per_entitled_capacity": "tps / entitled_capacity (aplicável só a AIX)",
    "tps_per_cpu_consumed": "tps / cpu_consumed (cpus efetivamente consumidas)",
    "tps_per_gb": "tps / (memory_mb / 1024)",
    "cost_per_1k_transactions": "cost_per_hour / (tps * 3.6) "
                                "(custo/hora ÷ transações/hora × 1000)",
}


def _dividir(numerador: float | None, denominador: float | None) -> float | None:
    """Divisão segura: denominador ausente/zero → None (nunca exceção)."""
    if numerador is None or not denominador:
        return None
    return numerador / denominador


def _normalizar_ambiente(tps: float | None, modelo: EnvironmentModel,
                         extras: dict) -> dict:
    """Normaliza o TPS de um ambiente pela capacidade do modelo (§19)."""
    faltantes: list[str] = []

    tps_per_vcpu = _dividir(tps, modelo.cpu.virtual_processors)
    if tps_per_vcpu is None:
        faltantes.append("virtual_processors")

    tps_per_physical = _dividir(tps, modelo.cpu.physical_processors)
    if tps_per_physical is None:
        faltantes.append("physical_processors")

    # entitled capacity: aplicável APENAS a AIX (Linux → None sem inconclusão)
    if modelo.platform.upper() == "AIX":
        tps_per_entitled = _dividir(tps, modelo.cpu.entitled_capacity)
        if tps_per_entitled is None:
            faltantes.append("entitled_capacity")
    else:
        tps_per_entitled = None

    # CPU consumida: só se o dado foi coletado (ausência não é inconclusão)
    cpu_consumed = extras.get("cpu_consumed")
    tps_per_cpu_consumed = _dividir(tps, cpu_consumed)

    mem_gb = (modelo.memory_mb / 1024.0) if modelo.memory_mb else 0.0
    tps_per_gb = _dividir(tps, mem_gb)
    if tps_per_gb is None:
        faltantes.append("memory_mb")

    # Custo por mil transações: só quando há custo informado
    custo_hora = extras.get("cost_per_hour")
    if tps and custo_hora:
        cost_per_1k = custo_hora / (tps * 3.6)
    else:
        cost_per_1k = None

    status = NORMALIZATION_INCONCLUSIVE if faltantes else NORMALIZATION_OK
    return {
        "environment_id": modelo.environment_id,
        "tps": tps,
        "tps_per_vcpu": tps_per_vcpu,
        "tps_per_physical_core": tps_per_physical,
        "tps_per_entitled_capacity": tps_per_entitled,
        "tps_per_cpu_consumed": tps_per_cpu_consumed,
        "tps_per_gb": tps_per_gb,
        "cost_per_1k_transactions": cost_per_1k,
        "raw": {
            "virtual_processors": modelo.cpu.virtual_processors,
            "physical_processors": modelo.cpu.physical_processors,
            "entitled_capacity": modelo.cpu.entitled_capacity,
            "memory_mb": modelo.memory_mb,
            "cpu_consumed": cpu_consumed,
            "cost_per_hour": custo_hora,
        },
        "missing_fields": faltantes,
        "status": status,
    }


def normalize(env_results: dict[str, dict],
              env_models: dict[str, EnvironmentModel]) -> dict:
    """Normaliza o throughput de todos os ambientes (§5.10/§19).

    ``env_results``: ``{"<env_id>": {"tps": x, "cpu_consumed"?: y,
    "cost_per_hour"?: z}}`` — TPS medido (e dados de consumo/custo quando
    coletados). Retorna ``{"per_environment": {...}, "formulas": {...},
    "status": str}``; o status global é ``NORMALIZATION_INCONCLUSIVE`` se
    QUALQUER ambiente tiver campo aplicável ausente/zero.
    """
    por_ambiente: dict[str, dict] = {}
    status_global = NORMALIZATION_OK
    for env_id, resultado in env_results.items():
        modelo = env_models.get(env_id)
        if modelo is None:
            por_ambiente[env_id] = {
                "environment_id": env_id,
                "status": NORMALIZATION_INCONCLUSIVE,
                "missing_fields": ["environment_model"],
            }
            status_global = NORMALIZATION_INCONCLUSIVE
            continue
        entrada = _normalizar_ambiente(resultado.get("tps"), modelo, resultado)
        por_ambiente[env_id] = entrada
        if entrada["status"] == NORMALIZATION_INCONCLUSIVE:
            status_global = NORMALIZATION_INCONCLUSIVE
    return {
        "per_environment": por_ambiente,
        "formulas": dict(FORMULAS),
        "status": status_global,
    }
