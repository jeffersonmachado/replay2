#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import shutil
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# ── Logging padronizado ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stderr,
)
log = logging.getLogger("replay2")

from dakota_gateway import auth
from dakota_gateway.source_analyzer.audit import set_db_pool
from dakota_gateway.replay_control import (
    Runner,
    query_all,
    query_one,
    retry_run,
)
from dakota_gateway.state_db import connect, init_db, now_ms
from dakota_gateway.state_db import ConnectionPool
from control.routes import (
    handle_admin_get_route,
    handle_admin_post_route,
    handle_capture_delete_route,
    handle_capture_get_route,
    handle_capture_post_route,
    handle_catalog_delete_route,
    handle_catalog_get_route,
    handle_catalog_post_route,
    handle_gateway_get_route,
    handle_gateway_post_route,
    handle_observability_delete_route,
    handle_observability_get_route,
    handle_observability_post_route,
    handle_operational_delete_route,
    handle_operational_get_route,
    handle_operational_post_route,
    handle_run_delete_route,
    handle_run_get_route,
    handle_run_post_route,
    handle_journey_get_route,
    handle_journey_post_route,
    handle_synthetic_get_route,
    handle_synthetic_post_route,
    handle_benchmark_get_route,
    handle_benchmark_post_route,
    handle_ui_get_route,
)
from control.routes.route_helpers import write_json
from control.rate_limit import from_env as _rate_limiter_from_env
# Re-exports de services: consumidos como CONTROL._* pelos testes de
# integração (tests/test_gateway_status_unit.py, test_targets_api.py, ...).
from control.services.environment_service import (
    resolve_run_target_request as _resolve_run_target_request,
)
from control.services.analytics_scenario_service import (
    delete_analytics_scenario as _delete_analytics_scenario,
    list_analytics_scenarios as _list_analytics_scenarios,
    save_analytics_scenario as _save_analytics_scenario,
    set_analytics_scenario_favorite as _set_analytics_scenario_favorite,
)
from control.services.operational_scenario_service import (
    delete_operational_scenario as _delete_operational_scenario,
    instantiate_run_from_scenario as _instantiate_run_from_scenario,
    list_operational_scenarios as _list_operational_scenarios,
    save_operational_scenario as _save_operational_scenario,
    set_operational_scenario_favorite as _set_operational_scenario_favorite,
)
from control.services.report_format_service import (
    report_to_csv as _report_to_csv,
    report_to_markdown as _report_to_markdown,
)
from control.services.report_overview_service import (
    build_observability_overview as _build_observability_overview,
    build_reprocess_analytics as _build_reprocess_analytics,
    build_runs_trend_report as _build_runs_trend_report,
)
from control.services.report_run_service import (
    build_reprocess_trace as _build_reprocess_trace,
    build_run_comparison as _build_run_comparison,
    build_run_family as _build_run_family,
    build_run_report as _build_run_report,
    create_reprocess_run_from_failure as _create_reprocess_run_from_failure,
)
from control.services.gateway_observability_service import (
    read_gateway_monitor as _read_gateway_monitor,
    read_gateway_session_detail as _read_gateway_session_detail,
    read_gateway_sessions as _read_gateway_sessions,
)
from control.services.gateway_state_service import (
    auto_activate_gateway as _auto_activate_gateway_service,
    build_full_gateway_status as _build_full_gateway_status,
)
from control.services.knowledge_base_service import (
    build_knowledge_base_report as _build_knowledge_base_report,
)
from control.services.metrics_service import (
    collect_operational_metrics as _collect_operational_metrics,
)
from control.runtime_supervision import (
    Port22CaptureSampler,
    RuntimeContentCaptureRunner,
    default_project_root_from_file,
    env_bool as _runtime_env_bool,
    reconcile_gateway_capture_startup,
)
from control.services.benchmark_service import BenchmarkSupervisor
from control.page_state_builders import (
    build_audit_state as _build_audit_state_helper,
    build_business_rules_state as _build_business_rules_state_helper,
    build_journeys_report_state as _build_journeys_report_state_helper,
    build_pipeline_last_state as _build_pipeline_last_state_helper,
    infer_program_purpose as _infer_program_purpose_helper,
)
from control.audit_scan_support import (
    analyze_menus as _analyze_menus_helper,
    analyze_remote_navigation as _analyze_remote_navigation_helper,
    infer_module_label as _infer_module_label_helper,
    scan_local_sistema as _scan_local_sistema_helper,
)
from control.auth_support import (
    authenticate_request as _authenticate_request,
    clear_cookie as _clear_cookie_helper,
    get_cookie as _get_cookie_helper,
    require_page_user as _require_page_user_helper,
    require_user as _require_user_helper,
    reset_auth_cache as _reset_auth_cache_helper,
    set_cookie as _set_cookie_helper,
)
from control.engineering_route_support import (
    handle_engineering_api_get_route,
    handle_engineering_page_get_route,
)
from control.server_support import (
    BodyTooLargeError,
    InvalidContentLengthError,
    gateway_service_status as _support_gateway_service_status,
    gateway_toggle as _support_gateway_toggle,
    is_weak_password as _support_is_weak_password,
    read_json as _support_read_json,
    run_cmd as _support_run_cmd,
)
from control.error_middleware import error_guard
from control.websocket_support import (
    ws_handshake,
    ws_recv_frame,
    ws_send_pong,
    get_broadcaster,
    shutdown_broadcaster as _shutdown_broadcaster,
)


