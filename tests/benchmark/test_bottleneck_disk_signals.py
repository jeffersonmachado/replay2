"""Detector de gargalo: sinais de disco (latência e % tm_act do coletor AIX).

O coletor AIX (host_metrics.AixCollector via iostat) passou a emitir
``disk_busy_pct`` (maior % tm_act do intervalo) e ``disk_latency_ms``
(avgserv ponderado do iostat -D). O detector deve reconhecer disk_io por
qualquer um dos dois sinais — e continuar "unknown" quando os campos estão
ausentes/None (campos não medidos NUNCA viram zero que dispare regra).
"""
from __future__ import annotations

from dakota_gateway.benchmark.degradation import _gargalo_dominante


def test_disco_saturado_por_tm_act_e_disk_io():
    host = [{"cpu_pct": 30.0, "mem_pct": 40.0, "disk_busy_pct": 94.5}]
    assert _gargalo_dominante(host) == "disk_io"


def test_disco_por_latencia_alta_e_disk_io():
    host = [{"cpu_pct": 30.0, "disk_latency_ms": 55.0}]
    assert _gargalo_dominante(host) == "disk_io"


def test_campos_ausentes_nao_disparam_disk_io():
    """Coletor antigo (sem colunas de IO): ausência não pode virar gargalo."""
    host = [{"cpu_pct": 31.2, "mem_pct": 50.0}]
    assert _gargalo_dominante(host) == "unknown"


def test_campos_none_nao_disparam_disk_io():
    host = [{"cpu_pct": 31.2, "disk_busy_pct": None, "disk_latency_ms": None}]
    assert _gargalo_dominante(host) == "unknown"


def test_cpu_saturada_tem_precedencia_sobre_disco():
    host = [{"cpu_pct": 98.0, "disk_busy_pct": 95.0}]
    assert _gargalo_dominante(host) == "cpu"


def test_disco_ocupado_abaixo_do_limite_e_unknown():
    host = [{"cpu_pct": 30.0, "disk_busy_pct": 89.9, "disk_latency_ms": 49.9}]
    assert _gargalo_dominante(host) == "unknown"
