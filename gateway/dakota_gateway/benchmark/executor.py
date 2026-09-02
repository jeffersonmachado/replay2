"""Executor do benchmark real (contrato §2/§11/§12).

Fases obrigatórias (``PHASES``): PREFLIGHT → PREPARE → WARMUP → MEASUREMENT →
COOLDOWN → VALIDATION → CLEANUP → COMPLETED.

Regras rígidas:

- §2: qualquer ambiente inacessível no PREFLIGHT aborta o experimento com
  ``status=FAILED``, ``verdict=INCONCLUSIVE``, ``reason=environment_unreachable``
  — NUNCA PASS — e nenhuma jornada é executada no ambiente reprovado;
- §11: execução PAREADA — a ordem dos ambientes alterna por iteração
  (iteração 1: A→B, iteração 2: B→A, ...) e a ordem é registrada;
- §12: só amostras da fase MEASUREMENT entram no agregado oficial
  (``EnvironmentRunResult.samples``); warmup/cooldown são preservados nos
  campos próprios para auditoria;
- §11/§18: a escada de carga (``concurrency_levels``) é interrompida quando
  uma ``stop_condition`` é atingida (erro, p99, CPU de host, crescimento de
  swap) — com o motivo registrado;
- §9/circuit breaker: cada passada da jornada é um ciclo completo (abre
  sessão → executa → fecha sessão). Se a sessão morre, a jornada corrente
  aborta (sem amostras sintéticas), a próxima passada abre sessão nova, e
  limites de erro (``MAX_ERROR_SAMPLES_PER_PHASE``,
  ``MAX_CONSECUTIVE_FAILED_PASSES``) abortam a fase/run com FAILED — uma
  sessão morta nunca gera tempestade de amostras de erro.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from .contract import ExperimentContract
from .models import EnvironmentRunResult, ExperimentResult, OperationSample
from .stats import percentile

PHASES = ("PREFLIGHT", "PREPARE", "WARMUP", "MEASUREMENT", "COOLDOWN",
          "VALIDATION", "CLEANUP", "COMPLETED")

#: Fases cronometradas dentro de cada run (nível × iteração × ambiente).
_TIMED_PHASES = ("WARMUP", "MEASUREMENT", "COOLDOWN")

# ── Circuit breaker (defesa em profundidade contra tempestade de erros) ────
#: Máximo de amostras de erro por fase: acima disso a fase aborta e a run
#: falha — uma sessão morta NUNCA pode gerar milhões de amostras.
MAX_ERROR_SAMPLES_PER_PHASE = 1000
#: Passadas consecutivas morrendo por erro de sessão → ambiente instável.
MAX_CONSECUTIVE_FAILED_PASSES = 3
#: Backoff antes de reabrir sessão após passada com morte inesperada.
FAILED_PASS_BACKOFF_S = 0.5
#: error_codes que indicam sessão morta de forma INESPERADA (session_closed
#: é fim natural de jornada — o app/shell encerrou — e não conta como falha).
_SESSION_FATAL_CODES = ("session_io_error",)

# ── Sonda de recuperação pós-carga (FASE 3) ────────────────────────────────
#: O host é considerado RECUPERADO quando a CPU volta a
#: baseline + max(RECOVERY_CPU_MARGIN_PP, baseline × RECOVERY_CPU_MARGIN_REL)
#: E o load1 volta a baseline + RECOVERY_LOAD_MARGIN — tolerância documentada
#: ao ruído de fundo do host (nunca "recuperou quando chegou a zero").
RECOVERY_CPU_MARGIN_PP = 5.0
RECOVERY_CPU_MARGIN_REL = 0.10
RECOVERY_LOAD_MARGIN = 0.5
#: Janela pré-carga consultada para a baseline de host (ms).
RECOVERY_BASELINE_WINDOW_MS = 60_000


def _now_ms() -> int:
    return int(time.time() * 1000)


def _amostra_para_jsonl(amostra) -> str:
    """Serializa uma amostra (OperationSample ou duck type) como linha JSON."""
    to_jsonl = getattr(amostra, "to_jsonl", None)
    if callable(to_jsonl):
        return to_jsonl()
    return json.dumps(dict(vars(amostra)), sort_keys=True, ensure_ascii=False)


class BenchmarkExecutor:
    """Orquestra o experimento completo em todos os ambientes (§11)."""

    def __init__(self, contract: ExperimentContract,
                 adapters: dict, artifacts_dir: Path,
                 *, journeys: list[dict] | None = None) -> None:
        self.contract = contract
        self.adapters = adapters
        self.artifacts_dir = Path(artifacts_dir)
        self.experiment_dir = self.artifacts_dir / contract.experiment_id
        self._journeys = list(journeys or [])
        self.order_history: list[dict] = []
        self.stop_reason: dict | None = None
        # FASE 3 — sonda de recuperação: baseline de host pré-carga por
        # ambiente e fim de carga (ms) da ÚLTIMA run de cada ambiente
        self._host_baseline: dict[str, list[dict]] = {}
        self._load_end_ms: dict[str, int] = {}

    # -- fases -----------------------------------------------------------

    def _preflight(self) -> dict[str, dict]:
        """PREFLIGHT: valida acessibilidade de TODOS os ambientes (§2)."""
        resultados: dict[str, dict] = {}
        for env_id in self.contract.environments:
            adapter = self.adapters.get(env_id)
            if adapter is None:
                resultados[env_id] = {
                    "ok": False,
                    "checks": [{"name": "adapter_present", "ok": False,
                                "detail": "adapter não configurado"}],
                }
                continue
            try:
                res = adapter.preflight()
            except Exception as exc:  # exceção de rede/conexão conta como falha
                res = {"ok": False, "checks": [
                    {"name": "preflight_exception", "ok": False, "detail": str(exc)},
                ]}
            resultados[env_id] = res if isinstance(res, dict) else {"ok": False}
        return resultados

    def _prepare(self) -> None:
        """PREPARE: garante o dataset do contrato em cada ambiente."""
        dataset_ref = {"dataset_sha256": self.contract.dataset_sha256,
                       "seed": self.contract.seed}
        for env_id in self.contract.environments:
            self.adapters[env_id].prepare_dataset(dataset_ref)

    def _run_phase(self, adapter, phase: str, seconds: int,
                   iteration: int, concurrency: int) -> tuple[list, str, int]:
        """Executa uma fase cronometrada com ``concurrency`` usuários virtuais.

        Cada passada da jornada é um ciclo completo (§9): o usuário virtual
        ABRE sessão nova, executa os passos e FECHA a sessão — assim, quando
        a aplicação encerra a sessão no fim da jornada (logout natural), a
        próxima passada simplesmente abre outra. Cada VU repete o ciclo até
        decorrer o tempo da fase, sempre executando ao menos UMA passada
        completa (§5.7).

        Circuit breaker: ``start_session`` falhando, N passadas consecutivas
        com morte inesperada de sessão ou mais de
        ``MAX_ERROR_SAMPLES_PER_PHASE`` amostras de erro abortam a fase com
        razão de falha — SEM gerar amostras sintéticas.

        Devolve ``(amostras, fatal_reason, jornadas_completas)`` —
        ``fatal_reason`` vazio = ok. ``jornadas_completas`` conta as
        passadas que executaram TODOS os passos da jornada (sem exceção e
        sem corte por morte de sessão — o corte é registrado pelo adaptador
        em ``journey_incompletions``): é a única fonte confiável de jornada
        completa, pois quem conhece a lista de passos é o executor/adaptador,
        não a amostra.
        """
        coletadas: list = []
        lock = threading.Lock()
        deadline = time.monotonic() + max(0, seconds)
        journeys = self._journeys
        estado = {"erros_fase": 0, "fatal": "", "completas": 0}

        def worker(vu_id: str) -> None:
            primeira = True
            passadas_falhas = 0
            while primeira or time.monotonic() < deadline:
                primeira = False
                if estado["fatal"]:
                    return
                for journey in journeys:
                    if estado["fatal"]:
                        return
                    # §9: ciclo completo — sessão nova por passada da jornada
                    try:
                        handle = adapter.start_session(vu_id)
                    except Exception as exc:
                        estado["fatal"] = f"start_session_failed: {exc}"
                        return
                    incompletas_antes = len(
                        getattr(adapter, "journey_incompletions", None) or [])
                    try:
                        produzidas = adapter.execute_journey(
                            handle, journey, phase=phase)
                        incompletas_depois = len(
                            getattr(adapter, "journey_incompletions", None) or [])
                        if incompletas_depois == incompletas_antes:
                            # passada sem corte: todos os passos executados
                            with lock:
                                estado["completas"] += 1
                    except Exception as exc:
                        produzidas = []
                        estado["fatal"] = f"execute_journey_failed: {exc}"
                    finally:
                        try:
                            adapter.stop_session(handle)
                        except Exception:
                            pass
                    erros_sessao = 0
                    if produzidas:
                        with lock:
                            coletadas.extend(produzidas)
                        erros = sum(
                            1 for s in produzidas
                            if not getattr(s, "success", False))
                        erros_sessao = sum(
                            1 for s in produzidas
                            if getattr(s, "error_code", None)
                            in _SESSION_FATAL_CODES)
                        with lock:
                            estado["erros_fase"] += erros
                    if estado["erros_fase"] >= MAX_ERROR_SAMPLES_PER_PHASE:
                        estado["fatal"] = "error_sample_cap_exceeded"
                        return
                    if estado["fatal"]:
                        return
                    if erros_sessao:
                        # sessão morreu de forma inesperada: backoff e
                        # disjuntor de passadas falhas consecutivas
                        passadas_falhas += 1
                        if passadas_falhas >= MAX_CONSECUTIVE_FAILED_PASSES:
                            estado["fatal"] = "session_unstable"
                            return
                        time.sleep(FAILED_PASS_BACKOFF_S)
                    else:
                        passadas_falhas = 0
                if not journeys:
                    break

        threads = [threading.Thread(target=worker, args=(f"vu-{i + 1}",),
                                    daemon=True)
                   for i in range(concurrency)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        return coletadas, estado["fatal"], estado["completas"]

    # -- escada / parada ---------------------------------------------------

    def _stop_condition_hit(self, results: list[EnvironmentRunResult],
                            host_metrics: list[dict]) -> dict | None:
        """Avalia as ``stop_conditions`` do contrato sobre o nível executado."""
        sc = self.contract.stop_conditions
        total = 0
        erros = 0
        latencias: list[float] = []
        for run in results:
            for s in run.samples:
                total += 1
                latencias.append(float(s.latency_ms))
                if not s.success or s.timeout:
                    erros += 1
        if total:
            erro_pct = erros / total * 100.0
            if erro_pct > sc.error_rate_pct:
                return {"condition": "error_rate_pct", "value": erro_pct,
                        "limit": sc.error_rate_pct}
            p99 = percentile(sorted(latencias), 99)
            if p99 > sc.p99_limit_ms:
                return {"condition": "p99_limit_ms", "value": p99,
                        "limit": sc.p99_limit_ms}
        for amostra in host_metrics or []:
            swap_growth = amostra.get("swap_growth_mb")
            if swap_growth is not None and float(swap_growth) > sc.swap_growth_mb:
                return {"condition": "swap_growth_mb", "value": float(swap_growth),
                        "limit": sc.swap_growth_mb}
        # CPU de host: saturação SUSTENTADA (§17 "CPU permanecer saturada") —
        # exige ``host_cpu_sustained_samples`` amostras CONSECUTIVAS acima do
        # limite na série temporal de UMA run (pico isolado não para a escada;
        # caso real v1: 1 amostra a 99% no AIX de produção truncou a escada).
        sustained = max(1, int(getattr(sc, "host_cpu_sustained_samples", 3)))
        for run in results:
            consecutivas = 0
            for amostra in self._read_run_host_samples(run):
                cpu = amostra.get("cpu_pct")
                if cpu is not None and float(cpu) > sc.host_cpu_pct:
                    consecutivas += 1
                    if consecutivas >= sustained:
                        return {"condition": "host_cpu_pct",
                                "value": float(cpu),
                                "limit": sc.host_cpu_pct,
                                "sustained_samples": consecutivas,
                                "environment_id": run.environment_id}
                else:
                    consecutivas = 0
        return None

    # -- execução de um nível ----------------------------------------------

    def run_level(self, concurrency: int, iteration: int,
                  env_order: list[str]) -> list[EnvironmentRunResult]:
        """Executa um nível de concorrência em uma iteração (ordem pareada)."""
        results: list[EnvironmentRunResult] = []
        for env_id in env_order:
            adapter = self.adapters[env_id]
            # adaptadores stateful (ex.: SSHReplayAdapter) recebem o contexto
            # para estampar iteração/concorrência nas amostras (duck typing)
            set_ctx = getattr(adapter, "set_iteration_context", None)
            if callable(set_ctx):
                set_ctx(iteration, concurrency)
            started_ms = _now_ms()
            # contadores de rede da janela da run (FASE 3 — cobertura do
            # grupo "rede", que o sampler de host não instrumenta): leitura
            # antes/depois das fases, taxas = delta/tempo (best-effort;
            # adaptador sem o método ou falha remota → sem janela)
            collect_net = getattr(adapter, "collect_net_counters", None)
            net_antes = None
            if callable(collect_net):
                try:
                    net_antes = collect_net()
                except Exception:
                    net_antes = None
            por_fase: dict[str, list] = {f: [] for f in _TIMED_PHASES}
            completas_por_fase: dict[str, int] = {f: 0 for f in _TIMED_PHASES}
            host_metrics: list[dict] = []
            database_metrics: dict = {"available": False,
                                      "reason": "collector_not_run"}
            net_window: dict | None = None
            status = "COMPLETED"
            error_reason = ""
            inc_antes = len(getattr(adapter, "journey_incompletions", []) or [])
            # cobertura funcional (FASE 4): delta do checkpoint_log da run
            chk_antes = len(getattr(adapter, "checkpoint_log", []) or [])
            try:
                fases = (("WARMUP", self.contract.warmup_seconds),
                         ("MEASUREMENT", self.contract.measurement_seconds),
                         ("COOLDOWN", self.contract.cooldown_seconds))
                for phase, seconds in fases:
                    amostras, fatal, completas = self._run_phase(
                        adapter, phase, seconds, iteration, concurrency)
                    por_fase[phase] = amostras
                    completas_por_fase[phase] = completas
                    # janitor de órfãos entre fases: sessões mortas no meio
                    # da jornada deixam a árvore remota viva comendo CPU e
                    # distorcendo as fases seguintes (duck typing; falha do
                    # janitor nunca derruba a fase)
                    reap = getattr(adapter, "reap_orphans", None)
                    if callable(reap):
                        try:
                            reap()
                        except Exception:
                            pass
                    if fatal:
                        status = "FAILED"
                        error_reason = fatal
                        break
                finished_ms = _now_ms()
                self._load_end_ms[env_id] = finished_ms
                try:
                    host_metrics = list(
                        adapter.collect_host_metrics(started_ms, finished_ms) or [])
                except Exception as exc:
                    host_metrics = [{"available": False, "reason": str(exc)}]
                try:
                    database_metrics = dict(adapter.collect_database_metrics() or {})
                except Exception as exc:
                    database_metrics = {"available": False, "reason": str(exc)}
                if callable(collect_net):
                    try:
                        net_depois = collect_net()
                    except Exception:
                        net_depois = None
                    net_window = self._net_window(net_antes, net_depois,
                                                  started_ms, finished_ms)
                else:
                    net_window = None
            except Exception as exc:
                status = "FAILED"
                error_reason = str(exc)

            # evidência forense: tails de saída das sessões + jornadas
            # abortadas por morte/fechamento de sessão (run logs/, §24)
            tails_fn = getattr(adapter, "session_tails", None)
            session_logs = tails_fn() if callable(tails_fn) else {}
            incompletas = list(
                (getattr(adapter, "journey_incompletions", []) or [])[inc_antes:])

            result = EnvironmentRunResult(
                environment_id=env_id,
                iteration=iteration,
                concurrency=concurrency,
                status=status,
                samples=list(por_fase["MEASUREMENT"]),
                warmup_samples=list(por_fase["WARMUP"]),
                cooldown_samples=list(por_fase["COOLDOWN"]),
                database_metrics=database_metrics,
                error_reason=error_reason,
                completed_journeys=completas_por_fase["MEASUREMENT"],
                planned_duration_s=float(self.contract.measurement_seconds),
            )
            # clock skew (FASE 3): o offset medido pelo coletor remoto é
            # estampado na run — a comparação exige a prova de correção da
            # janela temporal (sem offset medido → INCONCLUSIVE)
            host_status = getattr(adapter, "host_metrics_status", None)
            if (isinstance(host_status, dict)
                    and host_status.get("clock_offset_ms") is not None):
                result.host_clock_offset_ms = int(host_status["clock_offset_ms"])
                result.host_clock_offset_measured = True
            result.net_window = net_window
            # Cobertura da verificação funcional (FASE 4): checkpoints da
            # fase MEASUREMENT desta run — executados/checados/exceções com
            # razão auditada. Adaptador sem checkpoint_log → None ("não
            # registrado"), nunca zero fingindo cobertura.
            if getattr(adapter, "checkpoint_log", None) is not None:
                checkpoints_meas = [
                    e for e in (adapter.checkpoint_log or [])[chk_antes:]
                    if e.get("phase") == "MEASUREMENT"]
                result.checkpoints_executed = len(checkpoints_meas)
                result.checkpoints_checked = sum(
                    1 for e in checkpoints_meas if e.get("checked"))
                result.checkpoint_exceptions = [
                    e for e in checkpoints_meas if not e.get("checked")]
            run_id = f"{env_id}-iter{iteration}-conc{concurrency}"
            result.host_samples_path = self._write_run_artifacts(
                run_id, result, host_metrics, env_order,
                session_logs=session_logs, incompletions=incompletas,
                host_status=host_status)
            results.append(result)
        return results

    @staticmethod
    def _net_window(net_antes: dict | None, net_depois: dict | None,
                    started_ms: int, finished_ms: int) -> dict | None:
        """Taxas de rede da janela da run a partir dos contadores remotos.

        ``net_*`` são contadores absolutos (bytes/pacotes desde o boot);
        as taxas são ``delta / duração_da_janela``. Contador que andou para
        trás (reboot/overflow) invalida a taxa correspondente — nunca gera
        número negativo inventado.
        """
        if not net_antes or not net_depois:
            return None
        janela_s = max((finished_ms - started_ms) / 1000.0, 0.001)
        janela: dict = {"fonte": "contadores_remotos",
                        "window_s": round(janela_s, 3)}
        for rotulo, chave, fator in (
                ("net_rx_kbs", "rx_bytes", 1024.0),
                ("net_tx_kbs", "tx_bytes", 1024.0),
                ("net_rx_pps", "rx_packets", 1.0),
                ("net_tx_pps", "tx_packets", 1.0)):
            antes = net_antes.get(chave)
            depois = net_depois.get(chave)
            if not isinstance(antes, (int, float)) or not isinstance(
                    depois, (int, float)):
                continue
            delta = float(depois) - float(antes)
            if delta < 0:
                continue
            janela[rotulo] = round(delta / janela_s / fator, 3)
        # cobertura exige ao menos UMA taxa calculada — dict sem taxa não é
        # evidência de rede
        return janela if any(
            k.startswith("net_") and isinstance(v, float)
            for k, v in janela.items()) else None

    def _persist_run_status(self, result: EnvironmentRunResult) -> None:
        """Atualiza o status no execution-result.json da run (pós-reclassificação)."""
        run_id = (f"{result.environment_id}-iter{result.iteration}"
                  f"-conc{result.concurrency}")
        resumo_path = (self.experiment_dir / "runs" / run_id
                       / "execution-result.json")
        if not resumo_path.is_file():
            return
        try:
            dados = json.loads(resumo_path.read_text(encoding="utf-8"))
            dados["status"] = result.status
            resumo_path.write_text(
                json.dumps(dados, indent=2, ensure_ascii=False),
                encoding="utf-8")
        except (OSError, ValueError):
            pass

    def _write_run_artifacts(self, run_id: str, result: EnvironmentRunResult,
                             host_metrics: list[dict],
                             env_order: list[str],
                             *, session_logs: dict | None = None,
                             incompletions: list | None = None,
                             host_status: dict | None = None) -> str:
        """Grava os artefatos da run (§24) e devolve o path de host-samples."""
        run_dir = self.experiment_dir / "runs" / run_id
        (run_dir / "logs").mkdir(parents=True, exist_ok=True)

        # logs forenses: tail da saída de cada usuário virtual (evidência do
        # que a sessão recebeu perto da morte/fechamento)
        for vu, tail in (session_logs or {}).items():
            if not tail:
                continue
            texto = tail.decode("utf-8", "replace") if isinstance(tail, (bytes, bytearray)) else str(tail)
            (run_dir / "logs" / f"session-{vu}.log").write_text(
                texto, encoding="utf-8")

        todas = [*result.warmup_samples, *result.samples, *result.cooldown_samples]
        with open(run_dir / "application-samples.jsonl", "w", encoding="utf-8") as fh:
            for amostra in todas:
                fh.write(_amostra_para_jsonl(amostra) + "\n")

        host_path = run_dir / "host-samples.jsonl"
        with open(host_path, "w", encoding="utf-8") as fh:
            for amostra in host_metrics or []:
                fh.write(json.dumps(amostra, sort_keys=True, ensure_ascii=False) + "\n")

        with open(run_dir / "database-samples.jsonl", "w", encoding="utf-8") as fh:
            if result.database_metrics:
                fh.write(json.dumps(result.database_metrics, sort_keys=True,
                                    ensure_ascii=False) + "\n")

        diffs = [dict(vars(s)) if not hasattr(s, "to_jsonl")
                 else json.loads(s.to_jsonl())
                 for s in result.samples if getattr(s, "functional_divergence", False)]
        (run_dir / "functional-diffs.json").write_text(
            json.dumps(diffs, indent=2, ensure_ascii=False), encoding="utf-8")

        resumo = {
            "run_id": run_id,
            "experiment_id": self.contract.experiment_id,
            "environment_id": result.environment_id,
            "iteration": result.iteration,
            "concurrency": result.concurrency,
            "environment_order": list(env_order),
            "status": result.status,
            "error_reason": result.error_reason,
            "measurement_samples": len(result.samples),
            "warmup_samples": len(result.warmup_samples),
            "cooldown_samples": len(result.cooldown_samples),
            "incomplete_journeys": list(incompletions or []),
            # jornadas COMPLETAS da fase MEASUREMENT (fonte confiável de
            # journeys_count/completed_journeys_per_second na comparação) e
            # duração planejada da fase — None em artefatos antigos
            "completed_journeys": result.completed_journeys,
            "planned_duration_s": result.planned_duration_s,
            # cobertura da verificação funcional (FASE 4 — deltas do
            # checkpoint_log do adaptador; None = não registrado) e janela
            # de rede por contadores remotos (FASE 3)
            "checkpoints_executed": result.checkpoints_executed,
            "checkpoints_checked": result.checkpoints_checked,
            "checkpoint_exceptions": result.checkpoint_exceptions,
            "net_window": result.net_window,
            # evidência da coleta de host: disponibilidade, tentativas e
            # clock offset medido (auditoria do skew orquestrador×host, §13)
            "host_metrics": {
                "samples": len(host_metrics or []),
                "status": dict(host_status) if host_status else None,
            },
        }
        texto = json.dumps(resumo, indent=2, ensure_ascii=False)
        (run_dir / "execution-result.json").write_text(texto, encoding="utf-8")
        (run_dir / "process-result.json").write_text(texto, encoding="utf-8")
        return str(host_path)

    # -- experimento completo ------------------------------------------------

    def run(self) -> ExperimentResult:
        """Executa o experimento completo (fases de ``PHASES``)."""
        self.experiment_dir.mkdir(parents=True, exist_ok=True)
        self.contract.write_manifest(self.experiment_dir)

        # PREFLIGHT — §2: ambiente inacessível NUNCA produz PASS.
        preflight = self._preflight()
        inacessiveis = [env for env, res in preflight.items() if not res.get("ok")]
        if inacessiveis:
            resultado = ExperimentResult(
                contract_sha256=self.contract.sha256(),
                status="FAILED",
                runs=[],
                verdict="INCONCLUSIVE",
                reason="environment_unreachable",
            )
            self._write_experiment_result(resultado, preflight)
            return resultado

        # PREPARE
        self._prepare()

        # Baseline de host pré-carga (FASE 3 — sonda de recuperação; sem
        # baseline o ambiente fica sem medição, nunca inventada)
        self._coletar_baselines_host()

        # WARMUP → MEASUREMENT → COOLDOWN por nível × iteração (§11 pareado)
        all_runs: list[EnvironmentRunResult] = []
        envs = list(self.contract.environments)
        niveis_transporte_consecutivos = 0
        transport_abort = False
        admission_ceiling: int | None = None
        for iteration in range(1, self.contract.iterations + 1):
            env_order = envs if iteration % 2 == 1 else list(reversed(envs))
            for concurrency in self.contract.concurrency_levels:
                # Teto de admissão: níveis >= ceiling são pulados (o ambiente
                # já demonstrou não admitir essa concorrência) — mas as
                # repetições dos níveis INFERIORES nas iterações seguintes
                # continuam (estatística pareada preservada).
                if (admission_ceiling is not None
                        and concurrency >= admission_ceiling):
                    continue
                self.order_history.append({
                    "iteration": iteration,
                    "concurrency": concurrency,
                    "environment_order": list(env_order),
                })
                results = self.run_level(concurrency, iteration, env_order)
                all_runs.extend(results)
                # Colapso de TRANSPORTE (VPN/rede local do orquestrador —
                # caso real cap13 v5: "Network is unreachable" nos dois
                # hosts por horas): TODAS as runs do nível falharam no
                # start por erro de transporte. 2 níveis consecutivos assim
                # = ambiente inacessível: aborta cedo como INCONCLUSIVE em
                # vez de moer níveis condenados (nunca PASS, §5.2/§20).
                if results and all(
                        r.status == "FAILED"
                        and getattr(self.adapters.get(r.environment_id),
                                    "last_start_error_transport", False)
                        for r in results):
                    niveis_transporte_consecutivos += 1
                else:
                    niveis_transporte_consecutivos = 0
                if niveis_transporte_consecutivos >= 2:
                    transport_abort = True
                    break
                host_metrics = self._collect_level_host_metrics(results)
                stop = self._stop_condition_hit(results, host_metrics)
                if stop is not None:
                    self.stop_reason = {
                        "iteration": iteration,
                        "concurrency": concurrency,
                        **stop,
                    }
                    break
                # LIMITE DE ADMISSÃO de sessões (§17 — saturação de admissão):
                # TODAS as runs do nível falharam no start_session (nenhuma
                # sessão admitida), SEM erro de transporte, e um nível
                # anterior COMPLETED. O ambiente demonstradamente não admite
                # N sessões concorrentes (menu não renderiza, licença do
                # runtime) — é o teto de admissão, achado de capacidade: as
                # runs do nível viram ABORTED e o nível (e superiores) é
                # pulado nas iterações seguintes. Avaliado DEPOIS das stop
                # conditions de host: saturação medida (CPU etc.) é sinal
                # mais específico e prevalece. Não confundir com transporte
                # (rede local caída → fail-fast acima) nem com nível
                # inicial quebrado (sem baseline → FAILED honesto).
                anteriores_ok = any(
                    r.status == "COMPLETED"
                    for r in all_runs[:-len(results)] or [])
                if (results and anteriores_ok and all(
                        r.status == "FAILED"
                        and r.error_reason.startswith("start_session_failed")
                        and not getattr(
                            self.adapters.get(r.environment_id),
                            "last_start_error_transport", False)
                        for r in results)):
                    if admission_ceiling is None:
                        admission_ceiling = concurrency
                    else:
                        admission_ceiling = min(admission_ceiling, concurrency)
                    if self.stop_reason is None:
                        self.stop_reason = {
                            "iteration": iteration,
                            "concurrency": concurrency,
                            "condition": "session_admission_limit",
                            "value": concurrency,
                            "limit": concurrency,
                        }
                    continue
            # Parada dura: transporte ou stop_condition de saturação/erro
            # (admission NÃO é parada dura — só teto para níveis superiores)
            if transport_abort or (
                    self.stop_reason is not None
                    and self.stop_reason.get("condition")
                    != "session_admission_limit"):
                break

        # VALIDATION + CLEANUP
        for env_id in self.contract.environments:
            try:
                self.adapters[env_id].cleanup()
            except Exception:
                pass

        # Sonda de recuperação pós-carga (FASE 3): medição REAL do retorno
        # do host à faixa da baseline — gravada no execution-result.json e
        # consumida pela comparação/relatório (recovery_seconds)
        recovery = self._sonda_recuperacao()

        # Stop_condition (§17): as falhas do NÍVEL PARADO (ex.: sessões que
        # não abrem sob saturação, "User limit exceeded" de licença) são o
        # achado de capacidade — reclassificadas como ABORTED, não derrubam
        # o experimento. Falhas em OUTROS níveis continuam fatais.
        # A reclassificação é PERSISTIDA no execution-result.json da run:
        # o rebuild (compare/report) lê o status do disco — memória e
        # evidência não podem divergir (bug real v7: disco FAILED ×
        # veredito WARN em memória).
        if self.stop_reason is not None:
            nivel = (self.stop_reason.get("iteration"),
                     self.stop_reason.get("concurrency"))
            for r in all_runs:
                if (r.status == "FAILED"
                        and (r.iteration, r.concurrency) == nivel):
                    r.status = "ABORTED"
                    self._persist_run_status(r)

        falhas = [r for r in all_runs if r.status == "FAILED"]
        if transport_abort:
            status = "FAILED"
            reason = "environment_unreachable_mid_run"
        elif falhas:
            status = "FAILED"
            reason = falhas[0].error_reason or "run_failed"
        elif self.stop_reason is not None:
            status = "COMPLETED"
            reason = f"stop_condition:{self.stop_reason['condition']}"
        else:
            status = "COMPLETED"
            reason = ""

        # COMPLETED — o veredito oficial sai de decision.decide (compare/report);
        # aqui o experimento nunca se auto-declara PASS.
        resultado = ExperimentResult(
            contract_sha256=self.contract.sha256(),
            status=status,
            runs=all_runs,
            verdict="INCONCLUSIVE",
            reason=reason,
            stop_reason=self.stop_reason,
            recovery=recovery,
        )
        self._write_experiment_result(resultado, preflight)
        return resultado

    def _read_run_host_samples(self, run: EnvironmentRunResult) -> list[dict]:
        """Lê as amostras de host gravadas por UMA run (ordem temporal)."""
        amostras: list[dict] = []
        if not run.host_samples_path:
            return amostras
        try:
            with open(run.host_samples_path, encoding="utf-8") as fh:
                for linha in fh:
                    linha = linha.strip()
                    if linha:
                        amostras.append(json.loads(linha))
        except OSError:
            pass
        return amostras

    def _collect_level_host_metrics(self, results: list[EnvironmentRunResult]) -> list[dict]:
        """Lê as amostras de host gravadas pelas runs do nível (p/ stop_conditions)."""
        amostras: list[dict] = []
        for run in results:
            amostras.extend(self._read_run_host_samples(run))
        return amostras

    # -- sonda de recuperação (FASE 3) --------------------------------------

    def _coletar_baselines_host(self) -> None:
        """Baseline de host PRÉ-CARGA por ambiente (sonda de recuperação).

        Só roda quando ``recovery_probe_seconds > 0`` no contrato. Sem
        baseline o ambiente fica sem medição de recuperação (relatório diz
        "não medido" — nunca inventado).
        """
        probe_s = int(getattr(self.contract, "recovery_probe_seconds", 0) or 0)
        if probe_s <= 0:
            return
        agora = _now_ms()
        for env_id in self.contract.environments:
            adapter = self.adapters.get(env_id)
            if adapter is None:
                continue
            try:
                amostras = list(adapter.collect_host_metrics(
                    agora - RECOVERY_BASELINE_WINDOW_MS, agora) or [])
            except Exception:
                amostras = []
            validas = [a for a in amostras
                       if isinstance(a, dict) and a.get("available") is not False]
            if validas:
                self._host_baseline[env_id] = validas

    def _sonda_recuperacao(self) -> dict:
        """Medição REAL da recuperação pós-carga (§18, FASE 3).

        Aguarda a janela de sonda e consulta as amostras pós-carga de cada
        ambiente: ``recovery_seconds`` = tempo entre o fim da carga e a
        PRIMEIRA amostra de volta à faixa da baseline (CPU e load1, com as
        margens documentadas ``RECOVERY_*``). Host que não recuperou dentro
        da janela → ``recovered=False`` + ``recovery_seconds=None``.
        """
        probe_s = int(getattr(self.contract, "recovery_probe_seconds", 0) or 0)
        if probe_s <= 0 or not self._host_baseline:
            return {}
        time.sleep(probe_s)
        recovery: dict = {}
        for env_id in self.contract.environments:
            adapter = self.adapters.get(env_id)
            baseline = self._host_baseline.get(env_id)
            fim_carga = self._load_end_ms.get(env_id)
            if adapter is None or not baseline or fim_carga is None:
                continue
            cpus = [float(a["cpu_pct"]) for a in baseline
                    if isinstance(a.get("cpu_pct"), (int, float))]
            loads = [float(a["load1"]) for a in baseline
                     if isinstance(a.get("load1"), (int, float))]
            if not cpus or not loads:
                continue
            base_cpu = sum(cpus) / len(cpus)
            base_load = sum(loads) / len(loads)
            lim_cpu = base_cpu + max(RECOVERY_CPU_MARGIN_PP,
                                     base_cpu * RECOVERY_CPU_MARGIN_REL)
            lim_load = base_load + RECOVERY_LOAD_MARGIN
            try:
                amostras = list(adapter.collect_host_metrics(
                    fim_carga, fim_carga + probe_s * 1000) or [])
            except Exception:
                continue
            validas = [a for a in amostras
                       if isinstance(a, dict) and a.get("available") is not False]
            recuperou_em: float | None = None
            for amostra in sorted(
                    validas, key=lambda a: int(a.get("ts_ms", 0))):
                cpu = amostra.get("cpu_pct")
                load = amostra.get("load1")
                ts = amostra.get("ts_ms")
                if not all(isinstance(v, (int, float))
                           for v in (cpu, load, ts)):
                    continue
                if float(cpu) <= lim_cpu and float(load) <= lim_load:
                    recuperou_em = (int(ts) - fim_carga) / 1000.0
                    break
            recovery[env_id] = {
                "recovered": recuperou_em is not None,
                "recovery_seconds": recuperou_em,
                "baseline": {"cpu_pct": round(base_cpu, 3),
                             "load1": round(base_load, 3)},
                "margins": {"cpu_pct": round(lim_cpu, 3),
                            "load1": round(lim_load, 3)},
                "probe_window_s": probe_s,
                "samples": len(validas),
            }
        return recovery

    def _write_experiment_result(self, resultado: ExperimentResult,
                                 preflight: dict) -> None:
        """Grava o execution-result.json do experimento (nível raiz)."""
        payload = {
            "experiment_id": self.contract.experiment_id,
            "contract_sha256": resultado.contract_sha256,
            "status": resultado.status,
            "verdict": resultado.verdict,
            "reason": resultado.reason,
            "phases": list(PHASES),
            "preflight": preflight,
            "order_history": self.order_history,
            "stop_reason": self.stop_reason,
            # sonda de recuperação pós-carga (FASE 3): {} quando desligada
            "recovery": dict(getattr(resultado, "recovery", None) or {}),
            "runs": [
                {
                    "environment_id": r.environment_id,
                    "iteration": r.iteration,
                    "concurrency": r.concurrency,
                    "status": r.status,
                    "error_reason": r.error_reason,
                    "measurement_samples": len(r.samples),
                    "warmup_samples": len(r.warmup_samples),
                    "cooldown_samples": len(r.cooldown_samples),
                }
                for r in resultado.runs
            ],
        }
        (self.experiment_dir / "execution-result.json").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


__all__ = [
    "PHASES",
    "BenchmarkExecutor",
    "EnvironmentRunResult",
    "ExperimentResult",
    "OperationSample",
]