def _is_weak_password(password: str) -> bool:
    return _support_is_weak_password(password)

def _read_json(req: BaseHTTPRequestHandler) -> dict:
    return _support_read_json(req)

def _run_cmd(cmd: list[str]) -> tuple[int, str]:
    return _support_run_cmd(cmd)


def _env_bool(name: str, default: bool = False) -> bool:
    return _runtime_env_bool(name, default)


# Caminho do banco em uso pelo servidor (definido no main) — usado para
# localizar o socket do capture-daemon (<dir do replay.db>/daemon/capture.sock).
_SERVER_DB_PATH = ""


def _gateway_service_status() -> dict:
    from dakota_gateway.capture_daemon import default_socket_path

    daemon_socket = default_socket_path(_SERVER_DB_PATH) if _SERVER_DB_PATH else ""
    return _support_gateway_service_status(
        run_cmd_fn=lambda cmd: _run_cmd(cmd),
        daemon_socket_path=daemon_socket,
    )


def _full_gateway_status(con) -> dict:
    """Status completo do gateway (payload unificado com /api/gateway/status)."""
    return _build_full_gateway_status(con, _gateway_service_status())


def _gateway_toggle(enabled: bool) -> dict:
    return _support_gateway_toggle(
        enabled,
        run_cmd_fn=lambda cmd: _run_cmd(cmd),
        service_status_fn=_gateway_service_status,
    )


class _Port22CaptureSampler(Port22CaptureSampler):
    def __init__(self):
        super().__init__(run_cmd=lambda cmd: _run_cmd(cmd))


class _RuntimeContentCaptureRunner(RuntimeContentCaptureRunner):
    def __init__(self, *, project_root: str, hmac_key_file: str = ""):
        super().__init__(
            project_root=project_root,
            hmac_key_file=hmac_key_file,
            env_bool_fn=_env_bool,
        )


