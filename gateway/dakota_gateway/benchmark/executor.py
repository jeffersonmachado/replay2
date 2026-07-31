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
                   iteration: int, concurrency: int) -> tuple[list, str]:
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

        Devolve ``(amostras, fatal_reason)`` — ``fatal_reason`` vazio = ok.
        """
        coletadas: list = []
        lock = threading.Lock()
        deadline = time.monotonic() + max(0, seconds)
        journeys = self._journeys
        estado = {"erros_fase": 0, "fatal": ""}

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
                    try:
                        produzidas = adapter.execute_journey(
                            handle, journey, phase=phase)
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
        return coletadas, estado["fatal"]

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
            por_fase: dict[str, list] = {f: [] for f in _TIMED_PHASES}
            host_metrics: list[dict] = []
            database_metrics: dict = {"available": False,
                                      "reason": "collector_not_run"}
            status = "COMPLETED"
            error_reason = ""
            inc_antes = len(getattr(adapter, "journey_incompletions", []) or [])
            try:
                fases = (("WARMUP", self.contract.warmup_seconds),
                         ("MEASUREMENT", self.contract.measurement_seconds),
                         ("COOLDOWN", self.contract.cooldown_seconds))
                for phase, seconds in fases:
                    amostras, fatal = self._run_phase(
                        adapter, phase, seconds, iteration, concurrency)
                    por_fase[phase] = amostras
                    if fatal:
                        status = "FAILED"
                        error_reason = fatal
                        break
                finished_ms = _now_ms()
                try:
                    host_metrics = list(
                        adapter.collect_host_metrics(started_ms, finished_ms) or [])
                except Exception as exc:
                    host_metrics = [{"available": False, "reason": str(exc)}]
                try:
                    database_metrics = dict(adapter.collect_database_metrics() or {})
                except Exception as exc:
                    database_metrics = {"available": False, "reason": str(exc)}
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
            )
            run_id = f"{env_id}-iter{iteration}-conc{concurrency}"
            result.host_samples_path = self._write_run_artifacts(
                run_id, result, host_metrics, env_order,
                session_logs=session_logs, incompletions=incompletas,
                host_status=getattr(adapter, "host_metrics_status", None))
            results.append(result)
        return results

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

        # WARMUP → MEASUREMENT → COOLDOWN por nível × iteração (§11 pareado)
        all_runs: list[EnvironmentRunResult] = []
        envs = list(self.contract.environments)
        for iteration in range(1, self.contract.iterations + 1):
            env_order = envs if iteration % 2 == 1 else list(reversed(envs))
            for concurrency in self.contract.concurrency_levels:
                self.order_history.append({
                    "iteration": iteration,
                    "concurrency": concurrency,
                    "environment_order": list(env_order),
                })
                results = self.run_level(concurrency, iteration, env_order)
                all_runs.extend(results)
                host_metrics = self._collect_level_host_metrics(results)
                stop = self._stop_condition_hit(results, host_metrics)
                if stop is not None:
                    self.stop_reason = {
                        "iteration": iteration,
                        "concurrency": concurrency,
                        **stop,
                    }
                    break
            if self.stop_reason is not None:
                break

        # VALIDATION + CLEANUP
        for env_id in self.contract.environments:
            try:
                self.adapters[env_id].cleanup()
            except Exception:
                pass

        falhas = [r for r in all_runs if r.status != "COMPLETED"]
        if falhas:
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
