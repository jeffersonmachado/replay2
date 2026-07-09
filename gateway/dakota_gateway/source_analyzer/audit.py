"""Infraestrutura de auditoria para inferencias IA no Replay2.

Cada inferencia (entidade, campo, provider) gera um AuditTrail com:
- O que foi inferido
- Por que (evidencias: regra, padrao, score, arquivo, linha)
- Confianca da inferencia
- Timestamp

Usado por: recital_extractor, field_classifier, smart_provider_router.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional


@dataclass
class AuditEvidence:
    """Uma evidencia atomica de inferencia."""
    rule: str = ""          # ex: "XxxAbreNNN", "token:cpf", "context:cliente"
    pattern: str = ""       # ex: "CreAbre100(1)", "cpf", "documento"
    score: float = 0.0      # peso da evidencia
    source_file: str = ""   # arquivo onde foi encontrada
    source_line: int = 0    # linha
    context: str = ""       # ex: "entidade=clientes", "modulo=contas_receber"


@dataclass
class AuditTrail:
    """Trilha de auditoria completa."""
    entity_name: str = ""
    field_name: str = ""
    inference_type: str = ""  # entity_discovery | field_classification | provider_routing
    final_decision: str = ""
    confidence: float = 0.0
    evidence: List[AuditEvidence] = field(default_factory=list)
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def add_evidence(self, rule: str, pattern: str, score: float,
                     source_file: str = "", source_line: int = 0,
                     context: str = "") -> None:
        self.evidence.append(AuditEvidence(
            rule=rule, pattern=pattern, score=score,
            source_file=source_file, source_line=source_line,
            context=context,
        ))
        self.confidence = max(self.confidence, min(score / 10.0, 1.0))

    def to_dict(self) -> dict:
        return {
            "entity_name": self.entity_name,
            "field_name": self.field_name,
            "inference_type": self.inference_type,
            "final_decision": self.final_decision,
            "confidence": round(self.confidence, 3),
            "evidence": [
                {
                    "rule": e.rule, "pattern": e.pattern,
                    "score": e.score, "source_file": e.source_file,
                    "source_line": e.source_line, "context": e.context,
                }
                for e in self.evidence
            ],
            "timestamp": self.timestamp,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    def inject_into_meta(self, existing_meta: Optional[str]) -> str:
        """Mescla audit trail no metadata_json existente."""
        try:
            meta = json.loads(existing_meta) if existing_meta else {}
        except (json.JSONDecodeError, TypeError):
            meta = {}
        meta["_audit"] = self.to_dict()
        return json.dumps(meta, ensure_ascii=False)

    @staticmethod
    def extract_from_meta(meta_json: Optional[str]) -> Optional[dict]:
        """Extrai audit trail do metadata_json."""
        if not meta_json:
            return None
        try:
            return json.loads(meta_json).get("_audit")
        except (json.JSONDecodeError, TypeError):
            return None


# ── Persistencia em DB ──
_audit_log: List[AuditTrail] = []
_db_pool = None


def set_db_pool(pool) -> None:
    global _db_pool
    _db_pool = pool


def log_audit(trail: AuditTrail) -> None:
    """Registra trilha em memoria e no DB (se disponivel)."""
    _audit_log.append(trail)
    if _db_pool:
        con = _db_pool.acquire()
        try:
            con.execute(
                """INSERT INTO audit_trails (entity_name, field_name, inference_type,
                   final_decision, confidence, evidence_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (trail.entity_name, trail.field_name, trail.inference_type,
                 trail.final_decision, trail.confidence,
                 json.dumps([e.__dict__ for e in trail.evidence], ensure_ascii=False, default=str),
                 trail.timestamp),
            )
            con.commit()
        except Exception:
            pass
        finally:
            _db_pool.release(con)


def get_audit_log() -> List[dict]:
    """Retorna trilhas do DB (se disponivel) ou da memoria."""
    if _db_pool:
        con = _db_pool.acquire()
        con.row_factory = sqlite3.Row
        try:
            rows = con.execute(
                "SELECT entity_name, field_name, inference_type, final_decision, "
                "confidence, evidence_json, created_at FROM audit_trails ORDER BY id"
            ).fetchall()
            if rows:
                result = []
                for r in rows:
                    try:
                        evidence = json.loads(r["evidence_json"]) if r["evidence_json"] else []
                    except Exception:
                        evidence = []
                    result.append({
                        "entity_name": r["entity_name"], "field_name": r["field_name"],
                        "inference_type": r["inference_type"], "final_decision": r["final_decision"],
                        "confidence": r["confidence"], "evidence": evidence,
                        "timestamp": r["created_at"],
                    })
                return result
        except Exception:
            pass
        finally:
            _db_pool.release(con)
    return [t.to_dict() for t in _audit_log]


def clear_audit_log() -> None:
    """Limpa auditoria."""
    _audit_log.clear()
    if _db_pool:
        con = _db_pool.acquire()
        try:
            con.execute("DELETE FROM audit_trails")
            con.commit()
        except Exception:
            pass
        finally:
            _db_pool.release(con)
