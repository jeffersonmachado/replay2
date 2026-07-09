"""Relatorio narrativo de decisoes de jornadas — contextualizado em negocio.

Cada passo da jornada recebe uma justificativa descritiva que explica
o PORQUE em linguagem de negocio, nao tecnica.

Exemplos:
- "Cadastro o cliente X porque para gerar um pedido o sistema exige cliente ativo"
- "Preencho os campos do pedido: numero, data, cliente, valor"
- "Consulto o pedido recem-criado para validar a persistencia"
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

from ..source_analyzer.crud_detector import CRUDCoverage
from ..source_analyzer.field_classifier import FieldClassification


@dataclass
class FieldSelectionReason:
    field_name: str = ""
    selected: bool = False
    reasons: list[str] = field(default_factory=list)


# ── Contextos de negocio por entidade ──
_ENTITY_CONTEXT: Dict[str, Dict[str, str]] = {
    "cliente": {
        "domain": "Cadastro de clientes",
        "narrative": "cliente",
        "article": "o",
        "create_story": "Cadastro o cliente {fields} porque toda venda, pedido ou duplicata exige um cliente vinculado.",
        "fill_story": "Preencho os dados do cliente: {fields}. O CPF/CNPJ e obrigatorio para emissao de notas fiscais.",
        "query_story": "Consulto o cliente recem-cadastrado para confirmar que os dados foram persistidos corretamente no cadastro.",
        "update_story": "Atualizo o endereco ou telefone do cliente simulando uma alteracao cadastral rotineira.",
        "delete_story": "Excluo o cliente de teste para nao poluir a base de producao com registros ficticios.",
    },
    "fornecedor": {
        "domain": "Cadastro de fornecedores",
        "narrative": "fornecedor",
        "article": "o",
        "create_story": "Cadastro o fornecedor {fields} porque para emitir uma OC o sistema exige fornecedor ativo.",
        "fill_story": "Preencho os dados do fornecedor: {fields}. O CNPJ e obrigatorio para emissao de OCs.",
        "query_story": "Consulto o fornecedor recem-cadastrado para validar a persistencia dos dados.",
        "update_story": "Atualizo dados cadastrais do fornecedor simulando uma alteracao de endereco ou contato.",
        "delete_story": "Excluo o fornecedor de teste para manter a base limpa.",
    },
    "produto": {
        "domain": "Cadastro de produtos",
        "narrative": "produto",
        "article": "o",
        "create_story": "Cadastro o produto {fields} porque para incluir itens em um pedido o produto precisa existir no estoque.",
        "fill_story": "Preencho os dados do produto: {fields}. O codigo e obrigatorio para identificacao no sistema.",
        "query_story": "Consulto o produto recem-cadastrado para validar a persistencia.",
        "update_story": "Atualizo o preco ou estoque do produto simulando uma atualizacao de tabela.",
        "delete_story": "Excluo o produto de teste.",
    },
    "pedido": {
        "domain": "Pedidos de venda",
        "narrative": "pedido",
        "article": "o",
        "create_story": "Gero um pedido para o cliente {fields} simulando uma venda real no balcao da loja.",
        "fill_story": "Preencho o pedido: {fields}. O cliente e os itens sao obrigatorios para o faturamento.",
        "query_story": "Consulto o pedido recem-gerado para validar que itens, valores e cliente estao corretos.",
        "update_story": "Altero o pedido (ex: incluo mais itens ou ajusto quantidade) simulando alteracao antes do faturamento.",
        "delete_story": "Cancelo o pedido de teste simulando uma desistencia do cliente.",
    },
    "nota": {
        "domain": "Notas fiscais / faturamento",
        "narrative": "nota fiscal",
        "article": "a",
        "create_story": "Emissao de nota fiscal — o faturamento do pedido gera a nota automaticamente.",
        "fill_story": "Preencho os dados da nota: {fields}. Natureza de operacao e CFOP sao obrigatorios.",
        "query_story": "Consulto a nota fiscal emitida para validar os dados fiscais e o valor total.",
        "update_story": "Corrijo a nota fiscal (ex: ajuste de aliquota) simulando uma carta de correcao.",
        "delete_story": "Cancelo a nota fiscal de teste.",
    },
    "duplicata": {
        "domain": "Contas a receber / duplicatas",
        "narrative": "duplicata",
        "article": "a",
        "create_story": "Gero duplicata para o cliente — cada nota fiscal faturada gera duplicatas automaticamente.",
        "fill_story": "Preencho a duplicata: {fields}. Vencimento e valor sao calculados a partir da nota.",
        "query_story": "Consulto a duplicata gerada para validar vencimento, valor e cliente.",
        "update_story": "Altero o vencimento da duplicata simulando uma renegociacao de prazo.",
        "delete_story": "Excluo a duplicata de teste.",
    },
    "estoque": {
        "domain": "Controle de estoque",
        "narrative": "item de estoque",
        "article": "o",
        "create_story": "Registro movimentacao de estoque — entrada de mercadoria no deposito.",
        "fill_story": "Preencho a movimentacao: {fields}. Produto, quantidade e deposito sao obrigatorios.",
        "query_story": "Consulto o saldo do produto apos a movimentacao para validar a atualizacao.",
        "update_story": "Corrijo a quantidade da movimentacao simulando um ajuste de inventario.",
        "delete_story": "Estorno a movimentacao de teste.",
    },
    "funcionario": {
        "domain": "Cadastro de funcionarios",
        "narrative": "funcionario",
        "article": "o",
        "create_story": "Cadastro o funcionario {fields} — necessario para controle de ponto, comissao e folha.",
        "fill_story": "Preencho os dados: {fields}. CPF, cargo e data de admissao sao obrigatorios.",
        "query_story": "Consulto o funcionario recem-cadastrado para validar a persistencia.",
        "update_story": "Atualizo o salario ou cargo simulando uma promocao ou dissidio.",
        "delete_story": "Excluo o funcionario de teste.",
    },
    "empresa": {
        "domain": "Cadastro de empresas / filiais",
        "narrative": "empresa",
        "article": "a",
        "create_story": "Cadastro a empresa {fields} — necessario para configurar filiais e parametros fiscais.",
        "fill_story": "Preencho os dados: {fields}. CNPJ e inscricao estadual sao obrigatorios.",
        "query_story": "Consulto a empresa recem-cadastrada.",
        "update_story": "Atualizo parametros fiscais da empresa.",
        "delete_story": "Excluo a empresa de teste.",
    },
}


def _narrative_for(entity: str, step_type: str, fields_list: List[str], prefix: str) -> str:
    """Gera justificativa narrativa contextualizada para um passo da jornada."""
    entity_lower = entity.lower()
    fields_str = ", ".join(fields_list[:4]) if fields_list else "dados obrigatorios"

    # Busca contexto especifico da entidade
    ctx = None
    for key, value in _ENTITY_CONTEXT.items():
        if key in entity_lower or entity_lower in key:
            ctx = value
            break

    if ctx:
        story_key = f"{step_type}_story"
        if story_key in ctx:
            return ctx[story_key].format(fields=fields_str)

    # Fallback narrativo generico (mas ainda contextual)
    narratives = {
        "menu": f"Acesso o menu de {prefix.upper()} — ponto de entrada para todas as operacoes de {entity}. "
                f"Sem este passo, o operador nao consegue navegar ate a tela desejada.",
        "include": f"Inicio a inclusao de um novo registro em {entity}. "
                   f"Este passo e necessario para criar dados de teste que serao usados nas validacoes seguintes.",
        "fill": f"Preencho os campos do formulario de {entity}: {fields_str}. "
                f"Cada campo e preenchido com dados sinteticos realistas gerados pelo DataProvider "
                f"(CPF/CNPJ validos, nomes, enderecos, valores).",
        "confirm": f"Confirmo a operacao em {entity} — pressiono F10 para gravar. "
                   f"O sistema valida os dados e persiste o registro. Se houver erro de validacao, a jornada falha aqui.",
        "query": f"Consulto {entity} para verificar que o registro foi criado/alterado com sucesso. "
                 f"A consulta localiza pelo campo mais relevante e confirma a integridade dos dados.",
        "update": f"Altero {entity} — modifico um ou mais campos do registro recem-criado. "
                  f"Esta operacao valida que o sistema permite alteracoes e mantem a integridade referencial.",
        "delete": f"Excluo {entity} — removo o registro de teste para nao deixar residuos na base. "
                  f"Este passo e essencial para que a jornada seja repetivel sem conflitos de chave.",
    }
    return narratives.get(step_type, f"Executo operacao {step_type} em {entity}")


def _entity_domain(entity: str, prefix: str) -> str:
    """Retorna o dominio de negocio da entidade."""
    for key, value in _ENTITY_CONTEXT.items():
        if key in entity.lower() or entity.lower() in key:
            return value["domain"]
    return f"Modulo {prefix.upper()}"


# ── Mantendo a classe JourneyReport e funcao build_journey_report compativeis ──

@dataclass
class JourneyReport:
    journey_id: str = ""
    entity_name: str = ""
    generated: bool = False
    entity_selection: dict = field(default_factory=dict)
    steps_summary: list[dict] = field(default_factory=list)
    field_decisions: list[FieldSelectionReason] = field(default_factory=list)
    program_names: dict = field(default_factory=dict)
    dataset_bindings: Dict[str, str] = field(default_factory=dict)
    total_steps: int = 0
    input_steps: int = 0
    verify_steps: int = 0
    generated_at: str = ""

    def __post_init__(self):
        if not self.generated_at:
            self.generated_at = datetime.now(timezone.utc).isoformat()

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    def to_dict(self) -> dict:
        return {
            "journey_id": self.journey_id,
            "entity_name": self.entity_name,
            "generated": self.generated,
            "entity_selection": self.entity_selection,
            "steps_summary": self.steps_summary,
            "field_decisions": [
                {"field_name": fd.field_name, "selected": fd.selected, "reasons": fd.reasons}
                for fd in self.field_decisions
            ],
            "program_names": self.program_names,
            "dataset_bindings": self.dataset_bindings,
            "total_steps": self.total_steps,
            "input_steps": self.input_steps,
            "verify_steps": self.verify_steps,
            "generated_at": self.generated_at,
        }


def build_journey_report(
    entity_name: str,
    coverage: Optional[CRUDCoverage],
    field_classifications: List[FieldClassification],
    module_prefix: str,
    program_names: Dict[str, str],
    steps_count: int,
    input_count: int,
    verify_count: int,
    dataset_bindings: Dict[str, str],
) -> JourneyReport:
    report = JourneyReport(
        journey_id=f"crud_{entity_name.lower()}",
        entity_name=entity_name,
        generated=coverage.is_complete if coverage else False,
        total_steps=steps_count, input_steps=input_count,
        verify_steps=verify_count, dataset_bindings=dataset_bindings,
        program_names=program_names,
    )

    # Decisao 1: Selecao da entidade
    if coverage:
        domain = _entity_domain(entity_name, module_prefix)
        report.entity_selection = {
            "has_create": coverage.has_create,
            "has_read": coverage.has_read,
            "has_update": coverage.has_update,
            "has_delete": coverage.has_delete,
            "is_complete": coverage.is_complete,
            "completeness_score": getattr(coverage, "completeness_score", 100.0 if coverage.is_complete else 0.0),
            "total_operations": coverage.total_operations,
            "missing_operations": coverage.missing_operations,
            "domain": domain,
            "justification": (
                f"CRUD completo detectado em {domain} — a entidade {entity_name} possui todas as operacoes "
                f"(CREATE, READ, UPDATE, DELETE) necessarias para uma jornada de validacao completa. "
                f"Isto significa que o sistema permite cadastrar, consultar, alterar e excluir registros deste tipo."
                if coverage.is_complete
                else f"CRUD incompleto em {domain} — faltam: {', '.join(coverage.missing_operations)}. "
                     f"A entidade {entity_name} nao possui todas as operacoes necessarias para uma jornada completa."
            ),
        }
    else:
        report.entity_selection = {
            "justification": "Sem dados de cobertura CRUD — entidade nao analisada",
        }

    # Decisao 2: Passos narrativos
    selected_fields = [fc.field_name for fc in field_classifications if fc.is_required or fc.semantic_category]
    step_types = ["menu", "include", "fill", "confirm", "query", "update", "delete"]
    for i, st in enumerate(step_types):
        report.steps_summary.append({
            "order": i + 1,
            "type": st,
            "program": program_names.get(st, f"{module_prefix}_{st}"),
            "justification": _narrative_for(entity_name, st, selected_fields, module_prefix),
        })

    # Decisao 3: Campos
    for fc in field_classifications:
        reasons: list[str] = []
        selected = False
        if fc.is_required:
            reasons.append(f"Campo obrigatorio — o sistema rejeita o registro sem este dado")
            selected = True
        if fc.semantic_category:
            cat_names = {"cpf": "CPF", "cnpj": "CNPJ", "email": "e-mail", "phone": "telefone",
                         "name": "nome/razao social", "address": "endereco", "city": "cidade",
                         "state": "UF", "date": "data", "money": "valor", "code": "codigo",
                         "boolean": "flag", "enum": "tipo/situacao", "text": "texto",
                         "quantity": "quantidade", "measure": "medida", "foreign_key": "FK"}
            cat_label = cat_names.get(fc.semantic_category, fc.semantic_category)
            reasons.append(f"Classificado como {cat_label} (confianca {fc.confidence:.0%}) — dado relevante para validacao")
            selected = True
        if fc.has_unique_constraint:
            reasons.append("Chave unica — identifica unicamente o registro")
            selected = True
        if fc.lookup_entity:
            reasons.append(f"Referencia {fc.lookup_entity} — vincula este registro a outra entidade do sistema")
        if not selected:
            reasons.append("Campo opcional — nao incluido no preenchimento automatico para manter a jornada simples")

        report.field_decisions.append(FieldSelectionReason(
            field_name=fc.field_name, selected=selected, reasons=reasons,
        ))

    # Campo de localizacao
    if field_classifications:
        first_fc = field_classifications[0]
        report.field_decisions.insert(0, FieldSelectionReason(
            field_name=first_fc.field_name,
            selected=True,
            reasons=[
                f"Campo principal de busca — usado para localizar o registro na consulta e alteracao",
                f"Classificado como {first_fc.semantic_category}" if first_fc.semantic_category else "Primeiro campo da entidade",
            ],
        ))

    return report
