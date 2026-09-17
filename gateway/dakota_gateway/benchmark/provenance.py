"""Cálculo REAL dos hashes de proveniência do contrato de benchmark.

O experimento oficial v7 foi marcado INCONCLUSIVE porque os hashes de
proveniência foram digitados à mão: ``journey_set_sha256``,
``dataset_sha256`` e ``application_version_sha256`` idênticos sem
justificativa, e ``think_time_profile.sha256`` placeholder
(``"def1277b"`` + zeros). Este módulo é o caminho oficial do v8: os hashes
passam a ser **calculados** dos artefatos, nunca digitados.

Esquemas:

- **arquivo único** → SHA-256 do conteúdo (streaming em chunks de 1 MiB,
  mesma disciplina do ``verifier`` — nunca ``read_bytes()`` em artefato
  grande);
- **diretório (hash de conjunto)** → linhas ``"<sha256>  <path relativo>\\n"``
  ordenadas pelo caminho relativo POSIX, e o SHA-256 da concatenação UTF-8 —
  o mesmo esquema do ``evidence-manifest.sha256`` (``report.py``). A ordem é
  estável e o hash muda se qualquer conteúdo OU caminho relativo mudar.

Decisão sobre hashes vazios/placeholder (registrada aqui e no runbook v8,
``docs/benchmark-oficial-v8.md``):

- placeholder óbvio (não-hex, literal ``unknown``, ≥ 32 zeros consecutivos)
  continua RECUSADO na criação por ``validate_provenance_hashes`` —
  inclusive no ``benchmark create`` da CLI, que agora devolve erro em vez de
  propagar a exceção;
- hash VAZIO segue tolerado apenas na camada de contrato
  (``exigir_presenca=False``), porque a criação via UI pode ainda não
  conhecer todos os artefatos — mas a DECISÃO exige presença
  (``exigir_presenca=True``) e vira INCONCLUSIVE. O caminho oficial de
  preparação (este módulo + flags da CLI) nunca produz hash vazio;
- string informada no JSON do contrato que DIVERGE do hash calculado pelo
  caminho informado é recusada (``ContractViolation``) — ambiguidade de
  proveniência é exatamente o modo de falha do v7.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .contract import ContractViolation

_CHUNK_BYTES = 1024 * 1024  # 1 MiB, como o verifier


def sha256_arquivo(caminho: Path) -> str:
    """SHA-256 hex do conteúdo de um arquivo (streaming em chunks)."""
    caminho = Path(caminho)
    if not caminho.is_file():
        raise ContractViolation(f"artefato de proveniência não encontrado: "
                                f"{caminho}")
    digest = hashlib.sha256()
    with open(caminho, "rb") as fh:
        while True:
            bloco = fh.read(_CHUNK_BYTES)
            if not bloco:
                break
            digest.update(bloco)
    return digest.hexdigest()


def sha256_conjunto(caminho: Path) -> str:
    """SHA-256 de um artefato: arquivo único ou hash de conjunto de diretório.

    Diretório: linhas ``"<sha256>  <path relativo>\\n"`` ordenadas (mesmo
    esquema do ``evidence-manifest.sha256``) e SHA-256 da concatenação.
    Diretório vazio é recusado — um conjunto sem arquivos não identifica
    nada e seria placeholder disfarçado.
    """
    caminho = Path(caminho)
    if caminho.is_file():
        return sha256_arquivo(caminho)
    if not caminho.is_dir():
        raise ContractViolation(f"artefato de proveniência não encontrado: "
                                f"{caminho}")
    linhas: list[str] = []
    for arq in sorted(caminho.rglob("*")):
        if not arq.is_file():
            continue
        relativo = arq.relative_to(caminho).as_posix()
        linhas.append(f"{sha256_arquivo(arq)}  {relativo}")
    if not linhas:
        raise ContractViolation(f"diretório de proveniência sem arquivos: "
                                f"{caminho}")
    payload = ("\n".join(linhas) + "\n").encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


#: Mapeamento flag da CLI → (campo do contrato, rótulo do artefato).
_CAMPOS = (
    ("journey_set", "journey_set_sha256", "jornadas"),
    ("dataset", "dataset_sha256", "dataset/massa de dados"),
    ("application", "application_version_sha256", "aplicação sob teste"),
    ("think_time_profile", "think_time_profile.sha256", "perfil de think time"),
)


def calcular_hashes_proveniencia(*, journey_set=None, dataset=None,
                                 application=None,
                                 think_time_profile=None) -> dict[str, dict]:
    """Calcula os hashes reais dos artefatos informados (caminhos locais).

    Devolve ``{campo: {"sha256", "fonte", "tipo"}}`` — ``tipo`` é
    ``"arquivo"`` ou ``"conjunto"`` (diretório). Campos sem caminho
    informado simplesmente não aparecem no resultado (o contrato pode
    continuar recebendo a string pelo JSON, no fluxo retrocompatível).
    """
    caminhos = {
        "journey_set": journey_set,
        "dataset": dataset,
        "application": application,
        "think_time_profile": think_time_profile,
    }
    calculados: dict[str, dict] = {}
    for flag, campo, rotulo in _CAMPOS:
        bruto = caminhos.get(flag)
        if not bruto:
            continue
        caminho = Path(bruto)
        calculados[campo] = {
            "sha256": sha256_conjunto(caminho),
            "fonte": str(caminho),
            "tipo": "arquivo" if caminho.is_file() else "conjunto",
            "rotulo": rotulo,
        }
    return calculados


def aplicar_hashes_proveniencia(dados: dict, calculados: dict[str, dict],
                                ) -> dict:
    """Injeta os hashes calculados no dict do contrato (antes do create).

    Conflito — string já presente no JSON divergindo do hash calculado —
    é recusado com ``ContractViolation`` (ambiguidade de proveniência);
    string idêntica é aceita (idempotente).
    """
    dados = dict(dados or {})
    for campo, reg in (calculados or {}).items():
        sha = reg["sha256"]
        if campo == "think_time_profile.sha256":
            perfil = dict(dados.get("think_time_profile") or {})
            atual = str(perfil.get("sha256") or "").strip()
            if atual and atual.lower() != sha.lower():
                raise ContractViolation(
                    f"{campo}: hash informado no contrato diverge do "
                    f"calculado de {reg['fonte']}")
            perfil["sha256"] = sha
            perfil.setdefault("type", "deterministic")
            perfil.setdefault("params", {})
            dados["think_time_profile"] = perfil
            continue
        atual = str(dados.get(campo) or "").strip()
        if atual and atual.lower() != sha.lower():
            raise ContractViolation(
                f"{campo}: hash informado no contrato diverge do calculado "
                f"de {reg['fonte']}")
        dados[campo] = sha
    return dados


def escrever_proveniencia(experiment_dir: Path,
                          calculados: dict[str, dict]) -> Path:
    """Grava ``provenance.json`` no diretório do experimento.

    Registro auditável de COMO cada hash do contrato foi obtido (fonte e
    tipo de cada artefato) — entra no ``evidence-manifest.sha256`` como
    qualquer outro artefato do experimento.
    """
    experiment_dir = Path(experiment_dir)
    experiment_dir.mkdir(parents=True, exist_ok=True)
    caminho = experiment_dir / "provenance.json"
    payload = {
        "schema": "provenance/v1",
        "artefatos": [
            {"campo": campo, "sha256": reg["sha256"], "fonte": reg["fonte"],
             "tipo": reg["tipo"], "rotulo": reg["rotulo"]}
            for campo, reg in sorted(calculados.items())
        ],
    }
    caminho.write_text(json.dumps(payload, indent=2, ensure_ascii=False,
                                  sort_keys=True) + "\n", encoding="utf-8")
    return caminho
