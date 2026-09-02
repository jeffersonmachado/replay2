"""Cobertura de coletores do benchmark real (FASE 3).

Um JSON parseável NÃO é prova de coleta válida. Este módulo separa, por
ambiente e por coletor:

- coletor disponível / parcialmente disponível / indisponível;
- amostra válida (linha JSON parseável que não seja o marcador de
  indisponibilidade ``{"available": false, ...}``);
- cobertura por GRUPO de métricas essenciais (cpu, memória, paginação,
  disco, rede, run queue) — um grupo só está "coberto" se alguma amostra
  válida trouxer VALOR numérico real em algum campo do grupo (rede também
  pode ser coberta pela medição de contadores na janela da run —
  ``net_window`` — já que o sampler de host não instrumenta rede);
- banco/Recital/ISAM: ``collector_not_supported`` = NÃO APLICÁVEL (arquivos
  ISAM não têm coletor de banco) — nunca conta como falta.

O gargalo dominante só pode ser DECLARADO quando todos os grupos
essenciais estão cobertos; faltando grupo essencial, o gargalo é
``unknown`` e a decisão é INCONCLUSIVE (``build_decision``) — nunca
gargalo inventado a partir de cobertura parcial.
"""
from __future__ import annotations

import json

#: Grupos de métricas essenciais para declarar gargalo dominante, com os
#: campos de amostra que comprovam cada grupo (basta UM valor numérico
#: não-nulo por grupo). ``run_queue`` aceita ``load1`` (load average é a
#: fila de runnables agregada no Linux/AIX); ``rede`` aceita os campos por
#: amostra OU a medição de contadores na janela da run (``net_window``).
GRUPOS_ESSENCIAIS: dict[str, tuple[str, ...]] = {
    "cpu": ("cpu_pct", "cpu_user", "cpu_system"),
    "memoria": ("mem_pct", "mem_used_mb"),
    "paginacao": ("swap_pct",),
    "disco": ("disk_latency_ms", "iops", "disk_read_kbs", "disk_write_kbs",
              "disk_busy_pct"),
    "rede": ("net_rx_kbs", "net_tx_kbs", "net_util_pct"),
    "run_queue": ("run_queue", "load1"),
}

#: Fração mínima de amostras válidas com o grupo presente para "coberto";
#: abaixo disso (mas acima de zero) o grupo é "parcial".
_FRACAO_COBERTO = 0.5


def _amostra_valida(linha: str) -> dict | None:
    """Parseia uma linha de host-samples.jsonl; devolve None se inválida.

    Válida = JSON objeto parseável que NÃO seja o marcador de
    indisponibilidade (``available: false``).
    """
    try:
        dado = json.loads(linha)
    except ValueError:
        return None
    if not isinstance(dado, dict):
        return None
    if dado.get("available") is False:
        return None
    return dado


def ler_amostras_host_validas(host_samples_path: str) -> list[dict]:
    """Lê as amostras VÁLIDAS de host de uma run (ordem temporal)."""
    amostras: list[dict] = []
    if not host_samples_path:
        return amostras
    try:
        with open(host_samples_path, encoding="utf-8") as fh:
            for linha in fh:
                linha = linha.strip()
                if not linha:
                    continue
                dado = _amostra_valida(linha)
                if dado is not None:
                    amostras.append(dado)
    except OSError:
        pass
    return amostras


def _grupos_das_amostras(amostras: list[dict]) -> dict[str, dict]:
    """Status de cada grupo essencial a partir das amostras válidas."""
    grupos: dict[str, dict] = {}
    total = len(amostras)
    for grupo, campos in GRUPOS_ESSENCIAIS.items():
        presentes: set[str] = set()
        com_valor = 0
        for amostra in amostras:
            achou = False
            for campo in campos:
                valor = amostra.get(campo)
                if isinstance(valor, (int, float)):
                    presentes.add(campo)
                    achou = True
            if achou:
                com_valor += 1
        if not total or com_valor == 0:
            status = "ausente"
        elif com_valor / total >= _FRACAO_COBERTO:
            status = "coberto"
        else:
            status = "parcial"
        grupos[grupo] = {"status": status,
                         "campos_presentes": sorted(presentes),
                         "amostras_com_valor": com_valor}
    return grupos


