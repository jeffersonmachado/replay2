"""Testes da verificação em passagem única de streaming (FASE 9).

O verifier relia cada audit-*.jsonl duas vezes (cadeia + manifest) e usava
``read_bytes()`` para o SHA do arquivo — em capturas de centenas de MB isso
dobrava o I/O e picava a memória. A verificação agora faz UMA passagem em
streaming (chunks de 1 MiB), acumulando hash-chain/HMAC/sequências e as
estatísticas conferidas nos manifests (file_sha256, bytes, seq_start/seq_end,
first/last hash) — o FORMATO dos arquivos de auditoria não mudou.

Run:
  PYTHONPATH=gateway python3 -m pytest tests/test_verifier_streaming_unit.py -v
  PYTHONPATH=gateway python3 -m pytest tests/test_verifier_streaming_unit.py -v -m slow
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
from dataclasses import asdict
from pathlib import Path

import pytest

from dakota_gateway import verifier
from dakota_gateway.audit_writer import write_manifest
from dakota_gateway.canonical import canonical_string_v2
from dakota_gateway.crypto import hmac_sha256_hex, sha256_hex
from dakota_gateway.schema import AuditEvent
from dakota_gateway.verifier import VerificationError, verify_log, verify_manifests

HMAC_KEY = b"segredo-fase9"


def _gerar_captura_encadeada(log_dir: Path, *, alvo_bytes: int, n_arquivos: int = 3,
                             sid: str = "sessao-grande", payload_bytes: int = 512) -> dict:
    """Gera captura sintética com hash-chain + HMAC válidos (v2), distribuída
    em n_arquivos com manifest por arquivo (como o AuditWriter na rotação),
    sem o custo de flock por evento. Retorna estatísticas esperadas."""
    log_dir.mkdir(parents=True, exist_ok=True)
    seq = 0
    seq_session = 0
    prev_hash = ""
    ts = 1_700_000_000_000
    dados = base64.b64encode(b"z" * payload_bytes).decode()
    stats = {"eventos": 0, "arquivos": []}
    for parte in range(n_arquivos):
        path = log_dir / f"audit-20260902-000000.part{parte + 1:03d}.jsonl"
        alvo_arquivo = alvo_bytes // n_arquivos
        primeiro_seq = 0
        primeiro_hash = ""
        with open(path, "w", encoding="utf-8") as f:
            while f.tell() < alvo_arquivo or (parte == n_arquivos - 1 and not primeiro_seq):
                seq += 1
                seq_session += 1
                ts += 5
                tipo = "bytes" if seq % 11 else "checkpoint"
                ev = AuditEvent(
                    v="v2", seq_global=seq, ts_ms=ts, type=tipo, actor="op",
                    session_id=sid, seq_session=seq_session, timestamp_ms=ts,
                )
                if tipo == "bytes":
                    ev.dir = "out" if seq % 2 else "in"
                    ev.data_b64 = dados
                    ev.n = payload_bytes
                else:
                    ev.sig = f"sig-{seq}"
                ev.prev_hash = prev_hash
                payload = canonical_string_v2(ev).encode("utf-8")
                ev.hash = sha256_hex(payload)
                ev.hmac = hmac_sha256_hex(HMAC_KEY, payload)
                f.write(json.dumps(asdict(ev), ensure_ascii=False) + "\n")
                prev_hash = ev.hash
                if not primeiro_seq:
                    primeiro_seq = seq
                    primeiro_hash = ev.hash
        write_manifest(str(path))
        stats["arquivos"].append({
            "name": path.name, "seq_start": primeiro_seq, "seq_end": seq,
            "first_hash": primeiro_hash, "last_hash": prev_hash,
            "size": path.stat().st_size,
        })
    stats["eventos"] = seq
    return stats


def _conta_aberturas(monkeypatch):
    """Conta aberturas por arquivo via open() do módulo verifier."""
    estado = {"aberturas": {}}
    open_real = io.open

    def _open_contando(path, mode="r", *args, **kwargs):
        estado["aberturas"][str(path)] = estado["aberturas"].get(str(path), 0) + 1
        return open_real(path, mode, *args, **kwargs)

    monkeypatch.setattr(verifier, "open", _open_contando, raising=False)
    return estado


def _proibe_read_bytes(monkeypatch):
    def _explode(self, *args, **kwargs):
        raise RuntimeError("read_bytes proibido em logs grandes: usar streaming")

    monkeypatch.setattr(Path, "read_bytes", _explode)


def test_verify_ok_sem_read_bytes_e_uma_passagem_por_arquivo(tmp_path, monkeypatch):
    stats = _gerar_captura_encadeada(tmp_path / "cap", alvo_bytes=200_000, n_arquivos=3)
    _proibe_read_bytes(monkeypatch)
    estado = _conta_aberturas(monkeypatch)

    verify_log(str(tmp_path / "cap"), HMAC_KEY)  # não levanta

    for arq in stats["arquivos"]:
        caminho = str(tmp_path / "cap" / arq["name"])
        assert estado["aberturas"].get(caminho, 0) == 1, (
            f"{arq['name']} aberto {estado['aberturas'].get(caminho, 0)}x — "
            "cadeia e manifest devem sair da MESMA passagem")


def test_manifest_adulterado_detectado_sem_relacao_com_read_bytes(tmp_path, monkeypatch):
    _gerar_captura_encadeada(tmp_path / "cap", alvo_bytes=60_000, n_arquivos=2)
    manifest = tmp_path / "cap" / "audit-20260902-000000.part001.jsonl.manifest.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["file_sha256"] = "0" * 64
    manifest.write_text(json.dumps(data), encoding="utf-8")

    _proibe_read_bytes(monkeypatch)
    with pytest.raises(VerificationError, match="file_sha256"):
        verify_log(str(tmp_path / "cap"), HMAC_KEY)


def test_manifest_bytes_divergentes_detectado(tmp_path, monkeypatch):
    _gerar_captura_encadeada(tmp_path / "cap", alvo_bytes=60_000, n_arquivos=1)
    manifest = tmp_path / "cap" / "audit-20260902-000000.part001.jsonl.manifest.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["bytes"] = int(data["bytes"]) + 1
    manifest.write_text(json.dumps(data), encoding="utf-8")

    _proibe_read_bytes(monkeypatch)
    with pytest.raises(VerificationError, match="bytes"):
        verify_log(str(tmp_path / "cap"), HMAC_KEY)


@pytest.mark.parametrize("campo,valor", [
    ("seq_start", lambda d: int(d["seq_start"]) + 1),
    ("seq_end", lambda d: int(d["seq_end"]) + 1),
    ("first_hash", lambda d: "1" * 64),
    ("last_hash", lambda d: "2" * 64),
])
def test_manifest_seq_e_hashes_divergentes_detectados(tmp_path, monkeypatch, campo, valor):
    _gerar_captura_encadeada(tmp_path / "cap", alvo_bytes=60_000, n_arquivos=1)
    manifest = tmp_path / "cap" / "audit-20260902-000000.part001.jsonl.manifest.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data[campo] = valor(data)
    manifest.write_text(json.dumps(data), encoding="utf-8")

    _proibe_read_bytes(monkeypatch)
    with pytest.raises(VerificationError, match=campo):
        verify_log(str(tmp_path / "cap"), HMAC_KEY)


def test_evento_adulterado_detectado_com_mesma_falha_de_antes(tmp_path, monkeypatch):
    _gerar_captura_encadeada(tmp_path / "cap", alvo_bytes=60_000, n_arquivos=1)
    path = tmp_path / "cap" / "audit-20260902-000000.part001.jsonl"
    conteudo = path.read_text(encoding="utf-8")
    path.write_text(conteudo.replace('"actor": "op"', '"actor": "oQ"', 1), encoding="utf-8")

    _proibe_read_bytes(monkeypatch)
    with pytest.raises(VerificationError, match="hash mismatch"):
        verify_log(str(tmp_path / "cap"), HMAC_KEY)


def test_verify_manifests_standalone_sem_read_bytes(tmp_path, monkeypatch):
    _gerar_captura_encadeada(tmp_path / "cap", alvo_bytes=60_000, n_arquivos=2)
    _proibe_read_bytes(monkeypatch)
    verify_manifests(str(tmp_path / "cap"))  # não levanta


@pytest.mark.slow
def test_verify_captura_100mb_streaming_sem_read_bytes(tmp_path, monkeypatch):
    """Captura ~100MB: verify_log em streaming (sem read_bytes), uma única
    abertura por arquivo, mesmo veredito da implementação anterior."""
    stats = _gerar_captura_encadeada(tmp_path / "cap", alvo_bytes=100 * 1024 * 1024, n_arquivos=4)
    total = sum(a["size"] for a in stats["arquivos"])
    assert total >= 100 * 1024 * 1024

    _proibe_read_bytes(monkeypatch)
    estado = _conta_aberturas(monkeypatch)
    verify_log(str(tmp_path / "cap"), HMAC_KEY)
    for arq in stats["arquivos"]:
        caminho = str(tmp_path / "cap" / arq["name"])
        assert estado["aberturas"].get(caminho, 0) == 1
