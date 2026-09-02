"""Regras do benchmark real por trás das rotas HTTP (contrato §21).

Camada de serviço do control plane para o pacote oficial
``dakota_gateway.benchmark``: criação de experimentos (contrato imutável +
manifesto), execução supervisionada em thread daemon (mesmo padrão do
``Runner.start_run_async`` das runs), cancelamento cooperativo, métricas
agregadas (amostras de aplicação e de host), comparação/decisão e relatório.

Nada aqui inventa números: sem amostras reais persistidas/artefatos no disco,
o veredito devolvido é sempre ``INCONCLUSIVE``.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

from dakota_gateway.benchmark.adapters import SSHReplayAdapter
from dakota_gateway.benchmark.comparison import (
    build_capacity,
    build_comparison,
    build_decision,
)
from dakota_gateway.benchmark.contract import create_contract, load_contract
from dakota_gateway.benchmark.environments import EnvironmentModel
from dakota_gateway.benchmark.executor import BenchmarkExecutor
from dakota_gateway.benchmark.models import (
    EnvironmentRunResult,
    ExperimentResult,
    OperationSample,
)
from dakota_gateway.benchmark import persistence as bp
from dakota_gateway.benchmark.report import write_experiment_artifacts
from dakota_gateway.benchmark.stats import compute_stats
from dakota_gateway.state_db import connect, init_db

log = logging.getLogger("replay2")

#: Status de experimento enquanto a thread supervisionada está viva.
STATUS_RUNNING = "RUNNING"
STATUS_CANCELLED = "CANCELLED"


# ── helpers de disco ────────────────────────────────────────────────────────

def _experiment_dir(artifacts_dir, experiment_id: str) -> Path:
    return Path(artifacts_dir) / experiment_id


def _load_env_models(experiment_dir: Path) -> dict[str, EnvironmentModel]:
    """Recarrega os modelos de ambiente gravados na criação do experimento."""
    modelos: dict[str, EnvironmentModel] = {}
    env_dir = Path(experiment_dir) / "environments"
    if not env_dir.is_dir():
        return modelos
    for caminho in sorted(env_dir.glob("*.json")):
        try:
            modelo = EnvironmentModel.from_dict(
                json.loads(caminho.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("modelo de ambiente inválido %s: %s", caminho, exc)
            continue
        modelos[modelo.environment_id] = modelo
    return modelos


def _load_journeys(experiment_dir: Path) -> list[dict]:
    """Recarrega as jornadas gravadas na criação (lista vazia se ausentes)."""
    caminho = Path(experiment_dir) / "journeys.json"
    if not caminho.is_file():
        return []
    try:
        dados = json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(dados, dict):
        dados = dados.get("journeys", [])
    return list(dados or [])


def _rebuild_result(experiment_dir: Path, contract) -> ExperimentResult:
    """Reconstrói o ExperimentResult a partir dos artefatos gravados (§24).

    Mesma lógica do caminho CLI (``benchmark compare/report``): lê o
    ``execution-result.json`` do experimento e os ``application-samples.jsonl``
    de cada run.
    """
    resultado = ExperimentResult(
        contract_sha256=contract.sha256(), status="COMPLETED", runs=[])
    exp_result_path = Path(experiment_dir) / "execution-result.json"
    if exp_result_path.is_file():
        dados = json.loads(exp_result_path.read_text(encoding="utf-8"))
        resultado.status = dados.get("status", "COMPLETED")
        resultado.verdict = dados.get("verdict", "INCONCLUSIVE")
        resultado.reason = dados.get("reason", "")
        resultado.stop_reason = dados.get("stop_reason")
        resultado.recovery = dados.get("recovery") or {}
    runs_dir = Path(experiment_dir) / "runs"
    if not runs_dir.is_dir():
        return resultado
    for run_dir in sorted(runs_dir.iterdir()):
        resumo_path = run_dir / "execution-result.json"
        if not resumo_path.is_file():
            continue
        resumo = json.loads(resumo_path.read_text(encoding="utf-8"))
        por_fase: dict[str, list] = {"WARMUP": [], "MEASUREMENT": [], "COOLDOWN": []}
        samples_path = run_dir / "application-samples.jsonl"
        if samples_path.is_file():
            with open(samples_path, encoding="utf-8") as fh:
                for linha in fh:
                    linha = linha.strip()
                    if not linha:
                        continue
                    amostra = OperationSample(**json.loads(linha))
                    por_fase.setdefault(amostra.phase, []).append(amostra)
        run = EnvironmentRunResult(
            environment_id=resumo.get("environment_id", ""),
            iteration=int(resumo.get("iteration", 0)),
            concurrency=int(resumo.get("concurrency", 0)),
            status=resumo.get("status", "COMPLETED"),
            samples=por_fase.get("MEASUREMENT", []),
            warmup_samples=por_fase.get("WARMUP", []),
            cooldown_samples=por_fase.get("COOLDOWN", []),
            host_samples_path=str(run_dir / "host-samples.jsonl"),
            error_reason=resumo.get("error_reason", ""),
            # artefatos antigos (ex.: v7) não registram a contagem — None =
            # "não medido" e as métricas de jornada são omitidas, nunca
            # inferidas das amostras
            completed_journeys=resumo.get("completed_journeys"),
            planned_duration_s=resumo.get(
                "planned_duration_s", float(contract.measurement_seconds)),
            # FASE 3/4: janela de rede, cobertura funcional e clock offset
            # (None/ausente em artefatos antigos = "não registrado")
            net_window=resumo.get("net_window"),
            checkpoints_executed=resumo.get("checkpoints_executed"),
            checkpoints_checked=resumo.get("checkpoints_checked"),
            checkpoint_exceptions=resumo.get("checkpoint_exceptions"),
        )
        # clock offset medido na coleta (FASE 3): vem do status do coletor
        host_status = (resumo.get("host_metrics") or {}).get("status") or {}
        if host_status.get("clock_offset_ms") is not None:
            run.host_clock_offset_ms = int(host_status["clock_offset_ms"])
            run.host_clock_offset_measured = True
        resultado.runs.append(run)
    return resultado


def import_experiments_from_artifacts(con, *, artifacts_dir) -> dict:
    """Adota no banco experimentos cujos artefatos existem em disco (§24/§33).

    Cobre o gap deploy → UI: o tarball de release inclui
    ``artifacts/benchmarks/<experiment_id>/``, mas a listagem lê apenas
    ``benchmark_experiments`` — servidor recém-atualizado mostrava a lista
    vazia mesmo com relatórios reais em disco. Chamada no boot do control
    plane (e disponível para uso operacional), é idempotente: experimento já
    registrado é pulado e NUNCA sobrescrito. Diretório sem manifesto válido
    vai para ``errors`` e não derruba o boot.
    """
    resumo: dict = {"imported": [], "skipped": [], "errors": []}
    base = Path(artifacts_dir)
    if not base.is_dir():
        return resumo

    for exp_dir in sorted(base.iterdir()):
        if not exp_dir.is_dir():
            continue
        manifesto = exp_dir / "experiment-manifest.json"
        if not manifesto.is_file():
            continue
        experiment_id = exp_dir.name
        try:
            if bp.get_experiment(con, experiment_id):
                resumo["skipped"].append(experiment_id)
                continue
            contract = load_contract(manifesto)
            if contract.experiment_id != experiment_id:
                raise ValueError(
                    f"experiment_id do manifesto ({contract.experiment_id}) "
                    f"difere do diretório ({experiment_id})")

            status, verdict, reason = "COMPLETED", "INCONCLUSIVE", ""
            resultado_path = exp_dir / "execution-result.json"
            if resultado_path.is_file():
                dados = json.loads(resultado_path.read_text(encoding="utf-8"))
                status = dados.get("status", status)
                verdict = dados.get("verdict", verdict)
                reason = dados.get("reason", reason)
            bp.save_experiment(con, contract, status=status,
                               verdict=verdict, reason=reason)

            runs_dir = exp_dir / "runs"
            if runs_dir.is_dir():
                for run_dir in sorted(runs_dir.iterdir()):
                    resumo_run = run_dir / "execution-result.json"
                    if not run_dir.is_dir() or not resumo_run.is_file():
                        continue
                    dados = json.loads(resumo_run.read_text(encoding="utf-8"))
                    run = EnvironmentRunResult(
                        environment_id=dados.get("environment_id", ""),
                        iteration=int(dados.get("iteration", 0)),
                        concurrency=int(dados.get("concurrency", 0)),
                        status=dados.get("status", "COMPLETED"),
                        error_reason=dados.get("error_reason", ""),
                    )
                    bp.save_run(
                        con,
                        dados.get("run_id") or run_dir.name,
                        experiment_id,
                        run,
                        phase_order=list(dados.get("environment_order") or []),
                    )
            resumo["imported"].append(experiment_id)
        except Exception as exc:  # noqa: BLE001 — boot não pode abortar
            log.warning("importação de benchmark ignorou %s: %s",
                        experiment_id, exc)
            resumo["errors"].append(experiment_id)
    return resumo


def _persist_result(con, contract, executor, result) -> None:
    """Persiste experimento, runs, amostras de aplicação e de host no SQLite."""
    bp.save_experiment(con, contract, status=result.status,
                       verdict=result.verdict, reason=result.reason)
    ordens = {(o["iteration"], o["concurrency"]): o["environment_order"]
              for o in executor.order_history}
    for run in result.runs:
        run_id = f"{run.environment_id}-iter{run.iteration}-conc{run.concurrency}"
        bp.save_run(con, run_id, contract.experiment_id, run,
                    phase_order=ordens.get((run.iteration, run.concurrency), []))
        bp.save_app_samples(
            con, run_id,
            [*run.warmup_samples, *run.samples, *run.cooldown_samples])
        if run.host_samples_path:
            try:
                with open(run.host_samples_path, encoding="utf-8") as fh:
                    host_samples = [json.loads(l) for l in fh if l.strip()]
            except OSError:
                host_samples = []
            bp.save_host_samples(
                con, experiment_id=contract.experiment_id,
                environment_id=run.environment_id, run_id=run_id,
                iteration=run.iteration, concurrency=run.concurrency,
                phase="MEASUREMENT", samples=host_samples)


# ── criação de experimento ──────────────────────────────────────────────────

def create_experiment(con, body: dict, *, artifacts_dir) -> dict:
    """Cria o experimento: contrato imutável + manifesto + registro no banco.

    Levanta ``ValueError`` com mensagem legível quando o corpo é inválido.
    """
    body = dict(body or {})
    ambientes_raw = body.get("environments") or []
    if not isinstance(ambientes_raw, list) or not ambientes_raw:
        raise ValueError("environments: informe ao menos um ambiente (lista de modelos)")
    modelos: dict[str, EnvironmentModel] = {}
    for item in ambientes_raw:
        modelo = EnvironmentModel.from_dict(item)
        if not modelo.environment_id:
            raise ValueError("cada ambiente precisa de environment_id")
        if not modelo.host:
            raise ValueError(f"ambiente '{modelo.environment_id}' sem host")
        modelos[modelo.environment_id] = modelo

    journey_set_sha256 = str(body.get("journey_set_sha256") or "").strip()
    dataset_sha256 = str(body.get("dataset_sha256") or "").strip()
    if not journey_set_sha256:
        raise ValueError("journey_set_sha256 obrigatório (hash das jornadas)")
    if not dataset_sha256:
        raise ValueError("dataset_sha256 obrigatório (hash da massa de dados)")

    concurrency_levels = body.get("concurrency_levels") or []
    if not isinstance(concurrency_levels, list) or not concurrency_levels:
        raise ValueError("concurrency_levels: informe ao menos um nível")

    experiment_id = str(body.get("experiment_id") or "").strip() or (
        f"bench-{int(time.time() * 1000)}")
    if bp.get_experiment(con, experiment_id):
        raise ValueError(f"experimento '{experiment_id}' já existe — contrato imutável")

    contract = create_contract(
        experiment_id=experiment_id,
        journey_set_sha256=journey_set_sha256,
        dataset_sha256=dataset_sha256,
        application_version_sha256=str(
            body.get("application_version_sha256") or ""),
        seed=int(body.get("seed") or 0),
        terminal_geometry=str(body.get("terminal_geometry") or "80x24"),
        concurrency_levels=concurrency_levels,
        warmup_seconds=int(body.get("warmup_seconds") or 0),
        measurement_seconds=int(body.get("measurement_seconds") or 0),
        cooldown_seconds=int(body.get("cooldown_seconds") or 0),
        iterations=int(body.get("iterations") or 1),
        think_time_profile=dict(body.get("think_time_profile") or {
            "type": "none", "sha256": "", "params": {}}),
        stop_conditions=dict(body.get("stop_conditions") or {}),
        environments=list(modelos),
    )

    experiment_dir = _experiment_dir(artifacts_dir, contract.experiment_id)
    manifesto = contract.write_manifest(experiment_dir)

    # Modelos de ambiente (hardware/acesso) ficam ao lado do manifesto para
    # que start/compare/report reconstruam tudo sem estado extra no banco.
    env_dir = experiment_dir / "environments"
    env_dir.mkdir(parents=True, exist_ok=True)
    for env_id, modelo in modelos.items():
        (env_dir / f"{env_id}.json").write_text(
            json.dumps(modelo.to_dict(), indent=2, ensure_ascii=False,
                       sort_keys=True),
            encoding="utf-8")

    journeys = body.get("journeys") or []
    if journeys:
        (experiment_dir / "journeys.json").write_text(
            json.dumps(list(journeys), indent=2, ensure_ascii=False),
            encoding="utf-8")

    bp.save_experiment(con, contract)
    return {
        "ok": True,
        "experiment_id": contract.experiment_id,
        "contract_sha256": contract.sha256(),
        "manifest": str(manifesto),
        "contract": contract.to_manifest_dict(),
    }


# ── payloads de leitura ─────────────────────────────────────────────────────

def list_experiments_payload(con) -> dict:
    """Lista experimentos (mais recentes primeiro), sem o contrato bruto."""
    experimentos = []
    for exp in bp.list_experiments(con):
        experimentos.append({
            "experiment_id": exp["experiment_id"],
            "contract_sha256": exp["contract_sha256"],
            "created_at_ms": exp["created_at_ms"],
            "status": exp["status"],
            "verdict": exp["verdict"],
            "reason": exp["reason"],
        })
    return {"ok": True, "experiments": experimentos}


def experiment_detail_payload(con, experiment_id: str, *, artifacts_dir) -> dict | None:
    """Detalhe do experimento: contrato, status, decisão e ambientes."""
    exp = bp.get_experiment(con, experiment_id)
    if not exp:
        return None
    try:
        contract = json.loads(exp.get("contract_json") or "{}")
    except (TypeError, json.JSONDecodeError):
        contract = {}
    experiment_dir = _experiment_dir(artifacts_dir, experiment_id)
    modelos = _load_env_models(experiment_dir)
    runs = bp.list_runs(con, experiment_id)
    return {
        "ok": True,
        "experiment": {
            "experiment_id": exp["experiment_id"],
            "contract_sha256": exp["contract_sha256"],
            "created_at_ms": exp["created_at_ms"],
            "status": exp["status"],
            "verdict": exp["verdict"],
            "reason": exp["reason"],
            "contract": contract,
            "environments": {eid: m.to_dict() for eid, m in modelos.items()},
            "runs_count": len(runs),
        },
    }


def list_runs_payload(con, experiment_id: str) -> dict:
    """Lista as runs do experimento na ordem de execução."""
    return {"ok": True, "experiment_id": experiment_id,
            "runs": bp.list_runs(con, experiment_id)}


def metrics_payload(con, experiment_id: str, *, environment_id: str = "",
                    concurrency: int = 0, iteration: int = 0) -> dict:
    """Amostras/agregados de aplicação e de host por ambiente/nível/iteração.

    Agregados calculados por pooling das amostras brutas (``compute_stats``);
    sem amostras, as listas vêm vazias e o veredito é o do experimento
    (INCONCLUSIVE até uma execução real concluir).
    """
    exp = bp.get_experiment(con, experiment_id)
    if not exp:
        return {"ok": False, "error": "experimento não encontrado"}

    filtros = ["r.experiment_id = ?"]
    args: list = [experiment_id]
    if environment_id:
        filtros.append("r.environment_id = ?")
        args.append(environment_id)
    if concurrency:
        filtros.append("r.concurrency = ?")
        args.append(int(concurrency))
    if iteration:
        filtros.append("r.iteration = ?")
        args.append(int(iteration))
    where = " AND ".join(filtros)

    rows = con.execute(
        "SELECT r.environment_id, r.concurrency, r.iteration, s.phase,"
        " s.latency_ms, s.success, s.timeout, s.functional_divergence"
        " FROM benchmark_app_samples s"
        " JOIN benchmark_runs r ON r.run_id = s.run_id"
        f" WHERE {where}",
        args,
    ).fetchall()

    grupos: dict[tuple, dict] = {}
    for row in rows:
        chave = (row["environment_id"], row["concurrency"],
                 row["iteration"], row["phase"])
        grupo = grupos.setdefault(chave, {
            "latencias": [], "errors": 0, "timeouts": 0,
            "divergences": 0, "successes": 0,
        })
        grupo["latencias"].append(float(row["latency_ms"]))
        if row["success"]:
            grupo["successes"] += 1
        elif row["timeout"]:
            grupo["timeouts"] += 1
        else:
            grupo["errors"] += 1
        if row["functional_divergence"]:
            grupo["divergences"] += 1

    app_aggregates = []
    for (env_id, conc, it, phase), grupo in sorted(grupos.items()):
        stats = compute_stats(grupo["latencias"])
        app_aggregates.append({
            "environment_id": env_id,
            "concurrency": conc,
            "iteration": it,
            "phase": phase,
            "n": stats.n,
            "mean": stats.mean,
            "p50": stats.p50,
            "p90": stats.p90,
            "p95": stats.p95,
            "p99": stats.p99,
            "max": stats.max,
            "stdev": stats.stdev,
            "cv": stats.cv,
            "ci95_low": stats.ci95_low,
            "ci95_high": stats.ci95_high,
            "successes": grupo["successes"],
            "errors": grupo["errors"],
            "timeouts": grupo["timeouts"],
            "divergences": grupo["divergences"],
        })

    host_where = "experiment_id = ?"
    host_args: list = [experiment_id]
    if environment_id:
        host_where += " AND environment_id = ?"
        host_args.append(environment_id)
    host_rows = con.execute(
        "SELECT environment_id, cpu_user, cpu_system, cpu_wait, mem_used_mb,"
        " swap_pct, disk_read_kbs, disk_write_kbs, iops, net_rx_kbs, net_tx_kbs"
        f" FROM benchmark_host_samples WHERE {host_where}",
        host_args,
    ).fetchall()

    def _media_max(valores: list) -> tuple[float | None, float | None]:
        limpos = [float(v) for v in valores if v is not None]
        if not limpos:
            return None, None
        return sum(limpos) / len(limpos), max(limpos)

    host_grupos: dict[str, list] = {}
    for row in host_rows:
        host_grupos.setdefault(row["environment_id"], []).append(row)
    host_aggregates = []
    for env_id, linhas in sorted(host_grupos.items()):
        busy = [
            (r["cpu_user"] or 0.0) + (r["cpu_system"] or 0.0)
            for r in linhas
            if r["cpu_user"] is not None or r["cpu_system"] is not None
        ]
        mem_avg, mem_max = _media_max([r["mem_used_mb"] for r in linhas])
        _sw_avg, swap_max = _media_max([r["swap_pct"] for r in linhas])
        dr_avg, _dr_max = _media_max([r["disk_read_kbs"] for r in linhas])
        dw_avg, _dw_max = _media_max([r["disk_write_kbs"] for r in linhas])
        iops_avg, _iops_max = _media_max([r["iops"] for r in linhas])
        rx_avg, _rx_max = _media_max([r["net_rx_kbs"] for r in linhas])
        tx_avg, _tx_max = _media_max([r["net_tx_kbs"] for r in linhas])
        host_aggregates.append({
            "environment_id": env_id,
            "samples": len(linhas),
            "cpu_busy_avg": (sum(busy) / len(busy)) if busy else None,
            "cpu_busy_max": max(busy) if busy else None,
            "mem_used_mb_avg": mem_avg,
            "mem_used_mb_max": mem_max,
            "swap_pct_max": swap_max,
            "disk_read_kbs_avg": dr_avg,
            "disk_write_kbs_avg": dw_avg,
            "iops_avg": iops_avg,
            "net_rx_kbs_avg": rx_avg,
            "net_tx_kbs_avg": tx_avg,
        })

    return {
        "ok": True,
        "experiment_id": experiment_id,
        "status": exp["status"],
        "verdict": exp["verdict"],
        "app_aggregates": app_aggregates,
        "host_aggregates": host_aggregates,
    }


def comparison_payload(con, experiment_id: str, *, artifacts_dir) -> dict | None:
    """Comparação absoluta + normalizada + degradação + decisão.

    Usa o payload persistido pela execução quando existe; caso contrário
    reconstrói o resultado a partir dos artefatos (sem persistir). Sem runs
    reais: ``verdict=INCONCLUSIVE``, ``recommendation=None`` e comparação
    nula — nunca números inventados.
    """
    exp = bp.get_experiment(con, experiment_id)
    if not exp:
        return None

    experiment_dir = _experiment_dir(artifacts_dir, experiment_id)
    modelos = _load_env_models(experiment_dir)
    ambientes = {eid: m.to_dict() for eid, m in modelos.items()}

    row = con.execute(
        "SELECT payload_json FROM benchmark_comparisons"
        " WHERE experiment_id=? ORDER BY id DESC LIMIT 1",
        (experiment_id,),
    ).fetchone()
    if row:
        payload = json.loads(row["payload_json"])
        payload.update({
            "ok": True,
            "experiment_id": experiment_id,
            "status": exp["status"],
            "environments": ambientes,
            "result_type": "REAL",
        })
        return payload

    manifesto = experiment_dir / "experiment-manifest.json"
    runs = bp.list_runs(con, experiment_id)
    if not manifesto.is_file() or not runs:
        return {
            "ok": True,
            "experiment_id": experiment_id,
            "status": exp["status"],
            "verdict": "INCONCLUSIVE",
            "recommendation": None,
            "reasons": ["experimento sem execução — nenhuma amostra real"],
            "comparison": None,
            "capacity": None,
            "environments": ambientes,
            "result_type": "INCONCLUSIVE",
        }

    contract = load_contract(manifesto)
    result = _rebuild_result(experiment_dir, contract)
    if not result.runs:
        return {
            "ok": True,
            "experiment_id": experiment_id,
            "status": exp["status"],
            "verdict": "INCONCLUSIVE",
            "recommendation": None,
            "reasons": ["experimento sem execução — nenhuma amostra real"],
            "comparison": None,
            "capacity": None,
            "environments": ambientes,
            "result_type": "INCONCLUSIVE",
        }
    comparison = build_comparison(result, modelos or None, contract=contract)
    capacity = build_capacity(result)
    decision = build_decision(result, comparison)
    return {
        "ok": True,
        "experiment_id": experiment_id,
        "status": exp["status"],
        "verdict": decision.verdict,
        "recommendation": decision.recommendation,
        "reasons": decision.reasons,
        "comparison": comparison,
        "capacity": capacity,
        "environments": ambientes,
        "result_type": "REAL",
    }


def report_payload(experiment_id: str, *, artifacts_dir, fmt: str = "json"):
    """Lê o report.json (ou report.md com ``fmt='md'``) do experimento.

    Devolve ``(content_type, conteúdo)`` ou ``None`` quando o relatório ainda
    não foi gerado (experimento sem execução concluída).
    """
    experiment_dir = _experiment_dir(artifacts_dir, experiment_id)
    if fmt == "md":
        caminho = experiment_dir / "report.md"
        content_type = "text/markdown; charset=utf-8"
    else:
        caminho = experiment_dir / "report.json"
        content_type = "application/json; charset=utf-8"
    if not caminho.is_file():
        return None
    return content_type, caminho.read_text(encoding="utf-8")


# ── execução supervisionada ─────────────────────────────────────────────────

class _BenchmarkCancelled(Exception):
    """Cancelamento cooperativo solicitado pelo operador."""


class _CancellingAdapter:
    """Proxy de adaptador que aborta na primeira chamada após o cancelamento.

    O executor trata a exceção por run (status FAILED); a thread supervisionada
    converte o desfecho em ``CANCELLED`` quando o evento está marcado — sem
    alterar o pacote oficial ``dakota_gateway.benchmark``.
    """

    def __init__(self, adapter, cancel_event: threading.Event) -> None:
        object.__setattr__(self, "_adapter", adapter)
        object.__setattr__(self, "_cancel_event", cancel_event)

    def __getattr__(self, name: str):
        attr = getattr(object.__getattribute__(self, "_adapter"), name)
        if not callable(attr):
            return attr

        def guarded(*args, **kwargs):
            if object.__getattribute__(self, "_cancel_event").is_set():
                raise _BenchmarkCancelled("benchmark cancelado pelo operador")
            return attr(*args, **kwargs)

        return guarded


class BenchmarkSupervisor:
    """Dispara e supervisiona execuções de benchmark em threads daemon.

    Mesmo padrão do ``Runner.start_run_async`` das runs: registro de threads
    por experimento, conexão SQLite própria por thread e estado final gravado
    nas tabelas ``benchmark_*``.
    """

    def __init__(self, db_path: str, artifacts_dir, *,
                 adapter_factory=None) -> None:
        self.db_path = db_path
        self.artifacts_dir = Path(artifacts_dir)
        # adapter_factory(env_model, contract) → adapter; injetável em testes
        # (padrão: SSHReplayAdapter real, §8/§9 do contrato).
        self._adapter_factory = adapter_factory
        self._threads: dict[str, threading.Thread] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    def is_running(self, experiment_id: str) -> bool:
        with self._lock:
            thread = self._threads.get(experiment_id)
            return bool(thread and thread.is_alive())

    def start(self, experiment_id: str) -> None:
        """Dispara a execução (idempotente se a thread já estiver viva)."""
        with self._lock:
            thread = self._threads.get(experiment_id)
            if thread and thread.is_alive():
                return
            cancel_event = threading.Event()
            self._cancel_events[experiment_id] = cancel_event
            thread = threading.Thread(
                target=self._run,
                args=(experiment_id, cancel_event),
                daemon=True,
                name=f"benchmark-{experiment_id}",
            )
            self._threads[experiment_id] = thread
            thread.start()

    def cancel(self, experiment_id: str) -> None:
        """Marca o evento de cancelamento cooperativo do experimento."""
        with self._lock:
            event = self._cancel_events.get(experiment_id)
            if event is not None:
                event.set()

    def wait_completion(self, experiment_id: str,
                        timeout: float | None = None) -> bool:
        """Aguarda a thread do experimento terminar (sinal real de conclusão).

        Retorna True se a execução concluiu dentro de ``timeout`` (ou se não
        há thread registrada); False se ainda está viva. Sem polling HTTP —
        quem chama recebe o retorno no instante da conclusão.
        """
        with self._lock:
            thread = self._threads.get(experiment_id)
        if thread is None:
            return True
        thread.join(timeout=timeout)
        return not thread.is_alive()

    def _make_adapter(self, modelo: EnvironmentModel, contract,
                      cancel_event: threading.Event):
        factory = self._adapter_factory
        adapter = (factory(modelo, contract) if factory
                   else SSHReplayAdapter(modelo, contract))
        return _CancellingAdapter(adapter, cancel_event)

    def _run(self, experiment_id: str, cancel_event: threading.Event) -> None:
        con = connect(self.db_path)
        try:
            init_db(con)
            experiment_dir = _experiment_dir(self.artifacts_dir, experiment_id)
            contract = load_contract(experiment_dir / "experiment-manifest.json")
            modelos = _load_env_models(experiment_dir)
            journeys = _load_journeys(experiment_dir)
            adapters = {
                env_id: self._make_adapter(modelos[env_id], contract, cancel_event)
                for env_id in contract.environments
                if env_id in modelos
            }
            executor = BenchmarkExecutor(contract, adapters,
                                         self.artifacts_dir,
                                         journeys=journeys)
            result = executor.run()

            comparison = build_comparison(result, modelos or None,
                                          contract=contract)
            capacity = build_capacity(result)
            decision = build_decision(result, comparison)
            result.verdict = decision.verdict
            write_experiment_artifacts(experiment_dir, result, comparison,
                                       capacity, decision)
            _persist_result(con, contract, executor, result)
            bp.save_comparison(con, experiment_id, {
                "verdict": decision.verdict,
                "recommendation": decision.recommendation,
                "reasons": decision.reasons,
                "comparison": comparison,
                "capacity": capacity,
            })
            if cancel_event.is_set():
                bp.update_experiment_status(
                    con, experiment_id, status=STATUS_CANCELLED,
                    verdict="INCONCLUSIVE", reason="cancelled_by_user")
            else:
                bp.update_experiment_status(
                    con, experiment_id, status=result.status,
                    verdict=decision.verdict,
                    reason=result.reason or "; ".join(decision.reasons))
        except Exception as exc:  # falha de infra da execução → FAILED auditável
            log.exception("execução do benchmark %s falhou", experiment_id)
            try:
                cancelado = cancel_event.is_set()
                bp.update_experiment_status(
                    con, experiment_id,
                    status=STATUS_CANCELLED if cancelado else "FAILED",
                    verdict="INCONCLUSIVE",
                    reason="cancelled_by_user" if cancelado else str(exc))
            except Exception:
                log.exception("falha ao registrar desfecho do benchmark %s",
                              experiment_id)
        finally:
            con.close()


def start_experiment(con, supervisor: BenchmarkSupervisor,
                     experiment_id: str, *, artifacts_dir) -> tuple[int, dict]:
    """Marca o experimento como RUNNING e dispara a thread supervisionada."""
    exp = bp.get_experiment(con, experiment_id)
    if not exp:
        return 404, {"ok": False, "error": "experimento não encontrado"}
    if exp["status"] == STATUS_RUNNING or supervisor.is_running(experiment_id):
        return 409, {"ok": False, "error": "experimento já em execução"}
    manifesto = _experiment_dir(artifacts_dir, experiment_id) / "experiment-manifest.json"
    if not manifesto.is_file():
        return 400, {"ok": False,
                     "error": "manifesto do experimento não encontrado — recrie o experimento"}
    bp.update_experiment_status(con, experiment_id, status=STATUS_RUNNING,
                                verdict="INCONCLUSIVE", reason="")
    supervisor.start(experiment_id)
    return 202, {"ok": True, "experiment_id": experiment_id,
                 "status": STATUS_RUNNING}


def cancel_experiment(con, supervisor: BenchmarkSupervisor,
                      experiment_id: str) -> tuple[int, dict]:
    """Solicita o cancelamento cooperativo de um experimento em execução."""
    exp = bp.get_experiment(con, experiment_id)
    if not exp:
        return 404, {"ok": False, "error": "experimento não encontrado"}
    if exp["status"] != STATUS_RUNNING:
        return 409, {"ok": False,
                     "error": f"experimento não está em execução (status={exp['status']})"}
    supervisor.cancel(experiment_id)
    bp.update_experiment_status(con, experiment_id, status=STATUS_CANCELLED,
                                verdict="INCONCLUSIVE", reason="cancelled_by_user")
    return 200, {"ok": True, "experiment_id": experiment_id,
                 "status": STATUS_CANCELLED}
