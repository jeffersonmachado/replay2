"""Regressão: start_services do selfinstall deve carregar env file opcional.

Variáveis operacionais do control plane (ex.: DAKOTA_SOURCE_ROOT, sem a qual
os endpoints synthetic respondem 500 em produção) eram perdidas a cada
deploy/restart, porque o stub monta o comando de start do zero. Operadores
tinham que reiniciar o serviço na mão com as variáveis — configuração
volátil, perdida no próximo update.

O stub passa a carregar ``$PREFIX/gateway/control.env`` (se existir) dentro
do `su` de start do control plane, tornando a configuração persistente entre
deploys. O arquivo NÃO vem no tarball (é do servidor, como .local-secrets).

O teste DEVE FALHAR antes da correção e PASSAR depois dela.
"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_start_services_sources_optional_control_env():
    stub = (ROOT / "scripts" / "selfinstall-stub.sh").read_text(encoding="utf-8")
    inicio = stub.index("start_services() {")
    corpo = stub[inicio:stub.index("\n}", inicio)]

    linhas_server = [ln for ln in corpo.splitlines() if "control/server.py" in ln]
    assert linhas_server, "start_services deve iniciar o control plane"
    for ln in linhas_server:
        assert "control.env" in ln, (
            "start do control plane deve carregar $PREFIX/gateway/control.env "
            "opcional (persistir DAKOTA_SOURCE_ROOT etc. entre deploys)")


def test_control_env_not_shipped_in_tarball():
    """control.env é configuração do servidor — não pode ir no pacote."""
    build = (ROOT / "scripts" / "build-tarball.sh").read_text(encoding="utf-8")
    assert "control.env" not in build, \
        "control.env não pode ser incluído no tarball (é do servidor)"
