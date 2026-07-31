"""PASSAGEM 1 (TDD) — spec nova do process_tree: PROIBIDA allowlist por nome.

A spec da cadeia de release determina que QUALQUER processo com
DAKOTA_PROCESS_RUN_ID que sobreviva ao pai deve ser classificado como leaked
(e escaped quando aplicavel), eliminado, com success=false — sem excecao por
nome de comm (chrome_crashpad, browsers, helpers).

Regra de invariante: se um processo aparece em killed_processes porque
sobreviveu ao pai, ele OBRIGATORIAMENTE deve aparecer em leaked_processes.

Estes testes FALHAM hoje: scripts/process_tree.py ainda contem a allowlist
(_IGNORED_COMMS/_BROWSER_COMMS/_has_browser_ancestor/_is_escape_ignorable/
_is_leak_ignorable) que perdoa comms por nome.

Rodar isolado:
    PYTHONPATH=gateway pytest -q tests/acceptance/test_process_tree_no_allowlist.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PT_CLI = ROOT / "scripts" / "process_tree.py"

# Simbolos da allowlist por nome proibidos pela spec nova.
FORBIDDEN_SYMBOLS = (
    "_IGNORED_COMMS",
    "_BROWSER_COMMS",
    "_has_browser_ancestor",
    "_is_escape_ignorable",
    "_is_leak_ignorable",
)

# Hash/contrato auxiliar: comm truncada pelo kernel em 15 chars.
CRASHPAD_COMM = "chrome_crashpad"


def _run_pt(command: list[str], *, timeout: float, name: str, workdir: Path) -> dict:
    """Executa process_tree.py via CLI e devolve o result JSON.

    A limpeza de processos sobreviventes e' feita no finally via subcomando
    `cleanup` (parte estavel da CLI), garantindo que nenhum filho vaze do
    teste mesmo quando o runner falha em mata-los.
    """
    result_json = workdir / f"{name}.result.json"
    stdout_log = workdir / f"{name}.log"
    try:
        r = subprocess.run(
            [sys.executable, str(PT_CLI), "run",
             "--name", name, "--timeout", str(timeout),
             "--stdout-log", str(stdout_log),
             "--result-json", str(result_json),
             "--run-id", name,
             "--"] + command,
            capture_output=True, text=True, timeout=timeout + 30, cwd=str(ROOT),
        )
        assert result_json.exists(), f"result JSON ausente\nstderr={r.stderr[:500]}"
        result = json.loads(result_json.read_text())
        result["_cli_returncode"] = r.returncode
        return result
    finally:
        # Garante que nada com o run_id do teste fica vivo apos o teste.
        subprocess.run(
            [sys.executable, str(PT_CLI), "cleanup", name],
            capture_output=True, text=True, timeout=15,
        )


def _fake_sleep(workdir: Path, comm: str) -> Path:
    """Symlink de `sleep` com o comm desejado (padrao de
    tests/acceptance/test_process_tree_executor.py)."""
    real_sleep = shutil.which("sleep")
    assert real_sleep, "binario 'sleep' nao encontrado no PATH"
    fake = workdir / comm
    os.symlink(real_sleep, fake)
    return fake


def _spawn_detached_script(fake_bin: Path) -> str:
    """Script que deixa um processo destacado (sessao propria) vivo."""
    return (
        "import subprocess\n"
        f"subprocess.Popen([{str(fake_bin)!r}, '60'], start_new_session=True)\n"
    )


# ── Teste estatico: allowlist por nome proibida no codigo ────────────────────

def test_source_has_no_name_allowlist():
    """process_tree.py nao pode conter NENHUM mecanismo de allowlist por nome
    de comm — nem para crashpad, nem para browsers/helpers."""
    src = PT_CLI.read_text(encoding="utf-8")
    for sym in FORBIDDEN_SYMBOLS:
        assert sym not in src, (
            f"allowlist por nome proibida pela spec: '{sym}' presente em {PT_CLI}"
        )


# ── Dinamico: chrome_crashpad NAO e' perdoado ────────────────────────────────

def test_detached_crashpad_comm_is_leaked_and_escaped(tmp_path):
    """Processo destacado com comm 'chrome_crashpad' (symlink -> sleep) DEVE
    aparecer em escaped_processes E leaked_processes, com success=false.
    Hoje a allowlist perdoa esse comm — este teste FALHA ate a remocao."""
    fake = _fake_sleep(tmp_path, CRASHPAD_COMM)
    result = _run_pt([sys.executable, "-c", _spawn_detached_script(fake)],
                     timeout=5.0, name="p1-noallow-crashpad", workdir=tmp_path)

    assert result["exit_code"] == 0, f"exit_code={result['exit_code']}"
    escaped_comms = [p["comm"] for p in result["escaped_processes"]]
    leaked_comms = [p["comm"] for p in result["leaked_processes"]]
    assert CRASHPAD_COMM in escaped_comms, (
        f"comm '{CRASHPAD_COMM}' deveria ser escaped; escaped={escaped_comms}"
    )
    assert CRASHPAD_COMM in leaked_comms, (
        f"comm '{CRASHPAD_COMM}' deveria ser leaked; leaked={leaked_comms}"
    )
    assert result["success"] is False, "success deve ser false com escape/leak"
    assert result["_cli_returncode"] != 0, "CLI deve sair != 0 com escape/leak"


# ── Dinamico: controle com comm generica (sem nome especial) ─────────────────

def test_detached_generic_comm_is_leaked_and_escaped(tmp_path):
    """Controle: o MESMO cenario com comm generica ja deve ser detectado —
    prova que o cenario de teste e' valido e so o crashpad era perdoado."""
    fake = _fake_sleep(tmp_path, "zzp1leak")
    result = _run_pt([sys.executable, "-c", _spawn_detached_script(fake)],
                     timeout=5.0, name="p1-noallow-generic", workdir=tmp_path)

    assert result["exit_code"] == 0
    assert "zzp1leak" in [p["comm"] for p in result["escaped_processes"]]
    assert "zzp1leak" in [p["comm"] for p in result["leaked_processes"]]
    assert result["success"] is False


# ── Invariante: killed por sobreviver ao pai ⇒ leaked ────────────────────────

def test_killed_because_survived_parent_implies_leaked(tmp_path):
    """Todo processo em killed_processes (morto pelo runner por sobreviver ao
    pai) DEVE constar em leaked_processes. Hoje o chrome_crashpad e' morto na
    fase KILL mas some de leaked_processes por causa da allowlist — FALHA."""
    fake = _fake_sleep(tmp_path, CRASHPAD_COMM)
    result = _run_pt([sys.executable, "-c", _spawn_detached_script(fake)],
                     timeout=5.0, name="p1-noallow-killed", workdir=tmp_path)

    killed_pids = {p["pid"] for p in result["killed_processes"]}
    leaked_pids = {p["pid"] for p in result["leaked_processes"]}
    assert killed_pids, (
        "cenario invalido: nenhum processo precisou ser morto pelo runner "
        f"(result={json.dumps(result, indent=2)[:800]})"
    )
    missing = killed_pids - leaked_pids
    assert not missing, (
        f"processos mortos por sobreviver ao pai ausentes de leaked_processes: "
        f"{sorted(missing)} (regra: killed ⇒ leaked)"
    )
    assert result["success"] is False
