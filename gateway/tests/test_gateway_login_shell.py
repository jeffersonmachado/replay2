"""Testes de regressão: shell da sessão capturada deve ser login shell.

No AIX, ksh/sh não aceitam a flag ``-l``; login shell se invoca com
argv[0] prefixado por ``-`` (ex.: ``-ksh``), caso em que o Popen precisa de
``executable=`` apontando o binário real. Sem isso a sessão SSH capturada
não lê ``/etc/profile`` nem ``~/.profile`` (ambiente do Recital não sobe).
"""

from __future__ import annotations

import os
import types

import pytest

from dakota_gateway.gateway import GatewayConfig, TerminalGateway


def _gateway(tmp_path, monkeypatch, *, sysname: str, shell: str | None, original_cmd: str = ""):
    monkeypatch.setattr(os, "uname", lambda: types.SimpleNamespace(sysname=sysname))
    if shell is None:
        monkeypatch.delenv("SHELL", raising=False)
    else:
        monkeypatch.setenv("SHELL", shell)
    monkeypatch.setenv("SSH_ORIGINAL_COMMAND", original_cmd)
    cfg = GatewayConfig(log_dir=str(tmp_path / "cap"), hmac_key=b"k")
    return TerminalGateway(cfg)


def test_aix_usa_argv0_com_hifen(tmp_path, monkeypatch):
    gw = _gateway(tmp_path, monkeypatch, sysname="AIX", shell="/usr/bin/ksh")
    argv = gw._session_argv()
    assert argv == ["-ksh"]
    assert gw._session_executable(argv) == "/usr/bin/ksh"


def test_aix_sem_shell_env_cai_em_sh(tmp_path, monkeypatch):
    gw = _gateway(tmp_path, monkeypatch, sysname="AIX", shell=None)
    argv = gw._session_argv()
    assert argv == ["-sh"]
    assert gw._session_executable(argv) == "/bin/sh"


def test_linux_usa_flag_l(tmp_path, monkeypatch):
    gw = _gateway(tmp_path, monkeypatch, sysname="Linux", shell="/bin/bash")
    argv = gw._session_argv()
    assert argv == ["/bin/bash", "-l"]
    assert gw._session_executable(argv) is None


def test_comando_remoto_nao_e_login_shell(tmp_path, monkeypatch):
    # ssh host comando: sem login shell em nenhuma plataforma (comportamento
    # tradicional do sshd — profiles não rodam para comandos não-interativos)
    for sysname in ("AIX", "Linux"):
        gw = _gateway(
            tmp_path / sysname, monkeypatch,
            sysname=sysname, shell="/usr/bin/ksh", original_cmd="ls -l",
        )
        argv = gw._session_argv()
        assert argv == ["/bin/sh", "-c", "ls -l"]
        assert gw._session_executable(argv) is None


def test_chdir_user_home_usa_pw_dir(tmp_path, monkeypatch):
    """A sessão capturada deve iniciar no HOME do usuário (como o sshd).

    Profiles do legado usam caminhos relativos ao CWD (ex.: o ".menu" do
    Recital em /etc/.profile); começar no diretório do projeto mostraria o
    menu/arquivos de outro usuário.
    """
    import pwd

    from dakota_gateway.cli_commands.runtime import _chdir_user_home

    alvo = {}
    monkeypatch.setattr(os, "chdir", lambda p: alvo.setdefault("path", p))
    _chdir_user_home()
    assert alvo["path"] == pwd.getpwuid(os.getuid()).pw_dir


def test_chdir_user_home_fallback_env_home(tmp_path, monkeypatch):
    import dakota_gateway.cli_commands.runtime as runtime

    alvo = {}
    monkeypatch.setattr(os, "chdir", lambda p: alvo.setdefault("path", p))
    monkeypatch.setenv("HOME", "/home/fake")
    # simula ausência de pwd (ex.: plataforma sem o módulo)
    import sys

    monkeypatch.setitem(sys.modules, "pwd", None)
    runtime._chdir_user_home()
    assert alvo["path"] == "/home/fake"