def analisar_cobertura(runs: list) -> dict[str, dict]:
    """Cobertura por coletor e por ambiente (aplicação, host, banco).

    ``runs``: ``EnvironmentRunResult`` do experimento (qualquer status —
    runs ABORTED de saturação também carregam evidência de host). Retorna
    ``{env: {"application": {...}, "host": {...}, "database": {...}}}``.
    """
    por_env: dict[str, list] = {}
    for run in runs:
        por_env.setdefault(run.environment_id, []).append(run)

    saida: dict[str, dict] = {}
    for env_id, env_runs in por_env.items():
        # aplicação: amostras de MEASUREMENT das runs COMPLETED
        app_amostras = sum(len(r.samples) for r in env_runs
                           if r.status == "COMPLETED")

        # host: amostras válidas de TODAS as runs do ambiente
        amostras: list[dict] = []
        for run in env_runs:
            amostras.extend(ler_amostras_host_validas(
                getattr(run, "host_samples_path", "") or ""))
        grupos = _grupos_das_amostras(amostras)

        # rede pode ser coberta pela medição de contadores na janela da run
        rede_via = None
        if grupos["rede"]["status"] != "ausente":
            rede_via = "amostras"
        else:
            janelas = [r.net_window for r in env_runs
                       if getattr(r, "net_window", None)]
            if janelas and any(
                    any(isinstance(j.get(c), (int, float))
                        for c in ("net_rx_kbs", "net_tx_kbs",
                                  "net_rx_pkts", "net_tx_pkts"))
                    for j in janelas):
                grupos["rede"] = {
                    "status": "coberto",
                    "campos_presentes": sorted(
                        c for c in ("net_rx_kbs", "net_tx_kbs",
                                    "net_rx_pkts", "net_tx_pkts")
                        if any(isinstance(j.get(c), (int, float))
                               for j in janelas)),
                    "amostras_com_valor": len(janelas),
                }
                rede_via = "janela"

        grupos_ausentes = sorted(
            g for g, d in grupos.items() if d["status"] == "ausente")
        grupos_parciais = sorted(
            g for g, d in grupos.items() if d["status"] == "parcial")

        if not amostras:
            host_status = "indisponivel"
        elif grupos_ausentes or grupos_parciais:
            host_status = "parcialmente_disponivel"
        else:
            host_status = "disponivel"

        timestamps = [int(a["ts_ms"]) for a in amostras
                      if isinstance(a.get("ts_ms"), (int, float))]

        # banco: collector_not_supported = NÃO APLICÁVEL (arquivos
        # Recital/ISAM não têm coletor de banco) — nunca conta como falta
        db_metrics = {}
        for run in env_runs:
            if getattr(run, "database_metrics", None):
                db_metrics = run.database_metrics
        if not db_metrics:
            db_status = "nao_executado"
        elif db_metrics.get("available") is False:
            motivo = str(db_metrics.get("reason", ""))
            db_status = ("nao_aplicavel"
                         if motivo in ("collector_not_supported",
                                       "collector_not_run")
                         else "indisponivel")
        else:
            db_status = "disponivel"

        saida[env_id] = {
            "application": {
                "status": ("disponivel" if app_amostras > 0
                           else "indisponivel"),
                "amostras_measurement": app_amostras,
            },
            "host": {
                "status": host_status,
                "amostras_validas": len(amostras),
                "span_ms": ((max(timestamps) - min(timestamps))
                            if len(timestamps) >= 2 else 0),
                "grupos": grupos,
                "grupos_ausentes": grupos_ausentes,
                "grupos_parciais": grupos_parciais,
                "rede_via": rede_via,
            },
            "database": {"status": db_status,
                         "reason": str(db_metrics.get("reason", "") or "")},
        }
    return saida


def bottleneck_evidence(cobertura: dict[str, dict]) -> dict[str, dict]:
    """Evidência suficiente para DECLARAR gargalo dominante, por ambiente.

    ``ok=False`` quando algum grupo essencial está ausente/parcial ou o
    coletor de host está indisponível — o chamador força
    ``dominant_bottleneck="unknown"`` e a decisão vira INCONCLUSIVE.
    Banco não entra na conta: é exigido só "quando aplicável", e
    ``nao_aplicavel`` já é a resposta documentada do coletor.
    """
    evidencia: dict[str, dict] = {}
    for env_id, cob in cobertura.items():
        host = cob.get("host", {})
        faltantes = list(host.get("grupos_ausentes", [])) + [
            f"{g} (parcial)" for g in host.get("grupos_parciais", [])]
        if host.get("status") == "indisponivel":
            faltantes.append("host_metrics (coletor indisponível)")
        evidencia[env_id] = {
            "ok": not faltantes,
            "missing": faltantes,
            "database_status": cob.get("database", {}).get("status", ""),
        }
    return evidencia
