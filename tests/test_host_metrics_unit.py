#!/usr/bin/env python3
"""Testes para: host_metrics (coletor/parsers), host_metrics_service."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
GATEWAY_DIR = ROOT / "gateway"
sys.path.insert(0, str(GATEWAY_DIR))
sys.path.insert(0, str(GATEWAY_DIR / "control"))

from dakota_gateway.db import ConnectionPool, init_db
from dakota_gateway.host_metrics import (
    FIELDS,
    AixCollector,
    HostMetricsSampler,
    LinuxCollector,
    _base_sample,
    parse_iostat_aix,
    parse_iostat_d_aix,
    parse_lsattr_realmem_kb,
    parse_lsps_swap_pct,
    parse_proc_diskstats,
    parse_proc_diskstats_full,
    parse_proc_meminfo,
    parse_proc_stat_cpu,
    parse_proc_stat_iowait,
    parse_uptime_loadavg,
    parse_vmstat_aix,
)
from services.host_metrics_service import (
    EXPORT_FORMAT,
    build_export,
    downsample,
    query_host_metrics,
    run_window,
)

PROC_STAT = """cpu  100 0 50 800 20 0 5 0 0 0
cpu0 60 0 30 300 10 0 3 0 0 0
intr 12345
"""

PROC_MEMINFO = """MemTotal:       16384000 kB
MemFree:         1000000 kB
MemAvailable:    8192000 kB
SwapTotal:       2097152 kB
SwapFree:        1048576 kB
"""

PROC_DISKSTATS = """   8       0 sda 100 0 2000 0 50 0 4000 0 0 0 0
   8       1 sda1 10 0 200 0 5 0 400 0 0 0 0
 259       0 nvme0n1 200 0 8000 0 100 0 16000 0 0 0 0
 259       1 nvme0n1p1 20 0 800 0 10 0 1600 0 0 0 0
   7       0 loop0 1 0 2 0 0 0 0 0 0 0 0
"""

VMSTAT_AIX = """System configuration: lcpu=8 mem=32768MB

kthr    memory              page              faults        cpu
----- ----------- ------------------------ ------------ -----------
 r  b   avm   fre  re  pi  po  fr   sr  cy  in   sy  cs us sy id wa
 1  1 12345 67890   0   0   0   0    0   0  10  200 100  5  3 90  2
 2  0 12346 67800   0   0   0   0    0   0  12  250 120 25 10 60  5
"""

VMSTAT_AIX_LPAR = """System configuration: lcpu=8 mem=32768MB ent=2.00

kthr    memory              page              faults        cpu     -----  -----
----- ----------- ------------------------ ------------ ----------- --------
 r  b   avm   fre  re  pi  po  fr   sr  cy  in   sy  cs us sy id wa    pc  ec
 1  1 12345 67890   0   0   0   0    0   0  10  200 100 30 20 45  5  0.5 25.0
"""

LSATTR_REALMEM = """33554432 realmem N/A True
"""

LSPS_S = """Total Paging Space   Percent Used
      4096MB               37%
"""

UPTIME_AIX = """  01:30PM   up 10 days,  2:34,  5 users,  load average: 1.20, 1.10, 1.05
"""

# Formato real capturado no MIG24 (AIX 7300-02): `iostat 1 2` — o 1º relatório
# é o acumulado desde o boot; o 2º é o do intervalo de 1 s.
IOSTAT_AIX = """
System configuration: lcpu=4 drives=6 paths=5 vdisks=3

tty:      tin         tout    avg-cpu: % user % sys % idle % iowait
          0.0          0.0                0.9  34.6   61.5      3.0

Disks:         % tm_act     Kbps      tps    Kb_read   Kb_wrtn
cd0               0.0       0.0       0.0          0         0
hdisk9            0.0       8.0       2.0          0         8
hdisk8            0.0      68.0      17.0          0        68
hdisk6            0.0       0.0       0.0          0         0
hdisk7            0.0       0.0       0.0          0         0
hdisk0           36.0     324.0      64.0         32       292

tty:      tin         tout    avg-cpu: % user % sys % idle % iowait
          0.0          0.0                1.1   1.4   97.5      0.0