class ControlServer(ThreadingHTTPServer):
    # Backlog de accept maior que o default (5) da stdlib: com o limite de
    # conexões ativo, conexões na fila do SO aguardam um slot em vez de serem
    # derrubadas por backlog cheio.
    request_queue_size = 128

    def __init__(
        self,
        addr,
        handler,
        *,
        db_path: str,
        cookie_secret: bytes,
        hmac_key: bytes,
        capture_log_dir: str = "",
        hmac_key_file: str = "",
        gateway_auto_activate: bool = False,
        benchmark_artifacts_dir: str = "",
    ):
        super().__init__(addr, handler)
        self.db_path = db_path
        self.cookie_secret = cookie_secret
        self.hmac_key = hmac_key
        # ── Limite de conexões simultâneas (threads) ──
        # ThreadingHTTPServer cria uma thread por conexão SEM teto; N conexões
        # lentas/travadas esgotam o processo. O semáforo limita a cota de
        # conexões curtas: sem slot livre, process_request responde 503 e fecha
        # (fail-fast — bloquear o accept pararia também o shutdown).
        # Conexões WebSocket (/ws/*) são longas por natureza e NÃO contam na
        # cota: ao concluir o upgrade, o handler devolve o slot via
        # release_connection_slot() — é a cota separada das conexões longas.
        # DAKOTA_HTTP_MAX_CONNECTIONS (default 128; 0 = sem limite).
        try:
            max_connections = int(os.environ.get("DAKOTA_HTTP_MAX_CONNECTIONS", "128") or 128)
        except ValueError:
            max_connections = 128
        self.max_connections = max_connections
        self._conn_slots: threading.BoundedSemaphore | None = (
            threading.BoundedSemaphore(max_connections) if max_connections > 0 else None
        )
        self._detached_lock = threading.Lock()
        self._detached_requests: set[int] = set()
        self.capture_log_dir = capture_log_dir or os.path.join(os.path.dirname(db_path), "captures")
        os.makedirs(self.capture_log_dir, exist_ok=True)
        self.db_pool = ConnectionPool(db_path, min_size=1, max_size=16)
        con = self.db_pool.acquire()
        try:
            init_db(con)
            startup_state = reconcile_gateway_capture_startup(
                con,
                capture_log_dir=self.capture_log_dir,
                now_ms_fn=now_ms,
            )
            stale = int(startup_state.get("stale_captures_interrupted") or 0)
            if stale:
                log.info("[startup] %s captura(s) ativa(s) marcada(s) como interrupted (processo anterior encerrado)", stale)
            resumed = startup_state.get("resumed_capture")
            if resumed:
                log.info(
                    "[startup] captura retomada automaticamente para gateway ativo "
                    "(capture_id=%s)", int(resumed.get('id') or 0)
                )
            # Auto-ativação do gateway no boot
            if gateway_auto_activate and not resumed:
                self._auto_activate_gateway(con)
        finally:
            self.db_pool.release(con)
        # Injeta conexao DB no modulo de auditoria para persistencia
        con2 = self.db_pool.acquire()
        try:
            set_db_pool(self.db_pool)
        finally:
            self.db_pool.release(con2)
        self.runner = Runner(db_path, hmac_key)
        self.port22_sampler = _Port22CaptureSampler()
        # Rate limiting por IP para /api/* (X2) — None quando
        # DAKOTA_RATE_LIMIT=0. /api/login tem throttle próprio mais estrito.
        self.rate_limiter = _rate_limiter_from_env()
        # Sampler de recursos do host (CPU/mem/load/disco) para o painel
        # /observability/resources e comparação de estresse entre ambientes.
        self.host_metrics_sampler = None
        if os.environ.get("HOST_METRICS_ENABLED", "1") != "0":
            from dakota_gateway.host_metrics import HostMetricsSampler
            interval = float(os.environ.get("HOST_METRICS_INTERVAL_S", "5") or 5)
            retention = int(os.environ.get("HOST_METRICS_RETENTION_DAYS", "7") or 7)
            self.host_metrics_sampler = HostMetricsSampler(
                self.db_pool, interval_s=interval, retention_days=retention,
            )
            self.host_metrics_sampler.start()
            log.info("[startup] sampler de recursos do host ativo (intervalo=%ss, retencao=%sd)", interval, retention)
        # Janitor de caches de replay (X6): append em captura ativa muda a
        # capture_sig e torna o cache anterior órfão; varre na partida e a
        # cada REPLAY_CACHE_JANITOR_INTERVAL_S (default 3600; =0 desliga).
        self.replay_cache_janitor = None
        if os.environ.get("REPLAY_CACHE_JANITOR", "1") != "0":
            from control.services.replay_state_cache import CacheJanitor
            self.replay_cache_janitor = CacheJanitor(
                self.capture_log_dir,
                os.path.join(self.capture_log_dir, "replay_state_cache"),
                interval_s=float(os.environ.get("REPLAY_CACHE_JANITOR_INTERVAL_S", "3600") or 3600),
                logger=log,
            )
            self.replay_cache_janitor.start()
        # Boot com captura ativa (retomada ou recem auto-ativada acima): liga
        # o observador passivo da porta 22. Na ativacao via API/UI quem o liga
        # e a rota /api/gateway/activate; sem isto o boot deixava o sampler
        # desligado ate a proxima ativacao manual.
        con3 = self.db_pool.acquire()
        try:
            active_capture = query_one(
                con3,
                "SELECT id, session_uuid, log_dir FROM capture_sessions"
                " WHERE status='active' ORDER BY id DESC LIMIT 1",
                (),
            )
        finally:
            self.db_pool.release(con3)
        if active_capture:
            started = self.port22_sampler.start(
                {k: active_capture[k] for k in active_capture.keys()}
            )
            if started.get("started"):
                log.info(
                    "[startup] sampler da porta 22 ativo para a captura id=%s",
                    active_capture["id"],
                )
        self.runtime_capture = _RuntimeContentCaptureRunner(
            project_root=default_project_root_from_file(__file__),
            hmac_key_file=hmac_key_file,
        )
        # Execuções de benchmark real (§21): threads daemon supervisionadas,
        # artefatos em artifacts/benchmarks/<experiment_id>/.
        self.benchmark_artifacts_dir = benchmark_artifacts_dir or os.path.join(
            default_project_root_from_file(__file__), "artifacts", "benchmarks")
        self.benchmark_supervisor = BenchmarkSupervisor(
            db_path, self.benchmark_artifacts_dir)
        # Adoção de experimentos cujos artefatos vieram no tarball (§33):
        # sem isto a lista /api/benchmarks ficava vazia após deploy mesmo com
        # relatórios reais em artifacts/benchmarks/. Idempotente, nunca aborta
        # o boot.
        con4 = self.db_pool.acquire()
        try:
            from control.services.benchmark_service import (
                import_experiments_from_artifacts,
            )
            importacao = import_experiments_from_artifacts(
                con4, artifacts_dir=self.benchmark_artifacts_dir)
            if importacao.get("imported"):
                log.info(
                    "[startup] experimentos de benchmark adotados dos artefatos: %s",
                    ", ".join(importacao["imported"]),
                )
        except Exception:
            log.exception("[startup] falha na importação de benchmarks (não fatal)")
        finally:
            self.db_pool.release(con4)

    def process_request(self, request, client_address):
        """Admite a conexão somente se houver slot livre; senão 503 + fecha.

        O acquire é não-bloqueante de propósito: process_request roda no loop
        de accept e um acquire bloqueante congelaria também o shutdown().
        """
        slots = self._conn_slots
        if slots is None or slots.acquire(blocking=False):
            super().process_request(request, client_address)
            return
        log.warning("[limite] conexão recusada (503): %s conexões curtas ativas", self.max_connections)
        try:
            body = b'{"error":"servidor ocupado; tente novamente em breve"}'
            request.sendall(
                b"HTTP/1.1 503 Service Unavailable\r\n"
                b"Content-Type: application/json; charset=utf-8\r\n"
                b"Retry-After: 1\r\n"
                b"Connection: close\r\n"
                b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n\r\n" + body
            )
        except OSError:
            pass
        try:
            request.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        request.close()

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            slots = self._conn_slots
            if slots is not None:
                with self._detached_lock:
                    was_detached = id(request) in self._detached_requests
                    self._detached_requests.discard(id(request))
                # Conexão "detached" (WebSocket): o slot já foi devolvido no
                # upgrade — não liberar de novo.
                if not was_detached:
                    slots.release()

    def release_connection_slot(self, request) -> None:
        """Devolve o slot da cota de conexões curtas (upgrade WebSocket).

        A thread do handler segue viva cuidando da conexão longa, mas deixa de
        contar no limite — senão poucos clientes de tempo real esgotariam o
        servidor. Idempotente por conexão.
        """
        slots = self._conn_slots
        if slots is None:
            return
        with self._detached_lock:
            key = id(request)
            if key in self._detached_requests:
                return
            self._detached_requests.add(key)
        slots.release()

    def server_close(self):
        """Para os componentes de fundo antes de fechar o socket.

        Sem isto, cada instância vaza threads (host metrics a cada 5s, cache
        janitor, sampler da porta 22, runtime capture, broadcaster WebSocket)
        que seguem vivas após o teardown — em suítes que sobem dezenas de
        servidores, as threads residuais contendem no GIL e gravam em DBs já
        apagados (flake de timeout em requisição loopback na árvore extraída
        do release 0.8.7). Todos os stop() são idempotentes.
        """
        for attr in ("port22_sampler", "host_metrics_sampler",
                     "replay_cache_janitor", "runtime_capture"):
            component = getattr(self, attr, None)
            if component is None:
                continue
            try:
                component.stop()
            except Exception:
                pass
        try:
            _shutdown_broadcaster()
        except Exception:
            pass
        super().server_close()

    def _auto_activate_gateway(self, con):
        """Ativa o gateway automaticamente no boot (regra em gateway_state_service)."""
        _auto_activate_gateway_service(
            con,
            capture_log_dir=self.capture_log_dir,
            now_ms_fn=now_ms,
            log=log,
        )


