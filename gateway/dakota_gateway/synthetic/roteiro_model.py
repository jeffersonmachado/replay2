"""Modelo de dados para roteiro inferido de processo de negócio.

Estrutura hierárquica:
    InferredRoute
        └── InferredRoutePhase (fases semânticas)
            └── InferredRouteStep (passos dentro de cada fase)

Evidências:
    ProgramEvidence — rastro de programa/PRG fonte
    MenuEvidence — rastro de menu/opção de navegação
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ProgramEvidence:
    """Evidência de um programa (.prg) que fundamenta um passo/fase."""
    program_name: str = ""
    program_path: str = ""
    module: str = ""                 # prefixo do módulo: ped, cad, fat, est, etc.
    program_type: str = ""           # main, support, validation, report
    title: str = ""                  # título extraído do fonte (@SAY ou TITLE)
    operations: List[str] = field(default_factory=list)  # DO chamados, operações

    def to_dict(self) -> dict:
        return {
            "program_name": self.program_name,
            "program_path": self.program_path,
            "module": self.module,
            "program_type": self.program_type,
            "title": self.title,
            "operations": self.operations,
        }

    @classmethod
    def from_dict(cls, d: dict) -> ProgramEvidence:
        return cls(
            program_name=d.get("program_name", ""),
            program_path=d.get("program_path", ""),
            module=d.get("module", ""),
            program_type=d.get("program_type", ""),
            title=d.get("title", ""),
            operations=d.get("operations", []),
        )


@dataclass
class MenuEvidence:
    """Evidência de menu que leva a um programa."""
    menu_name: str = ""
    menu_path: str = ""
    option_label: str = ""           # texto da opção (ex: "1. Inclusão de Pedido")
    option_key: str = ""             # tecla ou número da opção
    target_program: str = ""         # programa chamado
    source_line: int = 0

    def to_dict(self) -> dict:
        return {
            "menu_name": self.menu_name,
            "menu_path": self.menu_path,
            "option_label": self.option_label,
            "option_key": self.option_key,
            "target_program": self.target_program,
            "source_line": self.source_line,
        }

    @classmethod
    def from_dict(cls, d: dict) -> MenuEvidence:
        return cls(
            menu_name=d.get("menu_name", ""),
            menu_path=d.get("menu_path", ""),
            option_label=d.get("option_label", ""),
            option_key=d.get("option_key", ""),
            target_program=d.get("target_program", ""),
            source_line=d.get("source_line", 0),
        )


@dataclass
class InferredRouteStep:
    """Um passo atômico dentro de uma fase do roteiro.

    Representa uma ação do operador no sistema:
    - Acessar uma rotina
    - Informar um campo
    - Selecionar uma opção
    - Confirmar uma gravação
    """
    order: int = 0
    action: str = ""                 # descrição textual: "Acessar a rotina..."
    type: str = ""                   # navigate, input, select, submit, verify, wait
    menu_option: str = ""            # opção de menu associada
    program: str = ""                # PRG associado ao passo
    depends_on: List[str] = field(default_factory=list)  # passos anteriores (action)
    confidence: float = 0.0          # 0.0 a 1.0
    evidence: List[ProgramEvidence | MenuEvidence] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "order": self.order,
            "action": self.action,
            "type": self.type,
            "menu_option": self.menu_option,
            "program": self.program,
            "depends_on": self.depends_on,
            "confidence": round(self.confidence, 3),
            "evidence": [
                e.to_dict() for e in self.evidence
            ],
        }

    @classmethod
    def from_dict(cls, d: dict) -> InferredRouteStep:
        evidence_raw = d.get("evidence", [])
        evidence: List[ProgramEvidence | MenuEvidence] = []
        for e in evidence_raw:
            if "program_name" in e:
                evidence.append(ProgramEvidence.from_dict(e))
            elif "menu_name" in e:
                evidence.append(MenuEvidence.from_dict(e))
        return cls(
            order=d.get("order", 0),
            action=d.get("action", ""),
            type=d.get("type", ""),
            menu_option=d.get("menu_option", ""),
            program=d.get("program", ""),
            depends_on=d.get("depends_on", []),
            confidence=d.get("confidence", 0.0),
            evidence=evidence,
        )


@dataclass
class InferredRoutePhase:
    """Uma fase semântica do roteiro — agrupa passos relacionados.

    Exemplos de fases:
    - "Inicialização e Cliente"
    - "Valores e Logística"
    - "Itens do Pedido"
    - "Pagamento e Fechamento"
    """
    phase_id: str = ""               # ex: "phase-01"
    title: str = ""                  # ex: "Inicialização e Cliente"
    objective: str = ""              # descrição do objetivo da fase
    menu_context: List[MenuEvidence] = field(default_factory=list)
    program_context: List[ProgramEvidence] = field(default_factory=list)
    steps: List[InferredRouteStep] = field(default_factory=list)
    confidence: float = 0.0

    def to_dict(self) -> dict:
        return {
            "phase_id": self.phase_id,
            "title": self.title,
            "objective": self.objective,
            "menu_context": [m.to_dict() for m in self.menu_context],
            "program_context": [p.to_dict() for p in self.program_context],
            "steps": [s.to_dict() for s in self.steps],
            "confidence": round(self.confidence, 3),
        }

    @classmethod
    def from_dict(cls, d: dict) -> InferredRoutePhase:
        return cls(
            phase_id=d.get("phase_id", ""),
            title=d.get("title", ""),
            objective=d.get("objective", ""),
            menu_context=[MenuEvidence.from_dict(m) for m in d.get("menu_context", [])],
            program_context=[ProgramEvidence.from_dict(p) for p in d.get("program_context", [])],
            steps=[InferredRouteStep.from_dict(s) for s in d.get("steps", [])],
            confidence=d.get("confidence", 0.0),
        )


@dataclass
class ReferenceRouteSummary:
    """Resumo de comparação com um roteiro de referência."""
    reference_name: str = ""
    reference_source: str = ""       # arquivo, URL ou descrição
    phases_matched: int = 0
    phases_total: int = 0
    steps_matched: int = 0
    steps_total: int = 0
    coverage_pct: float = 0.0
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "reference_name": self.reference_name,
            "reference_source": self.reference_source,
            "phases_matched": self.phases_matched,
            "phases_total": self.phases_total,
            "steps_matched": self.steps_matched,
            "steps_total": self.steps_total,
            "coverage_pct": round(self.coverage_pct, 1),
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, d: dict) -> ReferenceRouteSummary:
        return cls(
            reference_name=d.get("reference_name", ""),
            reference_source=d.get("reference_source", ""),
            phases_matched=d.get("phases_matched", 0),
            phases_total=d.get("phases_total", 0),
            steps_matched=d.get("steps_matched", 0),
            steps_total=d.get("steps_total", 0),
            coverage_pct=d.get("coverage_pct", 0.0),
            notes=d.get("notes", []),
        )


@dataclass
class SimulationResult:
    """Resultado da simulação com dados sintéticos."""
    session_count: int = 0
    seed: int = 0
    total_fields: int = 0          # total de campos gerados por sessão
    sessions: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    ok: bool = True

    def to_dict(self) -> dict:
        return {
            "session_count": self.session_count,
            "seed": self.seed,
            "total_fields": self.total_fields,
            "sessions": self.sessions,
            "warnings": self.warnings,
            "ok": self.ok,
        }

    @classmethod
    def from_dict(cls, d: dict) -> SimulationResult:
        return cls(
            session_count=d.get("session_count", 0),
            seed=d.get("seed", 0),
            total_fields=d.get("total_fields", 0),
            sessions=d.get("sessions", []),
            warnings=d.get("warnings", []),
            ok=d.get("ok", True),
        )


@dataclass
class InferredRoute:
    """Roteiro completo de processo de negócio inferido automaticamente.

    Estrutura similar a um documento de referência como
    "Inclusão de Pedido de Venda", mas gerado a partir de
    código-fonte, jornadas, fluxos e regras de negócio.
    """
    route_id: str = ""
    title: str = ""                  # ex: "Inclusão de Pedido de Venda"
    summary: str = ""                # resumo em linguagem natural
    primary_menu: str = ""           # menu principal que dispara o fluxo
    primary_program: str = ""        # PRG principal do fluxo
    supporting_programs: List[ProgramEvidence] = field(default_factory=list)
    phases: List[InferredRoutePhase] = field(default_factory=list)
    reference_comparison: Optional[ReferenceRouteSummary] = None
    simulation: Optional[SimulationResult] = None

    def to_dict(self) -> dict:
        result: Dict[str, Any] = {
            "route_id": self.route_id,
            "title": self.title,
            "summary": self.summary,
            "primary_menu": self.primary_menu,
            "primary_program": self.primary_program,
            "supporting_programs": [p.to_dict() for p in self.supporting_programs],
            "phases": [p.to_dict() for p in self.phases],
        }
        if self.reference_comparison:
            result["reference_comparison"] = self.reference_comparison.to_dict()
        if self.simulation:
            result["simulation"] = self.simulation.to_dict()
        return result

    @classmethod
    def from_dict(cls, d: dict) -> InferredRoute:
        ref = d.get("reference_comparison")
        sim = d.get("simulation")
        return cls(
            route_id=d.get("route_id", ""),
            title=d.get("title", ""),
            summary=d.get("summary", ""),
            primary_menu=d.get("primary_menu", ""),
            primary_program=d.get("primary_program", ""),
            supporting_programs=[
                ProgramEvidence.from_dict(p) for p in d.get("supporting_programs", [])
            ],
            phases=[InferredRoutePhase.from_dict(p) for p in d.get("phases", [])],
            reference_comparison=(
                ReferenceRouteSummary.from_dict(ref) if ref else None
            ),
            simulation=(
                SimulationResult.from_dict(sim) if sim else None
            ),
        )

    def to_markdown(self) -> str:
        """Renderiza o roteiro como documento Markdown para leitura humana."""
        lines: List[str] = []

        # Título
        lines.append(f"# {self.title}")
        lines.append("")

        # Metadados de rastreabilidade
        lines.append("## Metadados de Rastreabilidade")
        lines.append("")
        lines.append(f"- **ID do Roteiro:** `{self.route_id}`")
        lines.append(f"- **Menu Principal:** {self.primary_menu or 'N/D'}")
        lines.append(f"- **PRG Principal:** `{self.primary_program or 'N/D'}`")
        lines.append("")

        # Resumo
        lines.append("## Resumo")
        lines.append("")
        lines.append(self.summary)
        lines.append("")

        # Programas de apoio
        if self.supporting_programs:
            lines.append("## Programas de Apoio / Cadastros Auxiliares")
            lines.append("")
            for sp in self.supporting_programs:
                title_suffix = f" — {sp.title}" if sp.title else ""
                lines.append(f"- **`{sp.program_name}`** ({sp.program_type}){title_suffix}")
            lines.append("")

        # Fases
        lines.append("## Fases do Processo")
        lines.append("")
        for phase in self.phases:
            lines.append(f"### {phase.title}")
            lines.append("")
            if phase.objective:
                lines.append(f"**Objetivo:** {phase.objective}")
                lines.append("")
            if phase.confidence > 0:
                lines.append(f"*Confiança da fase: {phase.confidence:.0%}*")
                lines.append("")

            # Tabela de passos
            if phase.steps:
                lines.append("| # | Ação | Tipo | Programa | Confiança |")
                lines.append("|---|------|------|----------|-----------|")
                for step in phase.steps:
                    prog = f"`{step.program}`" if step.program else "—"
                    lines.append(
                        f"| {step.order} | {step.action} | `{step.type}` | {prog} "
                        f"| {step.confidence:.0%} |"
                    )
                lines.append("")

            # Evidências da fase
            if phase.program_context or phase.menu_context:
                lines.append("<details>")
                lines.append("<summary>Evidências</summary>")
                lines.append("")
                for pe in phase.program_context:
                    lines.append(f"- **PRG:** `{pe.program_name}` (`{pe.module}`)")
                    if pe.title:
                        lines.append(f"  - Título: {pe.title}")
                    if pe.operations:
                        lines.append(f"  - Chamadas: {', '.join(f'`{o}`' for o in pe.operations)}")
                for me in phase.menu_context:
                    lines.append(f"- **Menu:** {me.menu_name} → \"{me.option_label}\" → `{me.target_program}`")
                lines.append("")
                lines.append("</details>")
                lines.append("")

        # Comparação com referência
        if self.reference_comparison:
            ref = self.reference_comparison
            lines.append("## Comparação com Roteiro de Referência")
            lines.append("")
            lines.append(f"- **Referência:** {ref.reference_name}")
            lines.append(f"- **Cobertura:** {ref.coverage_pct:.1f}% ({ref.steps_matched}/{ref.steps_total} passos correspondidos)")
            if ref.notes:
                lines.append("")
                for note in ref.notes:
                    lines.append(f"- {note}")
            lines.append("")

        # Simulação com dados sintéticos
        if self.simulation:
            sim = self.simulation
            status = "✅ Válido" if sim.ok else "❌ Inválido"
            lines.append("## Simulação com Dados Sintéticos")
            lines.append("")
            lines.append(f"- **Status:** {status}")
            lines.append(f"- **Sessões geradas:** {sim.session_count}")
            lines.append(f"- **Campos por sessão:** {sim.total_fields}")
            lines.append(f"- **Seed:** {sim.seed}")
            if sim.warnings:
                lines.append("")
                for w in sim.warnings:
                    lines.append(f"- ⚠ {w}")
            if sim.sessions:
                lines.append("")
                lines.append("### Amostra de dados (Sessão 1)")
                lines.append("")
                lines.append("```")
                s1 = sim.sessions[0] if sim.sessions else {}
                amostra = s1.get("amostra", [])
                for item in amostra[:12]:
                    lines.append(f"  {item}")
                if len(amostra) > 12:
                    lines.append(f"  ... +{len(amostra)-12} campos")
                lines.append("```")
            lines.append("")

        return "\n".join(lines)