Disks:         % tm_act     Kbps      tps    Kb_read   Kb_wrtn
cd0               0.0       0.0       0.0          0         0
hdisk9            0.0       0.0       0.0          0         0
hdisk8            0.0       0.0       0.0          0         0
hdisk6            0.0       0.0       0.0          0         0
hdisk7            0.0       0.0       0.0          0         0
hdisk0            0.0       8.0       2.0          8         0
"""

# `iostat -D 1 2` no formato real do MIG24 (2 intervalos; valores do 2º
# relatório sintéticos para exercitar a ponderação — formato é o real).
IOSTAT_D_AIX = """
System configuration: lcpu=4 drives=6 paths=5 vdisks=3

hdisk0          xfer:  %tm_act      bps      tps      bread      bwrtn
                         36.0   33000.0     64.0      32.0     292.0
                read:      rps  avgserv  minserv  maxserv   timeouts      fails
                          5.0      9.9      0.1     20.0           0          0
               write:      wps  avgserv  minserv  maxserv   timeouts      fails
                         10.0      8.4      1.2     16.2           0          0
               queue:  avgtime  mintime  maxtime  avgwqsz    avgsqsz     sqfull
                          0.0      0.0      0.0      0.0        0.0         0.0
--------------------------------------------------------------------------------
hdisk0          xfer:  %tm_act      bps      tps      bread      bwrtn
                         12.0   40000.0     15.0      8192.0   32768.0
                read:      rps  avgserv  minserv  maxserv   timeouts      fails
                         10.0      2.0      0.1      8.4           0          0
               write:      wps  avgserv  minserv  maxserv   timeouts      fails
                          5.0      4.0      1.2     16.2           0          0
               queue:  avgtime  mintime  maxtime  avgwqsz    avgsqsz     sqfull
                          0.0      0.0      0.0      0.0        0.0         0.0
"""

IOSTAT_D_AIX_SEM_IO = """
hdisk0          xfer:  %tm_act      bps      tps      bread      bwrtn
                          0.0      0.0      0.0        0.0        0.0
                read:      rps  avgserv  minserv  maxserv   timeouts      fails
                          0.0      0.0      0.0      0.0           0          0
               write:      wps  avgserv  minserv  maxserv   timeouts      fails
                          0.0      0.0      0.9      9.9           0          0
               queue:  avgtime  mintime  maxtime  avgwqsz    avgsqsz     sqfull
                          0.0      0.0      0.0      0.0        0.0         0.0
