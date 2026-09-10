"""Sincronização e evidência de type-ahead (§7, §24).

Princípio central: **quiet point ≠ convergência de estado**. Um período sem
bytes não prova que o ERP terminou ("aguarde..." → silêncio → continuação).
Este módulo separa os dois conceitos:

- :func:`quiet_points` localiza janelas de silêncio (úteis para saber quando
  o terminal estava ocupado);
- :func:`extract_typeahead_evidence` identifica, em capturas humanas, inputs
  enviados ANTES do quiet point da resposta anterior — evidência objetiva de
  type-ahead — e só marca como ``safe`` quando há critérios objetivos:
  sem checkpoint intermediário obrigatório, com resposta posterior do
  sistema e fim de sessão limpo (convergência observável), tudo rastreável
  à captura (sessão/seqs/timestamps).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TypeaheadEvidence:
    """Evidência auditável de type-ahead humano (§7: safe_typeahead_evidence)."""

    session_id: str
    input_seq: int          # seq_global do input enviado durante output
    prev_input_seq: int     # seq_global do input anterior
    input_ts: int           # ts_ms do input type-ahead
    quiet_point_ts: int     # ts em que o quiet point ocorreria (last_out + stable_ms)
    output_active: bool     # havia output em curso quando o input foi enviado
    checkpoint_between: bool
    converged: bool         # sistema respondeu depois e a sessão terminou limpa
    safe: bool
    stable_ms: int

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "input_seq": self.input_seq,
            "prev_input_seq": self.prev_input_seq,
            "input_ts": self.input_ts,
            "quiet_point_ts": self.quiet_point_ts,
            "output_active": self.output_active,
            "checkpoint_between": self.checkpoint_between,
            "converged": self.converged,
            "safe": self.safe,
            "stable_ms": self.stable_ms,
        }


def _is_out(ev: dict) -> bool:
    return ev.get("type") == "bytes" and ev.get("dir") == "out"


def _is_in(ev: dict) -> bool:
    return ev.get("type") in ("bytes", "deterministic_input") and (
        ev.get("dir") == "in" or ev.get("type") == "deterministic_input"
    )


def quiet_points(events: list[dict], *, stable_ms: int) -> list[tuple[int, int]]:
    """Retorna [(last_out_ts, quiet_ts)] — pontos onde o output ficou quieto.

    Um quiet point é apenas ``last_out_ts + stable_ms``: o instante a partir
    do qual o terminal estava em silêncio há ``stable_ms``. NÃO implica
    convergência de estado — decisões críticas devem preferir espera por
    estado esperado (checkpoint), não por silêncio.
    """
    points: list[tuple[int, int]] = []
    out_ts = [int(ev.get("ts_ms") or 0) for ev in events if _is_out(ev)]
    if not out_ts:
        return points
    for i, ts in enumerate(out_ts):
        nxt = out_ts[i + 1] if i + 1 < len(out_ts) else None
        if nxt is None or nxt - ts >= stable_ms:
            points.append((ts, ts + stable_ms))
    return points


def extract_capture_typeahead_evidence(
    log_dir: str,
    params: dict | None = None,
    *,
    stable_ms: int = 150,
) -> dict[str, list[int]]:
    """Extrai evidência segura de type-ahead de uma captura inteira.

    Uma sessão por vez é materializada (índice de offsets +
    ``iter_indexed_events``) — memória limitada em captures grandes, mesma
    disciplina dos executors. Retorna ``{session_id: [seq_global seguros]}``
    pronto para ``params.typeahead_evidence`` / ``AdaptiveRuntime``.

    Só seqs com ``safe=True`` entram (§7: sem checkpoint intermediário, com
    resposta posterior e fim de sessão limpo). Falhas de leitura de uma
    sessão apenas a excluem do mapa (a evidência é otimização, nunca
    requisito — sem evidência o executor segue conservador).
    """
    from .window import index_session_events, iter_indexed_events

    index, _starts = index_session_events(log_dir, params)
    evidence: dict[str, list[int]] = {}
    for sid in sorted(index):
        try:
            events = list(iter_indexed_events(index[sid]))
        except Exception:
            continue
        safe_seqs = [
            ev.input_seq
            for ev in extract_typeahead_evidence(events, stable_ms=stable_ms)
            if ev.safe
        ]
        if safe_seqs:
            evidence[str(sid)] = safe_seqs
    return evidence


def extract_typeahead_evidence(
    events: list[dict],
    *,
    stable_ms: int = 150,
) -> list[TypeaheadEvidence]:
    """Extrai evidências de type-ahead de uma sessão de captura.

    ``events`` deve conter os eventos ordenados de UMA sessão (bytes in/out,
    checkpoints, session_end). Um input é type-ahead quando ocorre antes do
    quiet point do output em curso (``input_ts < last_out_ts + stable_ms``
    com ``last_out_ts >= prev_input_ts``). A evidência só é ``safe`` quando:

    - não há checkpoint entre o input anterior e este;
    - o sistema respondeu depois do input (output posterior existe);
    - a sessão terminou com ``session_end`` (convergência observável);
    - o input anterior existia (sequência vista, não primeira ação solta).
    """
    evidences: list[TypeaheadEvidence] = []
    last_out_ts: int | None = None
    prev_input: dict | None = None
    prev_input_idx: int = -1

    # próximo output depois de cada posição (prova de resposta do sistema)
    next_out_idx: dict[int, int] = {}
    nxt: int | None = None
    for i in range(len(events) - 1, -1, -1):
        if nxt is not None:
            next_out_idx[i] = nxt
        if _is_out(events[i]):
            nxt = i

    session_ended = any(ev.get("type") == "session_end" for ev in events)

    def checkpoint_in_window(start_idx: int, end_idx: int) -> bool:
        return any(
            events[j].get("type") == "checkpoint"
            for j in range(start_idx + 1, end_idx + 1)
        )

    for i, ev in enumerate(events):
        if _is_out(ev):
            last_out_ts = int(ev.get("ts_ms") or 0)
            continue
        if not _is_in(ev):
            continue
        ts = int(ev.get("ts_ms") or 0)
        seq = int(ev.get("seq_global") or 0)
        if prev_input is not None and last_out_ts is not None:
            prev_ts = int(prev_input.get("ts_ms") or 0)
            output_active = last_out_ts >= prev_ts and ts < last_out_ts + stable_ms
            if output_active:
                response_idx = next_out_idx.get(i)
                converged = response_idx is not None and session_ended
                # checkpoint obrigatório entre a ação anterior e a resposta
                # que prova convergência invalida a segurança do type-ahead
                cp_between = checkpoint_in_window(
                    prev_input_idx, response_idx if response_idx is not None else i,
                )
                safe = converged and not cp_between
                evidences.append(TypeaheadEvidence(
                    session_id=str(ev.get("session_id") or ""),
                    input_seq=seq,
                    prev_input_seq=int(prev_input.get("seq_global") or 0),
                    input_ts=ts,
                    quiet_point_ts=last_out_ts + stable_ms,
                    output_active=True,
                    checkpoint_between=cp_between,
                    converged=converged,
                    safe=safe,
                    stable_ms=stable_ms,
                ))
        prev_input = ev
        prev_input_idx = i

    return evidences