class Handler(BaseHTTPRequestHandler):
    def handle_one_request(self):
        # O handler é reutilizado em keep-alive: o cache de autenticação é por
        # requisição — invalida antes de cada request para não reutilizar uma
        # sessão revogada/expirada da request anterior na mesma conexão.
        _reset_auth_cache_helper(self)
        super().handle_one_request()

    def _db(self):
        return self.server.db_pool.acquire()

    def _db_release(self, con):
        self.server.db_pool.release(con)

    def _build_business_rules_state(self) -> dict:
        return _build_business_rules_state_helper(self._db, self._db_release)

    def _build_pipeline_last_state(self) -> dict:
        return _build_pipeline_last_state_helper(self._db, self._db_release)

    def _build_audit_state(self) -> dict:
        local_sistema = self._scan_local_sistema()
        if not hasattr(Handler, "_remote_nav_cache"):
            Handler._remote_nav_cache = None  # type: ignore
        if Handler._remote_nav_cache is None:  # type: ignore
            try:
                Handler._remote_nav_cache = Handler._analyze_remote_navigation()  # type: ignore
            except Exception:
                Handler._remote_nav_cache = {"error": "falha na analise remota"}  # type: ignore
        return _build_audit_state_helper(
            self._db,
            self._db_release,
            local_sistema=local_sistema,
            remote_navigation=Handler._remote_nav_cache,  # type: ignore[arg-type]
            infer_program_purpose_fn=self._infer_program_purpose,
        )

    @staticmethod
    def _infer_program_purpose(filename: str) -> str:
        return _infer_program_purpose_helper(filename)

    @staticmethod
    def _analyze_remote_navigation() -> dict | None:
        return _analyze_remote_navigation_helper()

    @staticmethod
    def _scan_local_sistema() -> dict | None:
        if not hasattr(Handler, "_menu_analysis_cache"):
            Handler._menu_analysis_cache = None  # type: ignore

        def _cached_analyze_menus(base: str):
            cache = Handler._menu_analysis_cache  # type: ignore
            if cache is None:
                cache = Handler._analyze_menus(base)
                Handler._menu_analysis_cache = cache  # type: ignore
            return cache

        return _scan_local_sistema_helper(analyze_menus_fn=_cached_analyze_menus)

    @staticmethod
    def _analyze_menus(base_dir: str) -> dict:
        return _analyze_menus_helper(
            base_dir,
            infer_program_purpose_fn=Handler._infer_program_purpose,
            infer_module_label_fn=Handler._infer_module_label,
        )

    @staticmethod
    def _infer_module_label(code: str) -> str:
        return _infer_module_label_helper(code)

    def _build_journeys_report_state(self) -> dict:
        return _build_journeys_report_state_helper(self._db, self._db_release)

    def _set_cookie(self, name: str, value: str, max_age: int = 3600 * 12):
        _set_cookie_helper(self, name, value, max_age=max_age)

    def _clear_cookie(self, name: str):
        _clear_cookie_helper(self, name)

    @staticmethod
    def _is_relative_to(path: Path, parent: Path) -> bool:
        """is_relative_to polyfill (Python 3.9+)."""
        try:
            path.relative_to(parent)
            return True
        except ValueError:
            return False

    def _get_cookie(self, name: str) -> str | None:
        return _get_cookie_helper(self, name)

    def _auth(self):
        return _authenticate_request(self)

    def _require(self, roles: set[str] | None = None):
        return _require_user_helper(self, roles)

    def _require_page(self, roles: set[str] | None = None):
        return _require_page_user_helper(self, roles)

    def _serve_metrics(self):
        """Endpoint /metrics — metricas operacionais internas.

        Em producao (DAKOTA_ENV=production), exige autenticacao sempre.
        Em lab/homologacao, libera acesso de localhost (127.0.0.1).
        """
        dakota_env = os.environ.get("DAKOTA_ENV", "lab").strip().lower()
        client_host = self.client_address[0] if hasattr(self, 'client_address') else ""

        # Produção: sempre exige auth. Lab: só exige auth para acesso externo
        if dakota_env == "production" or client_host not in ("127.0.0.1", "::1", "localhost", ""):
            u = self._auth()
            if not u:
                write_json(self, 401, {"error": "autenticacao requerida para /metrics"})
                return
        try:
            con = self._db()
            try:
                metrics = _collect_operational_metrics(con)
            finally:
                self._db_release(con)
        except Exception:
            metrics = {"error": "db unavailable"}

        write_json(self, 200, metrics)

    def _handle_ws_gateway_status(self):
        """WebSocket /ws/gateway-status — push em tempo real do status completo (serviço + lógico)."""
        if not ws_handshake(self):
            self.send_response(400)
            self.end_headers()
            return
        # Conexão longa: devolve o slot da cota de conexões curtas
        # (DAKOTA_HTTP_MAX_CONNECTIONS) — ver ControlServer.release_connection_slot.
        release_slot = getattr(self.server, "release_connection_slot", None)
        if callable(release_slot):
            release_slot(self.connection)
        def full_status():
            con = self._db()
            try:
                return _full_gateway_status(con)
            finally:
                self._db_release(con)
        broadcaster = get_broadcaster(status_fn=full_status)
        broadcaster.add_client(self)
        try:
            while True:
                frame = ws_recv_frame(self)
                if frame is None:
                    break
                if frame["opcode"] == 0x8:  # close
                    break
                if frame["opcode"] == 0x9:  # ping → pong (RFC 6455)
                    ws_send_pong(self, frame.get("payload") or b"")
        finally:
            broadcaster.remove_client(self)

    def _serve_knowledge_base(self, p):
        """Endpoint /api/knowledge-base — P2-A (admin only)."""
        # Exige role admin
        u = self._require({"admin"})
        if not u:
            return

        params = parse_qs(p.query) if p.query else {}
        source_dir = params.get("source", [None])[0]

        status, payload = _build_knowledge_base_report(source_dir or "")
        if status == 200:
            write_json(self, 200, payload)
        else:
            write_json(self, status, payload)

    @staticmethod
    def _normalize_path(path: str) -> str:
        """Remove prefixo /v1 para versionamento transparente de API."""
        if path.startswith("/v1/"):
            return path[3:]  # /v1/api/... → /api/...
        return path

    # ── Guard central de autenticação (C1) ──
    # Rotas públicas explícitas: login, assets estáticos (página de login),
    # healthchecks e /metrics (que tem política própria de auth em produção).
    _PUBLIC_EXACT_PATHS = frozenset({
        "/health",
        "/ready",
        "/login",
        "/api/login",
        "/metrics",
    })
    _PUBLIC_PREFIXES = ("/assets/",)

    def _api_auth_guard(self, path: str) -> bool:
        """Guard estrutural: todo /api/* e /ws/* exige sessão autenticada.

        Retorna True se a requisição pode prosseguir; caso contrário já
        respondeu 401 com corpo JSON. Roles específicas (admin/operator)
        continuam sendo verificadas pelos próprios endpoints via _require().
        """
        if path in self._PUBLIC_EXACT_PATHS:
            return True
        if any(path.startswith(prefix) for prefix in self._PUBLIC_PREFIXES):
            return True
        if not (path.startswith("/api/") or path.startswith("/ws/")):
            # Páginas HTML têm guard próprio (redirect para /login).
            return True
        if self._auth():
            return True
        write_json(self, 401, {"error": "autenticacao requerida"})
        return False

    def _rate_limit_guard(self, path: str) -> bool:
        """Rate limit por IP para /api/* (X2). /api/login é excluído — já tem
        throttle próprio mais estrito por (IP, username) em admin_routes.

        Retorna True se a requisição pode prosseguir; caso contrário já
        respondeu 429 com Retry-After. Roda ANTES do auth guard para conter
        também tráfego abusivo não autenticado.
        """
        limiter = getattr(self.server, "rate_limiter", None)
        if limiter is None or not path.startswith("/api/") or path == "/api/login":
            return True
        client_host = self.client_address[0] if hasattr(self, "client_address") else ""
        if limiter.allow(client_host or "unknown"):
            return True
        retry_after = limiter.retry_after(client_host or "unknown")
        body = json.dumps(
            {"error": "limite de requisições excedido; tente novamente em breve"},
            ensure_ascii=False,
        ).encode("utf-8")
        self.send_response(429)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Retry-After", str(retry_after))
        self.end_headers()
        self.wfile.write(body)
        return False

    @error_guard
    def do_GET(self):
        p = urlparse(self.path)
        p = p._replace(path=self._normalize_path(p.path))

        if not self._rate_limit_guard(p.path):
            return
        if not self._api_auth_guard(p.path):
            return

        # ── WebSocket gateway status (tempo real) ──
        if p.path == "/ws/gateway-status":
            self._handle_ws_gateway_status()
            return

        # ── Healthcheck endpoints (sem auth) ──
        if p.path == "/health":
            version = "0.1.0"
            try:
                vf = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "VERSION")
                with open(vf) as f:
                    version = f.readline().strip()
            except Exception:
                pass
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok", "service": "replay2-control", "version": version}).encode("utf-8"))
            return
        if p.path == "/ready":
            try:
                con = self._db()
                try:
                    con.execute("SELECT 1")
                finally:
                    # release em finally: query quebrada não pode prender a
                    # conexão no pool (vazamento a cada /ready com erro).
                    self._db_release(con)
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ready", "db": "ok"}).encode("utf-8"))
            except Exception as exc:
                self.send_response(503)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "not_ready", "db": str(exc)}).encode("utf-8"))
            return
        if p.path == "/metrics":
            self._serve_metrics()
            return

        if handle_engineering_api_get_route(self, p, db_acquire=self._db, db_release=self._db_release):
            return
        if handle_engineering_page_get_route(self, p):
            return

        if handle_ui_get_route(self, p):
            return
        if p.path == "/api/me":
            u = self._require()
            if not u:
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(u).encode("utf-8"))
            return
        if handle_admin_get_route(self, p, gateway_service_status=_gateway_service_status, query_all_fn=query_all):
            return
        if handle_catalog_get_route(self, p):
            return
        if handle_observability_get_route(self, p):
            return
        if handle_operational_get_route(self, p):
            return
        if handle_gateway_get_route(
            self,
            p,
            query_one_fn=query_one,
            read_gateway_monitor_fn=_read_gateway_monitor,
            read_gateway_sessions_fn=_read_gateway_sessions,
            read_gateway_session_detail_fn=_read_gateway_session_detail,
        ):
            return
        if handle_capture_get_route(
            self,
            p,
            read_gateway_monitor_fn=_read_gateway_monitor,
        ):
            return
        if handle_run_get_route(self, p):
            return
        if handle_journey_get_route(self, p):
            return
        if handle_synthetic_get_route(self, p):
            return
        if handle_benchmark_get_route(self, p):
            return

        # ── P2-A: Knowledge Base API ──
        if p.path == "/api/knowledge-base" or p.path.startswith("/api/knowledge-base/"):
            self._serve_knowledge_base(p)
            return

        self.send_response(404)
        self.end_headers()

    @error_guard
    def do_POST(self):
        p = urlparse(self.path)
        p = p._replace(path=self._normalize_path(p.path))

        if not self._rate_limit_guard(p.path):
            return
        # Guard de autenticação antes de ler o corpo (M5/C1).
        if not self._api_auth_guard(p.path):
            return

        try:
            body = _read_json(self)
        except BodyTooLargeError:
            write_json(self, 413, {"error": "corpo da requisicao excede o limite permitido"})
            return
        except InvalidContentLengthError:
            write_json(self, 400, {"error": "Content-Length invalido"})
            return
        if handle_admin_post_route(
            self,
            p,
            body,
            auth_module=auth,
            query_one_fn=query_one,
            now_ms_fn=now_ms,
            gateway_toggle_fn=_gateway_toggle,
        ):
            return

        if handle_gateway_post_route(
            self,
            p,
            body,
            now_ms_fn=now_ms,
            capture_log_dir=self.server.capture_log_dir,
            start_port22_capture_fn=self.server.port22_sampler.start,
            stop_port22_capture_fn=self.server.port22_sampler.stop,
            start_runtime_capture_fn=self.server.runtime_capture.start,
            stop_runtime_capture_fn=self.server.runtime_capture.stop,
        ):
            return
        if handle_capture_post_route(
            self,
            p,
            body,
            now_ms_fn=now_ms,
            log_dir_base=self.server.capture_log_dir,
        ):
            return
        if handle_run_post_route(self, p, body):
            return
        if handle_journey_post_route(self, p, body):
            return
        if handle_synthetic_post_route(self, p, body):
            return
        if handle_benchmark_post_route(self, p, body):
            return
        if handle_observability_post_route(self, p, body):
            return
        if handle_catalog_post_route(self, p, body):
            return
        if handle_operational_post_route(self, p, body):
            return

        self.send_response(404)
        self.end_headers()

    @error_guard
    def do_DELETE(self):
        p = urlparse(self.path)
        p = p._replace(path=self._normalize_path(p.path))
        if not self._rate_limit_guard(p.path):
            return
        if not self._api_auth_guard(p.path):
            return
        if handle_run_delete_route(self, p):
            return
        if handle_capture_delete_route(self, p):
            return
        if handle_catalog_delete_route(self, p):
            return
        if handle_observability_delete_route(self, p):
            return
        if handle_operational_delete_route(self, p):
            return

        self.send_response(404)
        self.end_headers()

    def log_message(self, fmt, *args):
        return


