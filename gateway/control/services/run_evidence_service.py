"""Serviço de exportação do pacote de evidência verificável de uma run.

Camada fina sobre ``dakota_gateway.evidence_bundle``: resolve a versão do
gerador (VERSION), injeta o payload de métricas de host
(``host_metrics_service.build_export``) e devolve o manifesto montado.
"""
from __future__ import annotations

from pathlib import Path

from dakota_gateway.evidence_bundle import build_run_evidence_bundle

from control.services.host_metrics_service import build_export

_VERSION_FILE = Path(__file__).resolve().parents[3] / "VERSION"


def _generator_version() -> str:
    try:
        return _VERSION_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def export_run_evidence(
    con,
    run_id: int,
    *,
    hmac_key: bytes = b"",
    dest_dir: str,
    version: str = "",
) -> dict:
    """Exporta o pacote de evidência da run para ``dest_dir``.

    Retorna o manifesto (dict) com a chave extra ``bundle_dir``. Levanta
    ``ValueError`` quando a run não existe ou o destino não está vazio.
    """
    manifest = build_run_evidence_bundle(
        con,
        int(run_id),
        dest_dir,
        hmac_key=hmac_key,
        version=version or _generator_version(),
        host_metrics_export=build_export(con, int(run_id)),
    )
    manifest["bundle_dir"] = str(dest_dir)
    return manifest
