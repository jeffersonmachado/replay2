#!/usr/bin/env python3
"""Coleta de métricas de recursos do host (CPU, memória, load, disco).

Stdlib apenas. Linux lê /proc; AIX usa `vmstat`/`lsattr`/`lsps` (best-effort:
campos indisponíveis ficam None). O HostMetricsSampler roda em thread dentro
do control plane e grava amostras na tabela `host_metrics`, alimentando o
painel /observability/resources e a comparação de estresse entre ambientes.
"""
from __future__ import annotations

import logging
import os
import platform
import re
import subprocess
import threading
import time

log = logging.getLogger(__name__)

SAMPLE_INTERVAL_S = 5.0
RETENTION_DAYS = 7
AIX_PAGE_SIZE_KB = 4

# Campos persistidos (além de ts_ms) — mesma ordem da tabela host_metrics.
FIELDS = (
    "cpu_pct",
    "load1",
    "load5",
    "load15",
    "mem_total_mb",
    "mem_used_mb",
    "mem_pct",
    "swap_pct",
    "disk_read_kbs",
    "disk_write_kbs",
)

INSERT_SQL = (
    "INSERT INTO host_metrics (ts_ms, " + ", ".join(FIELDS) + ") "
    "VALUES (?" + ", ?" * len(FIELDS) + ")"
)


def now_ms() -> int:
    return int(time.time() * 1000)


def _base_sample(ts_ms: int) -> dict:
    sample = {field: None for field in FIELDS}
    sample["ts_ms"] = ts_ms
    # os.getloadavg não existe em todas as plataformas Unix (ex.: AIX)
    getloadavg = getattr(os, "getloadavg", None)
    if getloadavg is not None:
        try:
            load1, load5, load15 = getloadavg()
            sample["load1"] = round(load1, 2)
            sample["load5"] = round(load5, 2)
            sample["load15"] = round(load15, 2)
        except OSError:
            pass
    return sample


# ── Parsers puros (testáveis, sem IO) ────────────────────────────────────────

def parse_proc_stat_cpu(text: str) -> tuple[int, int]:
    """Extrai (jiffies_busy, jiffies_total) da linha 'cpu' do /proc/stat."""
    for line in text.splitlines():
        if not line.startswith("cpu "):
            continue
        parts = [int(x) for x in line.split()[1:] if x.isdigit()]
        if len(parts) < 4:
            break
        total = sum(parts)
        idle = parts[3] + (parts[4] if len(parts) > 4 else 0)  # idle + iowait
        return total - idle, total
    return 0, 0


def parse_proc_meminfo(text: str) -> dict:
    """Extrai memória/swap do /proc/meminfo (valores em kB)."""
    info: dict[str, int] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1].isdigit():
            info[parts[0].rstrip(":")] = int(parts[1])
    total_kb = info.get("MemTotal", 0)
    avail_kb = info.get("MemAvailable", info.get("MemFree", 0))
    swap_total = info.get("SwapTotal", 0)
    swap_free = info.get("SwapFree", 0)
    used_kb = max(0, total_kb - avail_kb)
    return {
        "mem_total_mb": round(total_kb / 1024, 1) if total_kb else None,
        "mem_used_mb": round(used_kb / 1024, 1) if total_kb else None,
        "mem_pct": round(used_kb * 100 / total_kb, 1) if total_kb else None,
        "swap_pct": round((swap_total - swap_free) * 100 / swap_total, 1) if swap_total else None,
    }


# Discos-base (sem partições) considerados na soma de IO.
_DISK_RE = re.compile(r"^(sd[a-z]+|vd[a-z]+|xvd[a-z]+|hd[a-z]+|nvme\d+n\d+|mmcblk\d+)$")


def parse_proc_diskstats(text: str) -> tuple[int, int]:
    """Soma (setores_lidos, setores_escritos) dos discos-base do /proc/diskstats."""
    sectors_read = 0
    sectors_written = 0
    for line in text.splitlines():
        parts = line.split()
        # layout: major minor name rd_ios rd_merges rd_sectors ... wr_sectors ...
        if len(parts) < 10 or not _DISK_RE.match(parts[2]):
            continue
        try:
            sectors_read += int(parts[5])
            sectors_written += int(parts[9])
        except ValueError:
            continue
    return sectors_read, sectors_written


