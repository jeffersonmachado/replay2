#!/usr/bin/env python3
"""Coleta de métricas de recursos do host (CPU, memória, load, disco).

Stdlib apenas. Linux lê /proc; AIX usa `vmstat`/`lsattr`/`lsps`/`iostat`
(best-effort: campos indisponíveis ficam None). O HostMetricsSampler roda em
thread dentro do control plane e grava amostras na tabela `host_metrics`,
alimentando o painel /observability/resources e a comparação de estresse
entre ambientes.
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
    "iops",
    "disk_latency_ms",
    "disk_busy_pct",
    "cpu_iowait_pct",
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


def parse_proc_diskstats_full(text: str) -> dict[str, tuple[int, int, int, int, int, int, int]]:
    """Contadores por disco-base do /proc/diskstats (para deltas entre amostras).

    Retorna ``{nome: (rd_ios, rd_sectors, rd_ticks_ms, wr_ios, wr_sectors,
    wr_ticks_ms, time_in_io_ms)}`` — os campos necessários para IOPS,
    latência média (ticks ÷ ios) e % busy (time_in_io ÷ elapsed) por delta.
    Partições e loop/ram ficam de fora (mesmo filtro de ``_DISK_RE``).
    """
    discos: dict[str, tuple[int, int, int, int, int, int, int]] = {}
    for line in text.splitlines():
        parts = line.split()
        # layout: major minor name rd_ios rd_merges rd_sectors rd_ticks
        #         wr_ios wr_merges wr_sectors wr_ticks in_flight time_in_io ...
        if len(parts) < 13 or not _DISK_RE.match(parts[2]):
            continue
        try:
            discos[parts[2]] = (
                int(parts[3]), int(parts[5]), int(parts[6]),
                int(parts[7]), int(parts[9]), int(parts[10]),
                int(parts[12]),
            )
        except ValueError:
            continue
    return discos


def parse_proc_stat_iowait(text: str) -> tuple[int, int]:
    """Extrai (jiffies_iowait, jiffies_total) da linha 'cpu' do /proc/stat."""
    for line in text.splitlines():
        if not line.startswith("cpu "):
            continue
        parts = [int(x) for x in line.split()[1:] if x.isdigit()]
        if len(parts) < 5:
            break
        return parts[4], sum(parts)
    return 0, 0


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


# Linha de disco do iostat clássico: name tm_act Kbps tps Kb_read Kb_wrtn.
# Só hdisk* — cd0 (óptico) e vdisk não entram na soma de IO do sistema.
_IOSTAT_DISK_RE = re.compile(
    r"^(hdisk\S*)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+(\d+)\s+(\d+)\s*$"
)

# Bloco de disco do `iostat -D`: "hdisk0          xfer:  %tm_act ..."
_IOSTAT_D_DISK_RE = re.compile(r"^(hdisk\S*)\s+xfer:")


def _float_ou_none(token: str) -> float | None:
    try:
        return float(token)
    except ValueError:
        return None


# Sufixos numéricos do iostat AIX: K/M/G de magnitude; "S" = segundos em
# campos de tempo cujo default é ms (caso real MIG24: maxtime "1.1S" sob
# flush pesado). Aplicado a ops (K/M/G) e avgserv (todos) — o AIX não emite
# "S" em contadores de taxa, então a interpretação é segura nos dois usos.
_SUFFIX_IOSTAT = {"K": 1e3, "M": 1e6, "G": 1e9, "S": 1e3}


def _num_iostat_aix(token: str) -> float | None:
    """Número do iostat AIX tolerante a sufixo (K/M/G/S), ou None."""
    t = token.strip().upper()
    if not t:
        return None
    mult = 1.0
    if len(t) > 1 and t[-1] in _SUFFIX_IOSTAT:
        mult = _SUFFIX_IOSTAT[t[-1]]
        t = t[:-1]
    try:
        return float(t) * mult
    except ValueError:
        return None


def parse_iostat_aix(text: str, interval_s: float = 1.0) -> dict:
    """Extrai IO de disco do ÚLTIMO relatório de intervalo do `iostat 1 2` (AIX).

    O primeiro relatório do iostat é sempre o acumulado desde o boot; o
    último é o do intervalo medido. Retorna (quando disponíveis):

    - ``disk_read_kbs``/``disk_write_kbs``: Kb_read/Kb_wrtn do intervalo,
      somados sobre os hdisk*, divididos pelo intervalo;
    - ``iops``: soma de tps dos hdisk*;
    - ``disk_busy_pct``: maior % tm_act entre os hdisk* (disco mais ocupado);
    - ``cpu_iowait_pct``: % iowait do avg-cpu do mesmo relatório.

    Saída vazia ou inesperada devolve {} (campos ficam None na amostra —
    nunca zero fingindo medição).
    """
    lines = text.splitlines()

    # % iowait da última linha de dados do avg-cpu (após cabeçalho "tty:")
    iowait: float | None = None
    for i, line in enumerate(lines):
        if line.startswith("tty:"):
            for j in range(i + 1, min(i + 3, len(lines))):
                parts = lines[j].split()
                if len(parts) == 6 and all(_float_ou_none(p) is not None for p in parts):
                    iowait = float(parts[5])
                    break

    # último cabeçalho "Disks:" (relatório do intervalo, não o desde-boot)
    disks_idx = -1
    for i, line in enumerate(lines):
        if line.startswith("Disks:"):
            disks_idx = i

    result: dict = {}
    if iowait is not None:
        result["cpu_iowait_pct"] = iowait
    if disks_idx < 0:
        return result

    read_kb = 0
    write_kb = 0
    tps_sum = 0.0
    busy_max = 0.0
    encontrou = False
    for line in lines[disks_idx + 1:]:
        if not line.strip():
            break
        m = _IOSTAT_DISK_RE.match(line)
        if not m:
            continue
        encontrou = True
        busy_max = max(busy_max, float(m.group(2)))
        tps_sum += float(m.group(4))
        read_kb += int(m.group(5))
        write_kb += int(m.group(6))
    if not encontrou:
        return result

    interval = interval_s if interval_s > 0 else 1.0
    result["disk_read_kbs"] = round(read_kb / interval, 1)
    result["disk_write_kbs"] = round(write_kb / interval, 1)
    result["iops"] = round(tps_sum, 1)
    result["disk_busy_pct"] = round(busy_max, 1)
    return result


def parse_iostat_d_aix(text: str) -> float | None:
    """Latência média ponderada de disco (ms) do `iostat -D 1 2` (AIX).

    Unidade validada empiricamente no MIG24 (AIX 7300-02): leitura raw de
    5000 ops em /dev/rhdisk0 mediu 0,144 ms/op e o avgserv do mesmo
    intervalo reportou 0,1 — o avgserv é em MILISSEGUNDOS (se fosse décimos
    de ms divergiria ~14x).

    Blocos posteriores sobrescrevem os anteriores (o 1º relatório é o
    acumulado desde o boot). A latência é a média ponderada pelas operações:
    (Σ rps*avgserv_read + Σ wps*avgserv_write) ÷ (Σ rps + Σ wps).
    Sem operações no intervalo, retorna None (sem IO = sem latência medida,
    nunca 0.0 fingido).
    """
    # disk -> {"read": (rps, avgserv), "write": (wps, avgserv)}
    disks: dict[str, dict[str, tuple[float, float]]] = {}
    current: str | None = None
    pending: str | None = None  # "read" | "write" (aguardando linha de dados)
    for line in text.splitlines():
        m = _IOSTAT_D_DISK_RE.match(line)
        if m:
            current = m.group(1)
            pending = None
            continue
        if current is None:
            continue
        if "avgserv" in line and "read:" in line:
            pending = "read"
            continue
        if "avgserv" in line and "write:" in line:
            pending = "write"
            continue
        if pending:
            parts = line.split()
            # só as 2 primeiras colunas importam (ops, avgserv); as demais
            # (minserv/maxserv) podem vir com sufixo sob carga extrema
            if len(parts) >= 2:
                ops = _num_iostat_aix(parts[0])
                avgserv = _num_iostat_aix(parts[1])
                if ops is not None and avgserv is not None:
                    disks.setdefault(current, {})[pending] = (ops, avgserv)
            pending = None

    ops_total = 0.0
    soma_ponderada = 0.0
    for dados in disks.values():
        for ops, avgserv in dados.values():
            ops_total += ops
            soma_ponderada += ops * avgserv
    if ops_total <= 0:
        return None
    return round(soma_ponderada / ops_total, 2)


# ── Coletores por plataforma ─────────────────────────────────────────────────

class LinuxCollector:
    """Coleta via /proc. CPU, iowait e disco dependem de delta entre amostras.

    Disco (paridade com o coletor AIX): além das taxas KB/s, deriva de
    /proc/diskstats o IOPS (Δios ÷ Δt), a latência média ponderada
    (Δ(rd_ticks+wr_ticks) ÷ Δios) e o % busy do disco mais ocupado
    (Δtime_in_io ÷ Δt — mesmo significado do % tm_act do iostat AIX).
    cpu_iowait_pct vem do Δ dos jiffies de iowait do /proc/stat.
    """

    def __init__(self, proc_root: str = "/proc", clock=None):
        self._proc = proc_root
        # clock injetável: testes NÃO devem patchar time.monotonic global —
        # outras threads do processo pytest consomem o side_effect e o
        # valor do delta vaza (flake observado na suíte completa)
        self._clock = clock or time.monotonic
        self._prev_cpu: tuple[int, int] | None = None
        self._prev_iowait: tuple[int, int] | None = None
        self._prev_disk: tuple[int, int, float] | None = None
        self._prev_disk_full: tuple[dict, float] | None = None

    def _read(self, name: str) -> str:
        try:
            with open(os.path.join(self._proc, name), encoding="utf-8", errors="replace") as fh:
                return fh.read()
        except OSError:
            return ""

    def sample(self, ts_ms: int) -> dict:
        sample = _base_sample(ts_ms)

        stat = self._read("stat")
        busy, total = parse_proc_stat_cpu(stat)
        if self._prev_cpu and total > self._prev_cpu[1]:
            d_busy = busy - self._prev_cpu[0]
            d_total = total - self._prev_cpu[1]
            sample["cpu_pct"] = round(d_busy * 100 / d_total, 1)
        if total:
            self._prev_cpu = (busy, total)

        iowait, total_io = parse_proc_stat_iowait(stat)
        if self._prev_iowait and total_io > self._prev_iowait[1]:
            d_iowait = iowait - self._prev_iowait[0]
            d_total_io = total_io - self._prev_iowait[1]
            sample["cpu_iowait_pct"] = round(d_iowait * 100 / d_total_io, 1)
        if total_io:
            self._prev_iowait = (iowait, total_io)

        mem = parse_proc_meminfo(self._read("meminfo"))
        sample.update({k: v for k, v in mem.items() if v is not None})

        sectors_read, sectors_written = parse_proc_diskstats(self._read("diskstats"))
        now_s = self._clock()
        if self._prev_disk:
            p_read, p_written, p_ts = self._prev_disk
            elapsed = now_s - p_ts
            if elapsed > 0:
                # setor = 512 bytes → kB/s
                sample["disk_read_kbs"] = round((sectors_read - p_read) * 512 / 1024 / elapsed, 1)
                sample["disk_write_kbs"] = round((sectors_written - p_written) * 512 / 1024 / elapsed, 1)
        self._prev_disk = (sectors_read, sectors_written, now_s)

        discos = parse_proc_diskstats_full(self._read("diskstats"))
        if self._prev_disk_full:
            p_discos, p_ts = self._prev_disk_full
            elapsed = now_s - p_ts
            if elapsed > 0:
                d_ios = 0
                d_ticks = 0
                busy_max = 0.0
                for nome, atual in discos.items():
                    anterior = p_discos.get(nome)
                    if anterior is None:
                        continue
                    ios = (atual[0] - anterior[0]) + (atual[3] - anterior[3])
                    if ios < 0:
                        continue  # contador reiniciado (ex.: hotplug)
                    d_ios += ios
                    d_ticks += (atual[2] - anterior[2]) + (atual[5] - anterior[5])
                    busy = (atual[6] - anterior[6]) / (elapsed * 1000) * 100
                    busy_max = max(busy_max, min(busy, 100.0))
                if d_ios > 0:
                    sample["iops"] = round(d_ios / elapsed, 1)
                    sample["disk_latency_ms"] = round(d_ticks / d_ios, 2)
                sample["disk_busy_pct"] = round(busy_max, 1)
        self._prev_disk_full = (discos, now_s)
        return sample


class AixCollector:
    """Coleta via vmstat/lsattr/lsps/iostat (best-effort).

    Disco: `iostat 1 2` (taxas do intervalo: KB/s lidos/escritos, IOPS,
    % tm_act do disco mais ocupado e % iowait da CPU) e `iostat -D 1 2`
    (latência média ponderada, avgserv em ms — unidade validada no MIG24).
    Custo: ~2 s adicionais por amostra (vmstat já consome ~1 s); qualquer
    comando ausente deixa os campos None, nunca zero fingindo medição.
    """

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
        # Disco: taxas do intervalo (iostat clássico) + latência (iostat -D)
        io = parse_iostat_aix(self._run(["iostat", "1", "2"]))
        for campo in ("disk_read_kbs", "disk_write_kbs", "iops",
                      "disk_busy_pct", "cpu_iowait_pct"):
            if io.get(campo) is not None:
                sample[campo] = io[campo]
        lat = parse_iostat_d_aix(self._run(["iostat", "-D", "1", "2"]))
        if lat is not None:
            sample["disk_latency_ms"] = lat
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
