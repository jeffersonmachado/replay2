"""Fachada do pacote replay_control (decomposição do módulo monolítico).

Reexporta todos os nomes que eram importáveis do antigo
``dakota_gateway/replay_control.py`` — funções, classes e símbolos
reexportados de módulos vizinhos — para preservar a superfície de
importação dos consumidores existentes.

Submódulos:

- ``window.py`` — helpers de janela/hash/params;
- ``deterministic.py`` — comparação determinística;
- ``executors.py`` — executores de replay (strict-global, parallel-sessions,
  parallel-sessions-concurrent) e ``LoadTestParams``;
- ``runner.py`` — ciclo de vida de runs (create/pause/resume/cancel/retry) e
  a classe ``Runner``.
"""
from __future__ import annotations

import hashlib
import json
import random
import selectors
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Lock, Semaphore, Thread

from . import deterministic, executors, runner, window
from .deterministic import (
    _comparison_mode_from_params,
    _deterministic_failure,
    _event_requires_deterministic_comparison,
    _expected_snapshot_from_event,
    _legacy_checkpoint_expected,
    _match_failure_values,
    _observed_snapshot_from_session,
    _session_start_by_id,
    _should_apply_deterministic_input,
    _state_for_session,
    _wait_for_expected_observed,
    compare_expected_observed,
    stale_reference_override,
    context_switch_override,
    content_present_override,
    synthetic_swap_override,
)
from .executors import (
    LoadTestParams,
    _soft_checkpoint_match,
    replay_parallel_sessions_concurrent_controlled,
    replay_parallel_sessions_controlled,
    replay_strict_global_controlled,
)
from .runner import (
    Runner,
    _RunControlState,
    cancel_run,
    create_run,
    pause_run,
    resume_run,
    retry_run,
    set_run_compliance,
)
from .window import (
    _event_in_replay_window,
    _first_session_start,
    _is_replay_input_event,
    _iter_events,
    _normalize_replay_window_params,
    _on_deterministic_mismatch,
    _replay_input_mode,
    _resolve_replay_window,
    _selected_events,
    _terminal_options_from_run,
    compute_fingerprint,
    compute_last_hash_hint,
    compute_seq_end,
    index_session_events,
    iter_indexed_events,
    scan_capture_metadata,
)

# Símbolos de módulos vizinhos que o replay_control monolítico reexportava
# (compatibilidade com consumidores que os importam daqui).
from ..state_db import exec1, init_db, now_ms, query_all, query_one
from ..db.connection import connect as db_connect
from ..verifier import verify_log, VerificationError
from ..replay import ReplayConfig, ReplayError, SessionReplayState, _TargetSession, _decode_replay_input, _session_config_from_event  # type: ignore
from ..replay_compare import (
    event_requires_comparison,
    expected_snapshot_from_event,
    observed_snapshot_from_session,
    wait_for_signature_match,
)
from ..compliance import compliance_blocks_execution
from ..replay_failures import (
    add_run_failure,
    build_failure_record,
    classify_checkpoint_failure,
    evaluate_checkpoint_match,
)
from ..replay_run_state import add_run_event, get_run, set_run_status, update_progress
from ..terminal_config import TerminalGeometry, normalize_encoding, validate_terminal_geometry
from dakota_terminal.comparison import compare_signatures, resolve_comparison_mode