def parse_vmstat_aix(text: str) -> dict:
    """Extrai cpu%% (us+sy) e páginas livres (fre) da última linha de dados do vmstat AIX.

    Funciona localizando os índices das colunas no cabeçalho — tolerante às
    colunas extras de LPAR (lpar/ec/pc) e ao banner de resumo inicial.
    """
    header_idx: dict[str, int] = {}
    result: dict = {}
    for line in text.splitlines():
        cols = line.split()
        if not cols:
            continue
        if "us" in cols and "sy" in cols and "fre" in cols:
            # 'sy' aparece duas vezes (faults e cpu): o bloco de cpu é o final.
            # rindex para us/sy/id/wa (última ocorrência), index para fre (memória).
            header_idx = {}
            for name in ("us", "sy", "id", "wa"):
                if name in cols:
                    header_idx[name] = len(cols) - 1 - cols[::-1].index(name)
            header_idx["fre"] = cols.index("fre")
            continue
        if not header_idx or not cols[0].lstrip("-").isdigit():
            continue
        try:
            us = int(cols[header_idx["us"]])
            sy = int(cols[header_idx["sy"]])
            fre = int(cols[header_idx["fre"]])
        except (ValueError, IndexError, KeyError):
            continue
        result = {"cpu_pct": float(us + sy), "aix_free_pages": fre}
    return result


def parse_lsattr_realmem_kb(text: str) -> int:
    """Extrai a memória real (kB) de `lsattr -El sys0 -a realmem`.

    Tolera as duas ordens de saída: `realmem 98828288 ...` (AIX, atributo
    primeiro) e `98828288 realmem ...` (valor primeiro). Retorna o primeiro
    token puramente numérico da linha do atributo.
    """
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and ("realmem" in parts or parts[0].isdigit()):
            for token in parts:
                if token.isdigit():
                    return int(token)
    return 0


