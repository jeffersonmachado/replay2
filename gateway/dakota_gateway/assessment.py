"""AI Assessment: analise inteligente de resultados de validacao e migracao.

Motores:
1. Garbage Collector — codigo morto, tabelas orfas
2. Bottleneck Detector — gargalos de performance
3. Risk Identifier — areas de maior risco na migracao
4. Inconsistency Finder — divergencias sutis entre ambientes
5. Regression Detector — comparacao entre runs
6. Recommendation Engine — recomendacoes acionaveis
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class GarbageFinding:
    """Codigo/recurso nao utilizado."""
    item_type: str = ""  # program, table, screen, file
    item_name: str = ""
    reason: str = ""
    source_file: str = ""
    estimated_cleanup_kb: int = 0
    severity: str = "low"  # low, medium, high


@dataclass
class Bottleneck:
    """Gargalo de performance detectado."""
    bottleneck_type: str = ""  # lock_contention, slow_query, io_bound, cpu_bound
    entity: str = ""
    environment: str = ""
    metric_name: str = ""
    metric_value: float = 0.0
    threshold: float = 0.0
    severity: str = "medium"
    recommendation: str = ""


@dataclass
class RiskArea:
    """Area de risco na migracao."""
    module: str = ""
    entity: str = ""
    risk_type: str = ""  # functional, performance, data_integrity, dependency
    failure_rate_pct: float = 0.0
    risk_factors: list[str] = field(default_factory=list)
    severity: str = "medium"
    recommendation: str = ""


@dataclass
class Inconsistency:
    """Inconsistencia entre ambientes."""
    entity: str = ""
    field: str = ""
    environment_a: str = ""
    environment_b: str = ""
    value_a: str = ""
    value_b: str = ""
    inconsistency_type: str = ""  # data, behavior, order, timing
    severity: str = "low"


@dataclass
class Recommendation:
    """Recomendacao acionavel."""
    priority: str = "medium"  # high, medium, low
    category: str = ""  # performance, security, data, code
    title: str = ""
    description: str = ""
    effort: str = "medium"  # low, medium, high
    impact: str = "medium"


@dataclass
class AssessmentReport:
    """Relatorio completo de AI Assessment."""
    source_dir: str = ""
    # Garbage
    garbage_findings: list[GarbageFinding] = field(default_factory=list)
    total_garbage_kb: int = 0
    # Bottlenecks
    bottlenecks: list[Bottleneck] = field(default_factory=list)
    # Risks
    risks: list[RiskArea] = field(default_factory=list)
    high_risk_count: int = 0
    # Inconsistencies
    inconsistencies: list[Inconsistency] = field(default_factory=list)
    # Recommendations
    recommendations: list[Recommendation] = field(default_factory=list)
    # Score
    health_score: float = 0.0  # 0-100
    summary: str = ""
    detail: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Garbage Collector
# ---------------------------------------------------------------------------

class GarbageCollector:
    """Identifica codigo morto, tabelas orfas e recursos nao utilizados."""

    @staticmethod
    def analyze(entities: list, screens: list, menu_tree: dict | None = None) -> list[GarbageFinding]:
        findings: list[GarbageFinding] = []

        if menu_tree:
            orphan_programs = menu_tree.get("orphan_programs", [])
            for prog in orphan_programs:
                findings.append(GarbageFinding(
                    item_type="program",
                    item_name=prog,
                    reason="Programa nao referenciado por nenhum menu",
                    severity="medium",
                ))

        # Entidades sem operacoes CRUD
        for entity in entities:
            ops = getattr(entity, 'operations', [])
            if not ops:
                findings.append(GarbageFinding(
                    item_type="table",
                    item_name=entity.name if hasattr(entity, 'name') else str(entity),
                    reason="Entidade sem operacoes detectadas no codigo",
                    severity="low" if getattr(entity, 'storage_type', '') == 'sql' else "medium",
                ))

        # Tabelas sem campos
        for entity in entities:
            fields = getattr(entity, 'fields', [])
            if len(fields) == 0 and (getattr(entity, 'operations', None) or []):
                findings.append(GarbageFinding(
                    item_type="table",
                    item_name=entity.name if hasattr(entity, 'name') else str(entity),
                    reason="Entidade com operacoes mas sem campos extraidos",
                    severity="low",
                ))

        return findings


# ---------------------------------------------------------------------------
# Bottleneck Detector
# ---------------------------------------------------------------------------

class BottleneckDetector:
    """Detecta gargalos de performance a partir de metricas de benchmark."""

    @staticmethod
    def analyze(benchmark_result: dict | None = None, run_metrics: dict | None = None) -> list[Bottleneck]:
        bottlenecks: list[Bottleneck] = []

        if benchmark_result:
            for comp in benchmark_result.get("comparisons", []):
                metrics = comp.get("metrics", {})

                # TPS degradation
                tps = metrics.get("tps", {})
                tps_delta = tps.get("delta_pct", 0)
                if tps_delta < -15:
                    bottlenecks.append(Bottleneck(
                        bottleneck_type="performance_degradation",
                        entity=comp.get("target", ""),
                        metric_name="tps",
                        metric_value=tps_delta,
                        threshold=-15,
                        severity="high" if tps_delta < -30 else "medium",
                        recommendation=f"Investigar queda de {abs(tps_delta)}% no TPS do ambiente {comp.get('target','')}",
                    ))

                # Latency increase
                lat = metrics.get("avg_latency_ms", {})
                lat_delta = lat.get("delta_pct", 0)
                if lat_delta > 20:
                    bottlenecks.append(Bottleneck(
                        bottleneck_type="latency_increase",
                        entity=comp.get("target", ""),
                        metric_name="avg_latency_ms",
                        metric_value=lat_delta,
                        threshold=20,
                        severity="high" if lat_delta > 50 else "medium",
                        recommendation=f"Latencia aumentou {lat_delta}% no ambiente {comp.get('target','')}",
                    ))

        return bottlenecks


# ---------------------------------------------------------------------------
# Risk Identifier
# ---------------------------------------------------------------------------

class RiskIdentifier:
    """Identifica areas de maior risco na migracao baseado em falhas e metricas."""

    @staticmethod
    def analyze(
        crud_summary: dict | None = None,
        failure_data: list[dict] | None = None,
        relationship_map: dict | None = None,
    ) -> list[RiskArea]:
        risks: list[RiskArea] = []

        # Entidades sem CRUD completo = risco funcional
        if crud_summary:
            completeness = crud_summary.get("completeness_pct", 100)
            if completeness < 70:
                risks.append(RiskArea(
                    module="",
                    entity="*",
                    risk_type="functional",
                    failure_rate_pct=100 - completeness,
                    risk_factors=[f"Apenas {completeness}% das entidades tem CRUD completo"],
                    severity="high",
                    recommendation="Priorizar validacao de entidades sem CRUD completo",
                ))

        # Entidades sem create = nao testaveis
        if crud_summary:
            without_create = crud_summary.get("entities_without_create", 0)
            if without_create > 0:
                risks.append(RiskArea(
                    module="",
                    entity="",
                    risk_type="functional",
                    failure_rate_pct=0,
                    risk_factors=[f"{without_create} entidades sem operacao de CREATE detectada"],
                    severity="medium",
                    recommendation="Verificar se entidades sem CREATE precisam de jornada de inclusao",
                ))

        # Entidades orfas (sem relacionamentos)
        if relationship_map:
            orphans = relationship_map.get("orphan_entities", [])
            if len(orphans) > 5:
                risks.append(RiskArea(
                    module="",
                    entity="",
                    risk_type="data_integrity",
                    failure_rate_pct=0,
                    risk_factors=[f"{len(orphans)} entidades sem relacionamentos detectados"],
                    severity="low",
                    recommendation="Revisar se entidades orfas precisam de relacionamentos",
                ))

        # Falhas por fluxo
        if failure_data:
            flow_failures: dict[str, int] = {}
            for f in failure_data:
                flow = f.get("flow_name", "unknown")
                flow_failures[flow] = flow_failures.get(flow, 0) + 1

            for flow, count in sorted(flow_failures.items(), key=lambda x: -x[1])[:5]:
                risks.append(RiskArea(
                    module=flow,
                    entity=flow,
                    risk_type="functional",
                    failure_rate_pct=min(100, count * 5),
                    risk_factors=[f"{count} falhas no fluxo {flow}"],
                    severity="high" if count > 10 else "medium",
                    recommendation=f"Investigar e corrigir falhas no fluxo {flow}",
                ))

        return risks


# ---------------------------------------------------------------------------
# Recommendation Engine
# ---------------------------------------------------------------------------

class RecommendationEngine:
    """Gera recomendacoes acionaveis a partir dos achados."""

    @staticmethod
    def generate(
        garbage: list[GarbageFinding],
        bottlenecks: list[Bottleneck],
        risks: list[RiskArea],
        validation_summary: dict | None = None,
    ) -> list[Recommendation]:
        recs: list[Recommendation] = []

        # Garbage → recomendacoes de limpeza
        high_sev_garbage = [g for g in garbage if g.severity == "high"]
        if high_sev_garbage:
            recs.append(Recommendation(
                priority="medium", category="code",
                title=f"Remover {len(high_sev_garbage)} recursos nao utilizados",
                description=f"Programas e tabelas sem uso detectados. Limpeza estimada: {sum(g.estimated_cleanup_kb for g in garbage)}KB",
                effort="low", impact="low",
            ))

        # Bottlenecks → recomendacoes de performance
        high_sev_bn = [b for b in bottlenecks if b.severity == "high"]
        for bn in high_sev_bn[:3]:
            recs.append(Recommendation(
                priority="high", category="performance",
                title=bn.recommendation,
                description=f"Gargalo {bn.bottleneck_type} em {bn.entity}: {bn.metric_name}={bn.metric_value}",
                effort="medium", impact="high",
            ))

        # Risks → recomendacoes de correcao
        high_risks = [r for r in risks if r.severity == "high"]
        for risk in high_risks[:3]:
            recs.append(Recommendation(
                priority="high", category="data",
                title=risk.recommendation,
                description=f"Risco {risk.risk_type} em {risk.entity or risk.module}: {'; '.join(risk.risk_factors)}",
                effort="medium", impact="high",
            ))

        # Validation → recomendacoes de qualidade
        if validation_summary:
            avg_score = validation_summary.get("average_score", 100)
            if avg_score < 70:
                recs.append(Recommendation(
                    priority="high", category="code",
                    title=f"Melhorar qualidade das jornadas (score: {avg_score})",
                    description=f"Score medio de validacao baixo. Revisar jornadas com issues.",
                    effort="medium", impact="high",
                ))

        return recs


# ---------------------------------------------------------------------------
# AI Assessment — orquestrador
# ---------------------------------------------------------------------------

class AIAssessment:
    """Orquestrador do AI Assessment: executa todos os motores e gera relatorio."""

    def __init__(self, db_connection: Optional[sqlite3.Connection] = None):
        self.con = db_connection
        self.gc = GarbageCollector()
        self.bd = BottleneckDetector()
        self.ri = RiskIdentifier()
        self.re = RecommendationEngine()

    def assess(
        self,
        *,
        entities: list | None = None,
        screens: list | None = None,
        menu_tree: dict | None = None,
        crud_summary: dict | None = None,
        relationship_map: dict | None = None,
        benchmark_result: dict | None = None,
        failure_data: list[dict] | None = None,
        validation_summary: dict | None = None,
        source_dir: str = "",
    ) -> AssessmentReport:
        """Executa assessment completo."""

        report = AssessmentReport(source_dir=source_dir)

        # 1. Garbage Collector
        report.garbage_findings = self.gc.analyze(
            entities or [], screens or [], menu_tree
        )
        report.total_garbage_kb = sum(
            g.estimated_cleanup_kb for g in report.garbage_findings
        )

        # 2. Bottleneck Detector
        report.bottlenecks = self.bd.analyze(benchmark_result)

        # 3. Risk Identifier
        report.risks = self.ri.analyze(crud_summary, failure_data, relationship_map)
        report.high_risk_count = sum(1 for r in report.risks if r.severity == "high")

        # 4. Recommendations
        report.recommendations = self.re.generate(
            report.garbage_findings,
            report.bottlenecks,
            report.risks,
            validation_summary,
        )

        # 5. Health Score (0-100)
        report.health_score = self._calculate_health(report)

        # 6. Summary
        report.summary = self._build_summary(report)

        # 7. Detail
        report.detail = {
            "garbage_count": len(report.garbage_findings),
            "bottleneck_count": len(report.bottlenecks),
            "risk_count": len(report.risks),
            "high_risk_count": report.high_risk_count,
            "recommendation_count": len(report.recommendations),
        }

        return report

    def _calculate_health(self, report: AssessmentReport) -> float:
        """Health Score com inferencia ponderada por severidade e tipo."""
        score = 100.0
        for g in report.garbage_findings:
            w = {"high": 8, "medium": 4, "low": 1}.get(g.severity, 2)
            score -= w
        for b in report.bottlenecks:
            w = {"performance_degradation": 10, "latency_increase": 8, "lock_contention": 7,
                 "io_bound": 5, "cpu_bound": 5, "slow_query": 4}.get(b.bottleneck_type, 3)
            score -= w
        for r in report.risks:
            base = {"high": 10, "medium": 5, "low": 2}.get(r.severity, 3)
            if r.risk_type == "functional": base *= 1.5
            elif r.risk_type == "data_integrity": base *= 2.0
            score -= base
        total = len(report.garbage_findings) + len(report.bottlenecks) + len(report.risks)
        if total > 20:
            score -= (total - 20) * 1.5
        return max(0.0, min(100.0, round(score, 1)))

    def _build_summary(self, report: AssessmentReport) -> str:
        lines = ["AI Assessment Report"]
        lines.append(f"Health Score: {report.health_score}/100")

        if report.garbage_findings:
            lines.append(f"Garbage: {len(report.garbage_findings)} itens nao utilizados")
        if report.bottlenecks:
            lines.append(f"Bottlenecks: {len(report.bottlenecks)} gargalos")
        if report.risks:
            high = report.high_risk_count
            lines.append(f"Risks: {len(report.risks)} areas ({high} high severity)")
        if report.recommendations:
            lines.append(f"Recommendations: {len(report.recommendations)}")

        return "\n".join(lines)

    def assess_from_pipeline(
        self, pipeline_result: dict, source_dir: str = ""
    ) -> AssessmentReport:
        """Executa assessment a partir do resultado do pipeline integrado."""
        discovery = pipeline_result.get("discovery", {})
        return self.assess(
            source_dir=source_dir or pipeline_result.get("source_dir", ""),
            crud_summary=discovery.get("crud"),
            validation_summary=pipeline_result.get("validation"),
            relationship_map=discovery.get("relationships"),
            menu_tree=discovery.get("menu"),
        )

    def to_dict(self, report: AssessmentReport) -> dict:
        """Serializa relatorio para JSON."""
        return {
            "source_dir": report.source_dir,
            "health_score": report.health_score,
            "summary": report.summary,
            "garbage": [
                {"type": g.item_type, "name": g.item_name, "reason": g.reason, "severity": g.severity}
                for g in report.garbage_findings[:20]
            ],
            "bottlenecks": [
                {"type": b.bottleneck_type, "entity": b.entity, "metric": b.metric_name,
                 "value": b.metric_value, "severity": b.severity, "recommendation": b.recommendation}
                for b in report.bottlenecks[:10]
            ],
            "risks": [
                {"module": r.module, "type": r.risk_type, "factors": r.risk_factors,
                 "severity": r.severity, "recommendation": r.recommendation}
                for r in report.risks[:15]
            ],
            "recommendations": [
                {"priority": r.priority, "category": r.category, "title": r.title,
                 "description": r.description, "effort": r.effort, "impact": r.impact}
                for r in report.recommendations
            ],
            "detail": report.detail,
        }
