"""Rate limiting simples por chave (IP) para os endpoints /api/* (dívida X2).

Janela fixa em memória: cada chave tem até `max_requests` requisições por
`window_seconds`. O login (`/api/login`) NÃO usa este limiter — ele já tem
throttle próprio mais estrito por (IP, username) em `admin_routes.py`.

Configuração por ambiente:
- `DAKOTA_RATE_LIMIT_RPM` — requisições por minuto por IP (default 600);
- `DAKOTA_RATE_LIMIT=0` — desliga o limiter (dev/testes controlados).

O default (600 rpm = 10 rps sustentados) é generoso para a UI (polling de
poucos endpoints a cada vários segundos) e só dispara em abuso real.
"""
from __future__ import annotations

import os
import threading
import time

_DEFAULT_RPM = 600
# Poda preguiçosa: quando o mapa passa deste tamanho, entradas com janela
# vencida são removidas no próximo allow() — memória limitada sem varredura
# periódica.
_MAX_KEYS = 10000


class RateLimiter:
    """Janela fixa por chave, thread-safe (ThreadingHTTPServer)."""

    def __init__(self, max_requests: int = _DEFAULT_RPM,
                 window_seconds: float = 60.0) -> None:
        self.max_requests = max(1, int(max_requests))
        self.window_seconds = float(window_seconds)
        self._hits: dict[str, list] = {}  # key -> [window_start, count]
        self._lock = threading.Lock()

    def allow(self, key: str, now: float | None = None) -> bool:
        """Registra uma requisição; True se dentro do limite."""
        now = time.time() if now is None else float(now)
        with self._lock:
            entry = self._hits.get(key)
            if entry is None or now - entry[0] >= self.window_seconds:
                self._hits[key] = [now, 1]
            elif entry[1] >= self.max_requests:
                return False
            else:
                entry[1] += 1
            if len(self._hits) > _MAX_KEYS:
                self._purge_locked(now)
            return True

    def retry_after(self, key: str, now: float | None = None) -> int:
        """Segundos até a janela da chave abrir de novo (mínimo 1)."""
        now = time.time() if now is None else float(now)
        with self._lock:
            entry = self._hits.get(key)
            if entry is None:
                return 1
            return max(1, int(self.window_seconds - (now - entry[0])) + 1)

    def _purge_locked(self, now: float) -> None:
        stale = [k for k, v in self._hits.items()
                 if now - v[0] >= self.window_seconds]
        for k in stale:
            del self._hits[k]


def from_env(environ: dict | None = None) -> RateLimiter | None:
    """Cria o limiter a partir do ambiente; None = desabilitado."""
    env = os.environ if environ is None else environ
    if str(env.get("DAKOTA_RATE_LIMIT", "1")).strip() == "0":
        return None
    rpm = int(str(env.get("DAKOTA_RATE_LIMIT_RPM", _DEFAULT_RPM)).strip() or _DEFAULT_RPM)
    return RateLimiter(max_requests=rpm, window_seconds=60.0)