def parse_lsps_swap_pct(text: str) -> float | None:
    """Extrai o percentual de paging space usado de `lsps -s` (AIX)."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.endswith("%"):
            try:
                return float(stripped.split()[-1].rstrip("%"))
            except ValueError:
                continue
    return None


_UPTIME_LOAD_RE = re.compile(r"load averages?:\s*([\d.]+),\s*([\d.]+),\s*([\d.]+)")


def parse_uptime_loadavg(text: str) -> tuple[float, float, float] | None:
    """Extrai (load1, load5, load15) do `uptime` (fallback AIX sem os.getloadavg)."""
    match = _UPTIME_LOAD_RE.search(text)
    if not match:
        return None
    try:
        return (float(match.group(1)), float(match.group(2)), float(match.group(3)))
    except ValueError:
        return None


# ── Coletores por plataforma ─────────────────────────────────────────────────

class LinuxCollector:
    """Coleta via /proc. CPU e disco dependem de delta entre amostras."""

    def __init__(self, proc_root: str = "/proc"):
        self._proc = proc_root
        self._prev_cpu: tuple[int, int] | None = None
        self._prev_disk: tuple[int, int, float] | None = None

    def _read(self, name: str) -> str:
        try:
            with open(os.path.join(self._proc, name), encoding="utf-8", errors="replace") as fh:
                return fh.read()
        except OSError:
            return ""

    def sample(self, ts_ms: int) -> dict:
        sample = _base_sample(ts_ms)

        busy, total = parse_proc_stat_cpu(self._read("stat"))
        if self._prev_cpu and total > self._prev_cpu[1]:
            d_busy = busy - self._prev_cpu[0]
            d_total = total - self._prev_cpu[1]
            sample["cpu_pct"] = round(d_busy * 100 / d_total, 1)
        if total:
            self._prev_cpu = (busy, total)

        mem = parse_proc_meminfo(self._read("meminfo"))
        sample.update({k: v for k, v in mem.items() if v is not None})

        sectors_read, sectors_written = parse_proc_diskstats(self._read("diskstats"))
        now_s = time.monotonic()
        if self._prev_disk:
            p_read, p_written, p_ts = self._prev_disk
            elapsed = now_s - p_ts
            if elapsed > 0:
                # setor = 512 bytes → kB/s
                sample["disk_read_kbs"] = round((sectors_read - p_read) * 512 / 1024 / elapsed, 1)
                sample["disk_write_kbs"] = round((sectors_written - p_written) * 512 / 1024 / elapsed, 1)
        self._prev_disk = (sectors_read, sectors_written, now_s)
        return sample


class AixCollector:
    """Coleta via vmstat/lsattr/lsps (best-effort; sem coleta de disco)."""

    def __init__(self):
        self._mem_total_kb: int | None = None

    @staticmethod
    def _run(cmd: list[str]) -> str:
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            return proc.stdout or ""
        except (OSError, subprocess.SubprocessError):
            return ""

    def _mem_total(self) -> int:
        if self._mem_total_kb is None:
            self._mem_total_kb = parse_lsattr_realmem_kb(self._run(["lsattr", "-El", "sys0", "-a", "realmem"]))
        return self._mem_total_kb

    def sample(self, ts_ms: int) -> dict:
        sample = _base_sample(ts_ms)
        vm = parse_vmstat_aix(self._run(["vmstat", "1", "2"]))
        if vm.get("cpu_pct") is not None:
            sample["cpu_pct"] = vm["cpu_pct"]
        free_pages = vm.get("aix_free_pages")
        total_kb = self._mem_total()
        if free_pages is not None and total_kb:
            free_kb = free_pages * AIX_PAGE_SIZE_KB
            used_kb = max(0, total_kb - free_kb)
            sample["mem_total_mb"] = round(total_kb / 1024, 1)
            sample["mem_used_mb"] = round(used_kb / 1024, 1)
            sample["mem_pct"] = round(used_kb * 100 / total_kb, 1)
        swap_pct = parse_lsps_swap_pct(self._run(["lsps", "-s"]))
        if swap_pct is not None:
            sample["swap_pct"] = swap_pct
        # AIX não tem os.getloadavg — fallback para `uptime`
        if sample["load1"] is None:
            loads = parse_uptime_loadavg(self._run(["uptime"]))
            if loads:
                sample["load1"], sample["load5"], sample["load15"] = loads
        return sample


def build_collector():
    """Seleciona o coletor da plataforma atual."""
    if platform.system().upper() == "AIX":
        return AixCollector()
    return LinuxCollector()


# ── Sampler em background ────────────────────────────────────────────────────

class HostMetricsSampler:
    """Thread que amostra recursos do host e grava em host_metrics.

    interval_s: período entre amostras (default 5s).
    retention_days: amostras mais antigas são apagadas 1x/hora (default 7).
    """

    def __init__(self, db_pool, interval_s: float = SAMPLE_INTERVAL_S, retention_days: int = RETENTION_DAYS):
        self.db_pool = db_pool
        self.interval_s = max(1.0, float(interval_s))
        self.retention_days = max(1, int(retention_days))
        self._collector = build_collector()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_cleanup = 0.0

    def sample_once(self) -> dict:
        """Coleta e grava uma amostra (também usado por testes)."""
        sample = self._collector.sample(now_ms())
        con = self.db_pool.acquire()
        try:
            con.execute(INSERT_SQL, tuple([sample["ts_ms"]] + [sample[f] for f in FIELDS]))
        finally:
            self.db_pool.release(con)
        return sample

    def _cleanup_old(self) -> None:
        cutoff = now_ms() - self.retention_days * 86_400_000
        con = self.db_pool.acquire()
        try:
            con.execute("DELETE FROM host_metrics WHERE ts_ms < ?", (cutoff,))
        finally:
            self.db_pool.release(con)

    def _loop(self) -> None:
        failures = 0
        while not self._stop.is_set():
            try:
                self.sample_once()
                failures = 0
                if time.monotonic() - self._last_cleanup > 3600:
                    self._cleanup_old()
                    self._last_cleanup = time.monotonic()
            except Exception:  # sampler nunca derruba o servidor
                failures += 1
                # Loga a 1ª falha e depois 1x a cada 60 para não spammar o log
                if failures == 1 or failures % 60 == 0:
                    log.warning("[host_metrics] falha ao coletar/gravar amostra (%d seguidas)", failures, exc_info=True)
            self._stop.wait(self.interval_s)

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="host-metrics-sampler", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        if self._thread is None:
            return
        self._stop.set()
        self._thread.join(timeout=timeout)
        self._thread = None
