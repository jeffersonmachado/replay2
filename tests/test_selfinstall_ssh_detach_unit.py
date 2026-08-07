"""Regressão: start_services do selfinstall não pode herdar o canal SSH.

Homologação do deploy Linux via `.run` (v0.8.9): o instalador concluiu tudo
(serviços saudáveis, health check OK), mas a sessão SSH do deploy ficou presa
— os `su "$SERVICE_USER" -c "... &"` de `start_services` deixam os wrappers
`bash -c` dos daemons com stdout/stderr herdados do canal SSH, e o sshd só
encerra a sessão quando o último descritor fecha. Todo deploy via
`ssh host "sh pacote.run"` travava até intervenção manual (pkill).

A correção é destacar stdin/stdout/stderr das chamadas `su` de start_services
(a saída dos daemons já vai para os logs em /tmp).

O teste DEVE FALHAR antes da correção e PASSAR depois dela.
"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _start_services_body(stub: str) -> str:
    inicio = stub.index("start_services() {")
    fim = stub.index("\n}", inicio)
    return stub[inicio:fim]


def test_selfinstall_start_services_detaches_su_from_caller_fds():
    stub = (ROOT / "scripts" / "selfinstall-stub.sh").read_text(encoding="utf-8")
    corpo = _start_services_body(stub)

    linhas_su = [ln.strip() for ln in corpo.splitlines()
                 if ln.strip().startswith('su "$SERVICE_USER"')]
    assert linhas_su, "start_services deve iniciar os serviços via su"
    for ln in linhas_su:
        assert "</dev/null" in ln.replace(" ", ""), \
            f"su sem stdin destacado (trava sessão SSH do deploy): {ln}"
        assert ">/dev/null" in ln.replace(" ", ""), \
            f"su sem stdout destacado (trava sessão SSH do deploy): {ln}"