"""



class TestParsersLinux(unittest.TestCase):
    def test_proc_stat_cpu(self):
        busy, total = parse_proc_stat_cpu(PROC_STAT)
        self.assertEqual(total, 975)
        self.assertEqual(busy, 975 - 800 - 20)  # idle + iowait

    def test_proc_stat_cpu_vazio(self):
        self.assertEqual(parse_proc_stat_cpu(""), (0, 0))

    def test_proc_meminfo(self):
        mem = parse_proc_meminfo(PROC_MEMINFO)
        self.assertEqual(mem["mem_total_mb"], 16000.0)
        self.assertEqual(mem["mem_used_mb"], 8000.0)
        self.assertEqual(mem["mem_pct"], 50.0)
        self.assertEqual(mem["swap_pct"], 50.0)

    def test_proc_meminfo_sem_swap(self):
        mem = parse_proc_meminfo("MemTotal: 1024 kB\nMemAvailable: 512 kB\n")
        self.assertIsNone(mem["swap_pct"])

    def test_proc_diskstats_ignora_particoes_e_loop(self):
        read, written = parse_proc_diskstats(PROC_DISKSTATS)
        # sda (2000/4000) + nvme0n1 (8000/16000); sda1, nvme0n1p1 e loop0 fora
        self.assertEqual(read, 10000)
        self.assertEqual(written, 20000)

    def test_proc_diskstats_full_campos_por_disco(self):
        discos = parse_proc_diskstats_full(PROC_DISKSTATS)
        self.assertEqual(set(discos), {"sda", "nvme0n1"})  # sem partições/loop
        # sda: rd_ios=100, rd_sectors=2000, rd_ticks=0, wr_ios=50,
        #      wr_sectors=4000, wr_ticks=0, time_in_io=0
        self.assertEqual(discos["sda"], (100, 2000, 0, 50, 4000, 0, 0))
        self.assertEqual(discos["nvme0n1"], (200, 8000, 0, 100, 16000, 0, 0))

    def test_proc_stat_iowait(self):
        iowait, total = parse_proc_stat_iowait(PROC_STAT)
        self.assertEqual((iowait, total), (20, 975))
        self.assertEqual(parse_proc_stat_iowait(""), (0, 0))


class TestParsersAix(unittest.TestCase):
    def test_vmstat_usa_ultima_linha(self):
        vm = parse_vmstat_aix(VMSTAT_AIX)
        self.assertEqual(vm["cpu_pct"], 35.0)  # us=25 + sy=10
        self.assertEqual(vm["aix_free_pages"], 67800)

    def test_vmstat_com_colunas_lpar(self):
        vm = parse_vmstat_aix(VMSTAT_AIX_LPAR)
        self.assertEqual(vm["cpu_pct"], 50.0)  # us=30 + sy=20
        self.assertEqual(vm["aix_free_pages"], 67890)

    def test_vmstat_vazio(self):
        self.assertEqual(parse_vmstat_aix("lixo\n"), {})

    def test_lsattr_realmem(self):
        self.assertEqual(parse_lsattr_realmem_kb(LSATTR_REALMEM), 33554432)
        # formato real do AIX: atributo primeiro, valor depois
        self.assertEqual(
            parse_lsattr_realmem_kb("realmem 98828288 Amount of usable physical memory in Kbytes False\n"),
            98828288,
        )
        self.assertEqual(parse_lsattr_realmem_kb(""), 0)

    def test_lsps_swap(self):
        self.assertEqual(parse_lsps_swap_pct(LSPS_S), 37.0)
        self.assertIsNone(parse_lsps_swap_pct("sem dados"))

    def test_uptime_loadavg(self):
        self.assertEqual(parse_uptime_loadavg(UPTIME_AIX), (1.20, 1.10, 1.05))
        self.assertIsNone(parse_uptime_loadavg("sem load average"))

    def test_iostat_usa_ultimo_relatorio(self):
        """O 1º relatório do iostat é desde-boot; o parser usa o do intervalo."""
        io = parse_iostat_aix(IOSTAT_AIX)
        # 2º relatório: hdisk0 Kb_read=8 Kb_wrtn=0 tps=2 tm_act=0; iowait=0
        self.assertEqual(io["disk_read_kbs"], 8.0)
        self.assertEqual(io["disk_write_kbs"], 0.0)
        self.assertEqual(io["iops"], 2.0)
        self.assertEqual(io["disk_busy_pct"], 0.0)
        self.assertEqual(io["cpu_iowait_pct"], 0.0)
        # se usasse o relatório desde-boot: read=32, write=360, iowait=3.0
        self.assertNotEqual(io["disk_read_kbs"], 32.0)
        self.assertNotEqual(io["cpu_iowait_pct"], 3.0)

    def test_iostat_disco_saturado(self):
        saturado = IOSTAT_AIX.replace(
            "hdisk0            0.0       8.0       2.0          8         0",
            "hdisk0           94.5     512.0     128.0        64       448",
        )
        io = parse_iostat_aix(saturado)
        self.assertEqual(io["disk_busy_pct"], 94.5)
        self.assertEqual(io["disk_read_kbs"], 64.0)
        self.assertEqual(io["disk_write_kbs"], 448.0)
        self.assertEqual(io["iops"], 128.0)

    def test_iostat_vazio_retorna_dict_vazio(self):
        self.assertEqual(parse_iostat_aix(""), {})
        self.assertEqual(parse_iostat_aix("lixo\n"), {})

    def test_iostat_d_latencia_ponderada_do_ultimo_relatorio(self):
        # último relatório: read 10 ops @2.0ms + write 5 ops @4.0ms → 40/15
        self.assertEqual(parse_iostat_d_aix(IOSTAT_D_AIX), 2.67)

    def test_iostat_d_sem_io_retorna_none(self):
        """Sem operações no intervalo não há latência medida (nunca 0.0)."""
        self.assertIsNone(parse_iostat_d_aix(IOSTAT_D_AIX_SEM_IO))
        self.assertIsNone(parse_iostat_d_aix(""))

    def test_iostat_d_tolerante_a_sufixos(self):
        """Sob carga o AIX imprime sufixos (caso real: maxtime '1.1S' no MIG24)."""
        com_sufixo = IOSTAT_D_AIX.replace(
            "                         10.0      2.0      0.1      8.4           0          0",
            "                         1.2K      1.1S     0.1      8.4           0          0",
        )
        # read: 1200 ops @1100 ms; write: 5 ops @4.0 ms → (1200*1100+20)/1205
        self.assertEqual(parse_iostat_d_aix(com_sufixo), 1095.45)

    def test_aix_collector_coleta_disco_via_iostat(self):
        outputs = {
            "vmstat": VMSTAT_AIX,
            "lsattr": LSATTR_REALMEM,
            "lsps": LSPS_S,
            "uptime": UPTIME_AIX,
            "iostat": IOSTAT_AIX,
        }
        collector = AixCollector()
        collector._run = lambda cmd: outputs.get(cmd[0], "") if "-D" not in cmd else IOSTAT_D_AIX
        with mock.patch.object(os, "getloadavg", None):
            sample = collector.sample(123)
        self.assertEqual(sample["disk_read_kbs"], 8.0)
        self.assertEqual(sample["iops"], 2.0)
        self.assertEqual(sample["disk_latency_ms"], 2.67)
        self.assertIn("disk_busy_pct", sample)
        self.assertIn("cpu_iowait_pct", sample)

    def test_aix_collector_sem_iostat_campos_none(self):
        """iostat ausente/falho deixa os campos de IO None (nunca zero)."""
        outputs = {
            "vmstat": VMSTAT_AIX,
            "lsattr": LSATTR_REALMEM,
            "lsps": LSPS_S,
            "uptime": UPTIME_AIX,
        }
        collector = AixCollector()
        collector._run = lambda cmd: outputs.get(cmd[0], "")
        with mock.patch.object(os, "getloadavg", None):
            sample = collector.sample(123)
        self.assertIsNone(sample["disk_read_kbs"])
        self.assertIsNone(sample["iops"])
        self.assertIsNone(sample["disk_latency_ms"])
        self.assertIsNone(sample["disk_busy_pct"])
        self.assertIsNone(sample["cpu_iowait_pct"])

    def test_aix_collector_sem_getloadavg_usa_uptime(self):
        """AIX não tem os.getloadavg: load vem do `uptime`; amostra não quebra."""
        outputs = {
            "vmstat": VMSTAT_AIX,
            "lsattr": LSATTR_REALMEM,
            "lsps": LSPS_S,
            "uptime": UPTIME_AIX,
        }
        collector = AixCollector()
        collector._run = lambda cmd: outputs.get(cmd[0], "")
        with mock.patch.object(os, "getloadavg", None):
            sample = collector.sample(123)
        self.assertEqual(sample["cpu_pct"], 35.0)
        self.assertEqual(sample["mem_total_mb"], 32768.0)
        self.assertEqual(sample["mem_pct"], 99.2)
        self.assertEqual(sample["swap_pct"], 37.0)
        self.assertEqual((sample["load1"], sample["load5"], sample["load15"]), (1.20, 1.10, 1.05))

    def test_base_sample_sem_getloadavg_nao_quebra(self):
        with mock.patch.object(os, "getloadavg", None):
            sample = _base_sample(123)
        self.assertIsNone(sample["load1"])
        self.assertEqual(sample["ts_ms"], 123)


class TestLinuxCollectorComProcFake(unittest.TestCase):
    def test_cpu_por_delta_e_memoria(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = Path(tmp)
            (proc / "meminfo").write_text(PROC_MEMINFO)
            (proc / "diskstats").write_text(PROC_DISKSTATS)
            (proc / "stat").write_text(PROC_STAT)
            collector = LinuxCollector(proc_root=tmp)
            first = collector.sample(1000)
            self.assertIsNone(first["cpu_pct"])  # sem delta na 1ª amostra
            # avança 100 jiffies busy + 200 total
            (proc / "stat").write_text(PROC_STAT.replace("cpu  100", "cpu  200").replace(" 800 ", " 850 "))
            second = collector.sample(2000)
            self.assertIsNotNone(second["cpu_pct"])
            self.assertGreater(second["cpu_pct"], 0)
            self.assertEqual(second["mem_pct"], 50.0)

    PROC_STAT_2 = "cpu  600 0 50 1250 70 0 5 0 0 0\n"
    PROC_DISKSTATS_2 = (
        "   8       0 sda 200 0 4000 500 100 0 8000 150 0 600 0\n"
        "   8       1 sda1 10 0 200 0 5 0 400 0 0 0 0\n"
        " 259       0 nvme0n1 200 0 8000 0 100 0 16000 0 0 0 0\n"
    )

    def test_disco_e_iowait_por_delta_parcidade_aix(self):
        """IOPS, latência, % busy e iowait derivados de /proc (paridade AIX)."""
        with tempfile.TemporaryDirectory() as tmp:
            proc = Path(tmp)
            (proc / "meminfo").write_text(PROC_MEMINFO)
            (proc / "diskstats").write_text(PROC_DISKSTATS)
            (proc / "stat").write_text(PROC_STAT)
            # clock injetado (nunca patch global de time.monotonic — flake)
            collector = LinuxCollector(proc_root=tmp, clock=iter([100.0, 105.0]).__next__)
            first = collector.sample(1000)
            self.assertIsNone(first["iops"])           # sem delta na 1ª
            self.assertIsNone(first["disk_latency_ms"])
            self.assertIsNone(first["cpu_iowait_pct"])
            (proc / "diskstats").write_text(self.PROC_DISKSTATS_2)
            (proc / "stat").write_text(self.PROC_STAT_2)
            second = collector.sample(2000)
            # sda em 5 s: Δrd_ios=100, Δwr_ios=50 → 150 ops → 30 IOPS
            self.assertEqual(second["iops"], 30.0)
            # latência ponderada: (500+150) ms ÷ 150 ops = 4.33 ms
            self.assertEqual(second["disk_latency_ms"], 4.33)
            # busy: Δtime_in_io=600 ms em 5000 ms → 12.0%
            self.assertEqual(second["disk_busy_pct"], 12.0)
            # iowait: Δ50 jiffies em Δ1000 → 5.0%
            self.assertEqual(second["cpu_iowait_pct"], 5.0)
            # taxas seguem funcionando: Δrd_sectors=2000, Δwr_sectors=4000
            self.assertEqual(second["disk_read_kbs"], round(2000 * 512 / 1024 / 5, 1))
            self.assertEqual(second["disk_write_kbs"], round(4000 * 512 / 1024 / 5, 1))

    def test_disco_sem_io_no_delta_latencia_none(self):
        """Sem operações no intervalo: latência None (nunca 0.0 fingido)."""
        with tempfile.TemporaryDirectory() as tmp:
            proc = Path(tmp)
            (proc / "meminfo").write_text(PROC_MEMINFO)
            (proc / "diskstats").write_text(PROC_DISKSTATS)
            (proc / "stat").write_text(PROC_STAT)
            collector = LinuxCollector(proc_root=tmp, clock=iter([100.0, 105.0]).__next__)
            collector.sample(1000)
            second = collector.sample(2000)  # diskstats idêntico → Δios=0
            self.assertIsNone(second["disk_latency_ms"])
            self.assertIsNone(second["iops"])
            self.assertEqual(second["disk_busy_pct"], 0.0)  # busy medido: 0


class HostMetricsDbCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.pool = ConnectionPool(str(Path(self.tmp.name) / "t.db"), min_size=1, max_size=2)
        con = self.pool.acquire()
        try:
            init_db(con)
        finally:
            self.pool.release(con)

    def tearDown(self):
        self.tmp.cleanup()

    def _insert_samples(self, base_ts: int, count: int) -> None:
        con = self.pool.acquire()
        try:
            for i in range(count):
                con.execute(
                    "INSERT INTO host_metrics (ts_ms, cpu_pct, load1, mem_pct) VALUES (?, ?, ?, ?)",
                    (base_ts + i * 1000, float(i), float(i) / 10, float(100 - i)),
                )
        finally:
            self.pool.release(con)

    def test_migracao_adiciona_colunas_de_io_em_db_antigo(self):
        """DB criado antes da v0.7.22 (sem colunas de IO) é migrado pelo init_db."""
        import sqlite3
        from dakota_gateway.host_metrics import INSERT_SQL
        raw = sqlite3.connect(str(Path(self.tmp.name) / "old.db"))
        raw.row_factory = sqlite3.Row
        raw.execute(
            "CREATE TABLE host_metrics ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT, ts_ms INTEGER NOT NULL,"
            " cpu_pct REAL, load1 REAL, load5 REAL, load15 REAL,"
            " mem_total_mb REAL, mem_used_mb REAL, mem_pct REAL, swap_pct REAL,"
            " disk_read_kbs REAL, disk_write_kbs REAL)"
        )
        init_db(raw)
        cols = {row["name"] for row in raw.execute("PRAGMA table_info(host_metrics)")}
        for col in ("iops", "disk_latency_ms", "disk_busy_pct", "cpu_iowait_pct"):
            self.assertIn(col, cols)
        # INSERT do sampler (todos os FIELDS) funciona no DB migrado
        raw.execute(INSERT_SQL, (1,) + tuple(None for _ in FIELDS))
        # idempotente: segunda passada não falha
        init_db(raw)
        raw.close()

    def test_sampler_grava_amostra(self):
        sampler = HostMetricsSampler(self.pool, interval_s=1)
        sample = sampler.sample_once()
        self.assertEqual(set(FIELDS) | {"ts_ms"}, set(sample.keys()))
        con = self.pool.acquire()
        try:
            n = con.execute("SELECT COUNT(*) c FROM host_metrics").fetchone()["c"]
        finally:
            self.pool.release(con)
        self.assertEqual(n, 1)

    def test_query_host_metrics_janela_e_downsample(self):
        self._insert_samples(base_ts=10_000, count=100)
        con = self.pool.acquire()
        try:
            payload = query_host_metrics(con, 10_000, 20_000, max_points=10)
        finally:
            self.pool.release(con)
        self.assertEqual(payload["total_samples"], 11)  # 10s..20s inclusivo
        self.assertLessEqual(len(payload["samples"]), 10)
        self.assertEqual(payload["window"], {"from_ms": 10_000, "to_ms": 20_000})

    def test_downsample_preserva_media(self):
        samples = [{"ts_ms": i, "cpu_pct": float(i % 2), "load1": None} for i in range(10)]
        out = downsample(samples, max_points=2)
        self.assertEqual(len(out), 2)
        for merged in out:
            self.assertIsNone(merged["load1"])
        # buckets [0,1,0,1,0]→0.4 e [1,0,1,0,1]→0.6
        self.assertAlmostEqual(out[0]["cpu_pct"], 0.4)
        self.assertAlmostEqual(out[1]["cpu_pct"], 0.6)

    def test_run_window_e_export(self):
        con = self.pool.acquire()
        try:
            con.execute(
                "INSERT INTO users (username, password_hash, role, created_at_ms)"
                " VALUES ('tester', 'x', 'admin', 1)",
            )
            con.execute(
                "INSERT INTO replay_runs (created_at_ms, created_by, log_dir, target_host,"
                " target_user, target_command, mode, run_fingerprint, status, started_at_ms,"
                " finished_at_ms) VALUES (?, 1, '/tmp', 'h', 'u', 'c', 'strict-global', 'fp',"
                " 'success', 5000, 8000)",
                (4000,),
            )
            run_id = con.execute("SELECT MAX(id) i FROM replay_runs").fetchone()["i"]
        finally:
            self.pool.release(con)
        self._insert_samples(base_ts=5000, count=4)
        con = self.pool.acquire()
        try:
            window = run_window(con, run_id)
            self.assertEqual((window["from_ms"], window["to_ms"]), (5000, 8000))
            export = build_export(con, run_id)
            self.assertEqual(export["format"], EXPORT_FORMAT)
            self.assertEqual(export["run"]["id"], run_id)
            self.assertEqual(len(export["samples"]), 4)  # 5s..8s inclusivo
            self.assertIsNone(run_window(con, 999999))
            self.assertIsNone(build_export(con, 999999))
        finally:
            self.pool.release(con)


if __name__ == "__main__":
    unittest.main()
