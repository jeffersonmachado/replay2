"""Perfil de latência local por ambiente (§13, §14).

Estatísticas locais (sem ML, sem rede) de quanto cada classe de ação leva
para convergir em cada ambiente, com contexto de tela/programa. Serve para
observabilidade, detecção de degradação e timeouts racionais — **nunca**
usando p50 como timeout: a sugestão usa a cauda (p99) com fator de segurança
e piso configurável.

Os perfis são estritamente isolados por ``environment_id``: amostras do AIX
nunca misturam com as do Linux (§14 — o comparativo AIX/POWER × Linux/x86
depende disso). Persistência em JSON local (offline).
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path


class LatencyProfile:
    """Perfil estatístico local de latência, isolado por ambiente."""

    def __init__(self, path: str | None = None, *, max_samples: int = 2048) -> None:
        self._path = path or ""
        self._max_samples = max(16, int(max_samples))
        self._lock = threading.Lock()
        # (environment_id, action_class, context) -> lista de amostras (ms)
        self._samples: dict[tuple[str, str, str], list[float]] = {}
        if self._path and Path(self._path).exists():
            self._load()

    # ------------------------------------------------------------------
    # Registro e consulta
    # ------------------------------------------------------------------

    @staticmethod
    def _key(environment_id: str, action_class: str, context: str) -> tuple[str, str, str]:
        return (str(environment_id or "unknown"), str(action_class or "unknown"),
                str(context or ""))

    def record(
        self,
        environment_id: str,
        action_class: str,
        context: str,
        elapsed_ms: float,
    ) -> None:
        key = self._key(environment_id, action_class, context)
        with self._lock:
            samples = self._samples.setdefault(key, [])
            samples.append(float(elapsed_ms))
            if len(samples) > self._max_samples:
                # reservoir: descarta o mais antigo (janela deslizante)
                del samples[: len(samples) - self._max_samples]

    def sample_count(self, environment_id: str, action_class: str, context: str) -> int:
        with self._lock:
            return len(self._samples.get(
                self._key(environment_id, action_class, context), [],
            ))

    @staticmethod
    def _percentile(sorted_samples: list[float], pct: float) -> float:
        if not sorted_samples:
            return 0.0
        if len(sorted_samples) == 1:
            return sorted_samples[0]
        rank = (pct / 100.0) * (len(sorted_samples) - 1)
        low = int(rank)
        high = min(low + 1, len(sorted_samples) - 1)
        frac = rank - low
        return sorted_samples[low] + (sorted_samples[high] - sorted_samples[low]) * frac

    def stats(
        self,
        environment_id: str,
        action_class: str,
        context: str = "",
    ) -> dict | None:
        with self._lock:
            samples = list(self._samples.get(
                self._key(environment_id, action_class, context), [],
            ))
        if not samples:
            return None
        ordered = sorted(samples)
        count = len(ordered)
        mean = sum(ordered) / count
        variance = sum((v - mean) ** 2 for v in ordered) / count
        return {
            "environment_id": str(environment_id or "unknown"),
            "action_class": str(action_class or "unknown"),
            "context": str(context or ""),
            "count": count,
            "min": ordered[0],
            "p50": self._percentile(ordered, 50),
            "p95": self._percentile(ordered, 95),
            "p99": self._percentile(ordered, 99),
            "max": ordered[-1],
            "mean": mean,
            "variance": variance,
            "last_updated": int(time.time() * 1000),
        }

    def suggested_timeout_ms(
        self,
        environment_id: str,
        action_class: str,
        context: str = "",
        *,
        floor_ms: int = 1000,
        factor: float = 2.0,
    ) -> int:
        """Timeout racional: cauda (p99) × fator, com piso — nunca p50."""
        stats = self.stats(environment_id, action_class, context)
        if not stats:
            return int(floor_ms)
        return int(max(floor_ms, stats["p99"] * max(1.0, factor)))

    def environments(self) -> list[str]:
        with self._lock:
            return sorted({key[0] for key in self._samples})

    # ------------------------------------------------------------------
    # Persistência local (JSON; offline)
    # ------------------------------------------------------------------

    def save(self) -> None:
        if not self._path:
            return
        with self._lock:
            payload = {
                "version": 1,
                "samples": [
                    {
                        "environment_id": key[0],
                        "action_class": key[1],
                        "context": key[2],
                        "values": values,
                    }
                    for key, values in sorted(self._samples.items())
                ],
            }
        path = Path(self._path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    def _load(self) -> None:
        try:
            payload = json.loads(Path(self._path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for entry in payload.get("samples", []):
            key = self._key(
                entry.get("environment_id", ""),
                entry.get("action_class", ""),
                entry.get("context", ""),
            )
            values = [float(v) for v in entry.get("values", [])][-self._max_samples:]
            if values:
                self._samples[key] = values