def _load_control_env() -> None:
        """Carrega gateway/control.env (KEY=VALUE) para os.environ, se existir.

        É o arquivo de configuração operacional persistente do servidor (ex.:
        DAKOTA_SOURCE_ROOT no MIG24). Não vem no tarball e sobrevive a deploys.
        Variáveis já presentes no ambiente têm precedência (setdefault).
        """
        env_file = Path(__file__).resolve().parents[1] / "control.env"
        try:
                lines = env_file.read_text(encoding="utf-8").splitlines()
        except OSError:
                return
        for line in lines:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                        continue
                key, _, value = line.partition("=")
                key = key.strip()
                if key:
                        os.environ.setdefault(key, value.strip())


def main():
        _load_control_env()
        ap = argparse.ArgumentParser()
        ap.add_argument("--listen", default="127.0.0.1:8090")
        ap.add_argument("--db", default="")
        ap.add_argument("--capture-log-dir", default="")
        ap.add_argument("--cookie-secret-file", required=True)
        ap.add_argument("--hmac-key-file", required=True)
        ap.add_argument("--bootstrap-admin", default="")  # username:password
        ap.add_argument("--gateway-auto-activate", action="store_true",
                        help="Ativa o gateway automaticamente no boot do servidor")
        args = ap.parse_args()

        host, port_s = args.listen.rsplit(":", 1)
        port = int(port_s)
        db_path = args.db or (Path(__file__).resolve().parents[1] / "state" / "replay.db")
        db_path = str(db_path)
        global _SERVER_DB_PATH
        _SERVER_DB_PATH = db_path

        cookie_secret = Path(args.cookie_secret_file).read_bytes().strip()
        hmac_key = Path(args.hmac_key_file).read_bytes().strip()
        if not cookie_secret:
                raise SystemExit("cookie secret vazio")
        if not hmac_key:
                raise SystemExit("hmac key vazio")

        # ── DAKOTA_ENV: modo de operação ──
        dakota_env = os.environ.get("DAKOTA_ENV", "lab").strip().lower()
        if dakota_env not in ("lab", "production", "homologation"):
                log.warning("DAKOTA_ENV='%s' desconhecido. Use: lab, production, homologation", dakota_env)
                dakota_env = "lab"

        if dakota_env == "production":
                log.warning("MODO PRODUCAO ATIVO — seguranca reforcada")
                if not os.environ.get("DAKOTA_ADMIN"):
                        raise SystemExit(
                            "DAKOTA_ENV=production requer DAKOTA_ADMIN definido.\n"
                            "Defina via: export DAKOTA_ADMIN='admin:senha-forte'"
                        )
        else:
                log.info("Modo: %s", dakota_env)

        env_admin = os.environ.get("DAKOTA_ADMIN", "").strip()
        bootstrap_admin = (args.bootstrap_admin or env_admin).strip()
        if args.bootstrap_admin and env_admin:
                log.warning("DAKOTA_ADMIN ignorada porque --bootstrap-admin foi informado.")

        con = connect(db_path)
        init_db(con)
        log.info("Banco inicializado: %s", db_path)

        existing_admin = con.execute(
                "SELECT username FROM users WHERE role='admin' ORDER BY id LIMIT 1"
        ).fetchone()
        if existing_admin:
                log.info("Admin ja existente: %s", existing_admin["username"])
        elif bootstrap_admin:
                if ":" not in bootstrap_admin:
                        raise SystemExit("bootstrap admin deve ser username:password (via --bootstrap-admin ou DAKOTA_ADMIN)")
                u, p = bootstrap_admin.split(":", 1)
                u = u.strip()
                if not u:
                        raise SystemExit("bootstrap admin inválido: username vazio")
                if _is_weak_password(p):
                        log.warning("Senha de bootstrap parece fraca. Use uma senha forte em producao.")
                ph = auth.pbkdf2_hash_password(p)
                con.execute(
                        "INSERT INTO users(username,password_hash,role,created_at_ms) VALUES(?,?, 'admin', ?)",
                        (u, ph, now_ms()),
                )
                log.info("Admin criado: %s", u)
        else:
                log.warning("Admin nao criado: informe --bootstrap-admin ou DAKOTA_ADMIN para bootstrap inicial.")

        # ── Bootstrap: cria perfil de conexão padrão se não existir ──
        existing_profile = con.execute(
                "SELECT id FROM connection_profiles WHERE profile_id='default' LIMIT 1"
        ).fetchone()
        if not existing_profile:
                ts = now_ms()
                con.execute(
                        "INSERT INTO connection_profiles"
                        " (profile_id, name, transport, port, auth_mode, created_at_ms, updated_at_ms)"
                        " VALUES ('default', 'SSH Direto (padrão)', 'ssh', 22, 'external', ?, ?)",
                        (ts, ts),
                )
                log.info("[bootstrap] perfil de conexao 'default' criado (SSH porta 22)")
        con.close()

        # Auto-ativação: flag CLI ou env var DAKOTA_GATEWAY_AUTO_ACTIVATE=true
        gateway_auto_activate = args.gateway_auto_activate or os.environ.get("DAKOTA_GATEWAY_AUTO_ACTIVATE", "").strip().lower() == "true"

        srv = ControlServer(
                (host, port),
                Handler,
                db_path=db_path,
                cookie_secret=cookie_secret,
                hmac_key=hmac_key,
                capture_log_dir=args.capture_log_dir,
                hmac_key_file=args.hmac_key_file,
                gateway_auto_activate=gateway_auto_activate,
        )
        log.info("listening on http://%s:%s", host, port)
        srv.serve_forever()


if __name__ == "__main__":
    main()
