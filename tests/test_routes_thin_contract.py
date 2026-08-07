"""Contrato: rotas finas — synthetic_routes.py abaixo do teto de tamanho.

Dívida R2 do DEBT_MAP: `synthetic_routes.py` chegou a 886 linhas (2026-08-07),
mais que o dobro da segunda maior rota (`capture_routes.py`, 416). O padrão
arquitetural do control plane é: routes/ = acoplamento HTTP fino, regras e
payloads em services/. Módulos de rota inflados concentram lógica de domínio
no lugar errado e voltam a crescer (a dívida já foi "resolvida" uma vez e
reabriu).

O teste DEVE FALHAR antes da decomposição e PASSAR depois dela.
"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TETO_LINHAS = 500


def test_synthetic_routes_below_size_ceiling():
    alvo = ROOT / "gateway" / "control" / "routes" / "synthetic_routes.py"
    linhas = len(alvo.read_text(encoding="utf-8").splitlines())
    assert linhas <= TETO_LINHAS, (
        f"synthetic_routes.py com {linhas} linhas (teto: {TETO_LINHAS}) — "
        "mova regras/payloads para gateway/control/services/ (divida R2)")


def test_synthetic_routes_has_no_inline_domain_serializers():
    """Serializadores de domínio (plan/dataset/preflight) vivem em services/."""
    alvo = ROOT / "gateway" / "control" / "routes" / "synthetic_routes.py"
    texto = alvo.read_text(encoding="utf-8")
    for nome in ("_serialize_plan", "_serialize_dataset", "_serialize_preflight",
                 "_persist_generated_dataset"):
        assert f"def {nome}" not in texto, (
            f"{nome} e regra de dominio — deve viver em services/, nao na rota")
