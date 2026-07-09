"""Motor de Regras de Negócio — entende o fluxo de negócio do Dakota.

Substitui a análise puramente técnica (CRUD) por regras de negócio:
- "Para gerar pedido, o cliente precisa estar cadastrado"
- "Nota fiscal é gerada a partir do faturamento do pedido"
- "Duplicata vem da nota fiscal"

Fluxos de negócio mapeados:
1. Venda: Cliente → Pedido → Faturamento → Duplicata → Recebimento
2. Compra: Fornecedor → OC → Recebimento → Estoque → Pagamento
3. Cadastro: Empresa → Filial → Usuário → Perfil
4. Estoque: Produto → Movimentação → Inventário
5. Produção: Ordem → Apontamento → Expedição
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple


@dataclass
class BusinessRule:
    """Uma regra de negócio que conecta entidades."""
    rule_id: str = ""
    description: str = ""           # ex: "Pedido exige cliente cadastrado"
    depends_on: str = ""            # entidade da qual depende
    enables: str = ""               # entidade que habilita
    dependency_type: str = ""       # "requires" | "generates" | "updates"
    required_operations: List[str] = field(default_factory=list)  # ["create", "read"]
    is_critical: bool = True        # se quebrar, é gap sério


@dataclass 
class BusinessFlow:
    """Um fluxo completo de negócio (ex: Venda do início ao fim)."""
    flow_id: str = ""
    flow_name: str = ""             # "Venda Completa"
    description: str = ""
    steps: List[Dict] = field(default_factory=list)  # [{entity, operation, why}]
    triggers: List[str] = field(default_factory=list)  # eventos que disparam


@dataclass
class BusinessGap:
    """Um gap de negócio detectado."""
    gap_id: str = ""
    severity: str = ""              # "critical" | "high" | "medium" | "low"
    description: str = ""           # descrição do gap em linguagem de negócio
    missing_entity: str = ""        # entidade faltante
    affected_flow: str = ""         # fluxo impactado
    impact: str = ""                # consequência no negócio
    recommendation: str = ""        # o que fazer


# ── Regras de negócio do Dakota Calçados ──

_BUSINESS_RULES: List[BusinessRule] = [
    # Fluxo de Venda
    BusinessRule(
        rule_id="BR-001", description="Pedido exige cliente cadastrado",
        depends_on="cliente", enables="pedido", dependency_type="requires",
        required_operations=["create", "read"], is_critical=True,
    ),
    BusinessRule(
        rule_id="BR-002", description="Pedido exige produto cadastrado no estoque",
        depends_on="produto", enables="pedido", dependency_type="requires",
        required_operations=["create", "read"], is_critical=True,
    ),
    BusinessRule(
        rule_id="BR-003", description="Nota fiscal é gerada a partir do faturamento do pedido",
        depends_on="pedido", enables="nota", dependency_type="generates",
        required_operations=["read"], is_critical=True,
    ),
    BusinessRule(
        rule_id="BR-004", description="Duplicata (contas a receber) é gerada a partir da nota fiscal",
        depends_on="nota", enables="duplicata", dependency_type="generates",
        required_operations=["read"], is_critical=True,
    ),
    BusinessRule(
        rule_id="BR-005", description="Baixa de duplicata atualiza o contas a receber",
        depends_on="duplicata", enables="recebimento", dependency_type="updates",
        required_operations=["update"], is_critical=False,
    ),

    # Fluxo de Compra
    BusinessRule(
        rule_id="BR-006", description="Ordem de compra exige fornecedor cadastrado",
        depends_on="fornecedor", enables="compra", dependency_type="requires",
        required_operations=["create", "read"], is_critical=True,
    ),
    BusinessRule(
        rule_id="BR-007", description="Entrada de mercadoria atualiza estoque",
        depends_on="compra", enables="estoque", dependency_type="updates",
        required_operations=["update"], is_critical=True,
    ),

    # Fluxo de Cadastro
    BusinessRule(
        rule_id="BR-008", description="Filial depende de empresa matriz cadastrada",
        depends_on="empresa", enables="filial", dependency_type="requires",
        required_operations=["create", "read"], is_critical=True,
    ),
    BusinessRule(
        rule_id="BR-009", description="Funcionário exige filial/cargo cadastrados",
        depends_on="filial", enables="funcionario", dependency_type="requires",
        required_operations=["create", "read"], is_critical=False,
    ),

    # Fluxo de Estoque
    BusinessRule(
        rule_id="BR-010", description="Movimentação de estoque exige produto cadastrado",
        depends_on="produto", enables="estoque", dependency_type="requires",
        required_operations=["read"], is_critical=True,
    ),
    BusinessRule(
        rule_id="BR-011", description="Venda (pedido faturado) dá baixa no estoque",
        depends_on="pedido", enables="estoque", dependency_type="updates",
        required_operations=["update"], is_critical=False,
    ),
]

# ── Fluxos de negócio completos ──

_BUSINESS_FLOWS: List[BusinessFlow] = [
    BusinessFlow(
        flow_id="FLOW-VENDA",
        flow_name="Venda Completa (Cliente → Pedido → Nota → Duplicata)",
        description="Fluxo completo de uma venda no balcão da loja: cadastra o cliente, "
                    "abre o pedido, inclui itens, fatura, gera nota fiscal e duplicata, "
                    "e finalmente recebe o pagamento.",
        steps=[
            {"entity": "cliente", "operation": "create",
             "why": "Cadastrar o cliente comprador — sem cliente não há pedido"},
            {"entity": "produto", "operation": "create",
             "why": "Cadastrar o produto vendido — sem produto não há item no pedido"},
            {"entity": "pedido", "operation": "create",
             "why": "Abrir o pedido de venda vinculando cliente + itens + valores"},
            {"entity": "pedido", "operation": "update",
             "why": "Incluir itens no pedido, ajustar quantidades e descontos"},
            {"entity": "nota", "operation": "create",
             "why": "Faturar o pedido — gera a nota fiscal automaticamente"},
            {"entity": "duplicata", "operation": "create",
             "why": "Gerar duplicata (contas a receber) a partir da nota fiscal"},
            {"entity": "duplicata", "operation": "update",
             "why": "Registrar o recebimento (baixa) da duplicata"},
        ],
        triggers=["abertura de pedido no balcão", "venda online"],
    ),
    BusinessFlow(
        flow_id="FLOW-COMPRA",
        flow_name="Compra (Fornecedor → OC → Recebimento → Estoque)",
        description="Fluxo de compra de mercadoria: cadastra o fornecedor, emite ordem de compra, "
                    "recebe a mercadoria e atualiza o estoque.",
        steps=[
            {"entity": "fornecedor", "operation": "create",
             "why": "Cadastrar o fornecedor — sem fornecedor não há OC"},
            {"entity": "compra", "operation": "create",
             "why": "Emitir ordem de compra vinculando fornecedor, produtos e quantidades"},
            {"entity": "compra", "operation": "update",
             "why": "Registrar o recebimento da mercadoria"},
            {"entity": "estoque", "operation": "update",
             "why": "Atualizar estoque com a mercadoria recebida"},
        ],
        triggers=["necessidade de reposição", "pedido de compra"],
    ),
    BusinessFlow(
        flow_id="FLOW-CADASTRO",
        flow_name="Cadastro (Empresa → Filial → Funcionário)",
        description="Fluxo de cadastro organizacional: cadastra a empresa matriz, "
                    "depois as filiais, depois os funcionários de cada filial.",
        steps=[
            {"entity": "empresa", "operation": "create",
             "why": "Cadastrar a empresa matriz — raiz de toda a estrutura organizacional"},
            {"entity": "filial", "operation": "create",
             "why": "Cadastrar filial vinculada à empresa — lojas, fábricas, centros"},
            {"entity": "funcionario", "operation": "create",
             "why": "Cadastrar funcionário vinculado à filial — vendedores, gerentes, etc."},
        ],
        triggers=["abertura de nova loja", "contratação"],
    ),
]


# ── Mapeamento de aliases técnicos → nomes semânticos de negócio ──
# Os extratores descobrem nomes como "cad110", "forn_sul", "fat210".
# As regras de negócio usam nomes como "cliente", "fornecedor", "nota".
# Este mapa normaliza os nomes técnicos para os semânticos.

_ENTITY_ALIAS_MAP: Dict[str, List[str]] = {
    # Venda
    "cliente":    ["cad", "cliente", "clientes", "ccadpes", "cadtra", "cadusu"],
    "produto":    ["epp", "prod", "produto", "produtos", "ean", "audit_ean"],
    "pedido":     ["ped", "pedido", "pedidos"],
    "nota":       ["fat", "nota", "nf", "faturamento", "notafiscal"],
    "duplicata":  ["cre", "dup", "duplicata", "contasreceber", "receber"],
    "vendedor":   ["ven", "vendedor", "vendedores"],
    # Compra
    "fornecedor": ["forn", "fornecedor", "fornecedores", "forn_sul"],
    "compra":     ["cmp", "compra", "compras", "oc", "ordemcompra"],
    # Estoque
    "estoque":    ["est", "estoque", "movest", "inventario"],
    # Cadastro
    "empresa":    ["emp", "empresa", "empresas", "matriz"],
    "filial":     ["fil", "filial", "filiais", "loja", "uni"],
    "funcionario":["fun", "funcionario", "funcionarios", "usuario", "cadusu", "confususis"],
    # Financeiro
    "contapagar": ["cpa", "contapagar", "pagamento"],
    "banco":      ["ban", "banco", "bancos"],
    # Fiscal
    "tributacao": ["trib", "fiscal", "imposto", "icms", "ipi"],
}


def _normalize_entity_name(name: str) -> str:
    """Normaliza um nome técnico de entidade para seu nome semântico de negócio.

    Ex: 'forn_sul' → 'fornecedor', 'cad110' → 'cliente', 'fat210' → 'nota'
    """
    low = name.lower().strip()
    for semantic, aliases in _ENTITY_ALIAS_MAP.items():
        for alias in aliases:
            if low.startswith(alias) or low == alias:
                return semantic
    return low  # retorna original se não mapear


class BusinessRuleEngine:
    """Avalia entidades descobertas contra regras de negócio."""

    def __init__(self):
        self.rules = _BUSINESS_RULES
        self.flows = _BUSINESS_FLOWS

    def evaluate(self, discovered_entities: Set[str]) -> Dict:
        """Avalia cobertura de regras de negócio contra entidades descobertas.

        Returns:
            dict com: rules_evaluated, gaps, flows_covered, recommendations
        """
        # Normaliza nomes técnicos → semânticos
        normalized = {_normalize_entity_name(e) for e in discovered_entities}
        entities_lower = {e.lower() for e in normalized}
        gaps: List[BusinessGap] = []
        rules_ok = 0
        rules_broken = 0

        for rule in self.rules:
            dep_ok = rule.depends_on.lower() in entities_lower
            ena_ok = rule.enables.lower() in entities_lower

            if dep_ok and ena_ok:
                rules_ok += 1
            elif ena_ok and not dep_ok:
                # Gap: entidade existe mas depende de outra que não foi detectada
                rules_broken += 1
                severity = "critical" if rule.is_critical else "high"
                gaps.append(BusinessGap(
                    gap_id=f"GAP-{rule.rule_id}",
                    severity=severity,
                    description=f"{rule.description} — a entidade '{rule.depends_on}' não foi detectada no código",
                    missing_entity=rule.depends_on,
                    affected_flow=self._find_flow_for_entity(rule.enables),
                    impact=(
                        f"Sem '{rule.depends_on}' cadastrado, o sistema não consegue processar '{rule.enables}'. "
                        f"Isso pode indicar que o código não implementa esta operação ou o extrator não a detectou."
                    ),
                    recommendation=(
                        f"Verificar se existe código para CREATE de {rule.depends_on}. "
                        f"Se existir, revisar o extrator. Se não existir, implementar."
                    ),
                ))

        # Cobertura de fluxos
        flows_coverage = []
        for flow in self.flows:
            entities_in_flow = {s["entity"] for s in flow.steps}
            covered = entities_in_flow & entities_lower
            missing = entities_in_flow - entities_lower
            flows_coverage.append({
                "flow_id": flow.flow_id,
                "flow_name": flow.flow_name,
                "description": flow.description,
                "entities_covered": sorted(covered),
                "entities_missing": sorted(missing),
                "coverage_pct": round(len(covered) / max(1, len(entities_in_flow)) * 100, 1),
                "steps": [
                    {**s, "available": s["entity"].lower() in entities_lower}
                    for s in flow.steps
                ],
            })

        return {
            "rules_evaluated": len(self.rules),
            "rules_ok": rules_ok,
            "rules_broken": rules_broken,
            "gaps": [g.__dict__ for g in gaps],
            "flows_coverage": flows_coverage,
            "recommendation": self._build_recommendation(rules_ok, rules_broken, flows_coverage),
        }

    def _find_flow_for_entity(self, entity: str) -> str:
        for flow in self.flows:
            for step in flow.steps:
                if step["entity"].lower() == entity.lower():
                    return flow.flow_name
        return "Fluxo não identificado"

    def _build_recommendation(self, ok: int, broken: int, flows: List[Dict]) -> str:
        if broken == 0:
            return "Todas as regras de negócio estão cobertas. Fluxos completos validados."
        critical_gaps = sum(1 for f in flows if f["coverage_pct"] < 50)
        if critical_gaps:
            return (
                f"{broken} regras de negócio quebradas. "
                f"{critical_gaps} fluxos com cobertura abaixo de 50%. "
                f"Prioridade: verificar entidades faltantes nos fluxos críticos (Venda, Compra, Cadastro)."
            )
        return (
            f"{broken} regras de negócio quebradas. "
            f"Revisar código-fonte para confirmar se as dependências existem ou se precisam ser implementadas."
        )
