# Synthetic Data — Auditoria e Arquitetura Proposta

## Objetivo

Transformar o Synthetic Engine em um gerador genérico, orientado por IA, capaz de:
1. Analisar código fonte
2. Identificar entidades
3. Identificar regras
4. Identificar validações
5. Identificar relacionamentos
6. Gerar massa válida

Sem necessidade de criar milhares de scripts específicos por entidade.

## Análise do Estado Atual

### Arquivos Existentes (29 arquivos em `gateway/dakota_gateway/synthetic/`)

#### Core Engine

| Arquivo | Linhas | Funcionalidade | Maturidade |
|---------|--------|---------------|------------|
| `engine.py` | ~200 | Orquestrador: analyze → infer → register → generate → template → replay | ✅ Maduro |
| `inferencer.py` | ~150 | Analisa código-fonte → ScreenSchema, FieldSchema | ✅ Funcional |
| `expanded_inferencer.py` | ~300 | Condicionais, dependências, transações | ✅ Funcional |
| `schema.py` | ~100 | Modelos: ScreenSchema, FieldSchema, SyntheticSchema | ✅ Maduro |

#### Providers (Geradores de Dados)

| Provider | Arquivo | Tipo de Dado |
|----------|---------|-------------|
| PersonNameProvider | `providers.py` | Nomes brasileiros (40+ nomes, 20+ sobrenomes) |
| CompanyNameProvider | `providers.py` | Razão social (prefixos, bases, sufixos) |
| CPFProvider | `providers.py` | CPF com dígito verificador |
| CNPJProvider | `providers.py` | CNPJ com dígito verificador |
| RGProvider | `providers.py` | RG |
| PhoneProvider | `providers.py` | Telefone |
| EmailProvider | `providers.py` | Email |
| AddressProvider | `providers.py` | Endereço |
| CEPProvider | `providers.py` | CEP |
| DateProvider | `providers.py` | Data |
| DatetimeProvider | `providers.py` | Data/hora |
| NumberProvider | `providers.py` | Número inteiro |
| DecimalProvider | `providers.py` | Número decimal |
| MoneyProvider | `providers.py` | Valor monetário |
| ChoiceProvider | `providers.py` | Escolha em lista |
| BooleanProvider | `providers.py` | Booleano |
| UUIDProvider | `providers.py` | UUID |
| SequenceProvider | `providers.py` | Sequencial |
| TextProvider | `providers.py` | Texto livre |
| CodeProvider | `providers.py` | Código alfanumérico |

O FieldSchema (`schema.py`) já fornece `inferred_provider_name()` que mapeia:
- `format` → provider (cpf, cnpj, rg, cep, email, phone, date, datetime)
- `datatype` → provider (person_name, company_name, number, decimal, money, boolean, uuid, code, text)
- `choices` → choice provider
- Campos adicionais: `min_value`, `max_value`, `country`, `validation_rules`

#### Dataset & Constraints

| Arquivo | Funcionalidade |
|---------|---------------|
| `dataset_builder.py` | Constrói datasets a partir de schemas e providers |
| `constraints.py` | Validação de constraints (required, unique, format, range) |
| `template_engine.py` | Templates de entrada com placeholders |
| `screen_registry.py` | Registro de telas no banco SQLite |

#### Journey & Stress

| Arquivo | Funcionalidade |
|---------|---------------|
| `journey.py` | Modelo de jornada |
| `journey_inferencer.py` | Inferência de jornadas |
| `journey_builder.py` | Construção de jornadas |
| `journey_verifier.py` | Verificação de execução |
| `capture_parametrizer.py` | Parametrização de capturas |
| `stress_runner.py` | Runner de stress test |
| `macro_journey.py` | Orquestração multi-módulo |

#### Validação & Relatório

| Arquivo | Funcionalidade |
|---------|---------------|
| `error_detector.py` | Detecta 50+ padrões de erro em tela |
| `screen_differ.py` | Diff de telas (esperado vs observado) |
| `homologation_report.py` | Relatório HTML/JSON |
| `junit_exporter.py` | Exportação JUnit XML |
| `csv_exporter.py` | Exportação CSV |

#### Operacional

| Arquivo | Funcionalidade |
|---------|---------------|
| `remote_executor.py` | Execução remota de jornadas |
| `scheduler.py` | Agendamento de execuções |
| `session_recorder.py` | Gravação de sessões |
| `snapshot_baseline.py` | Baseline de snapshots |
| `replay_adapter.py` | Adaptador para Replay Engine |
| `screen_explorer.py` | Exploração de telas |

## Limitações Atuais

### 1. Dependência de Providers Específicos

Hoje o sistema mapeia `datatype` → `provider` manualmente. Ex: se o campo é "cpf", usa `CPFProvider`. Isso funciona para campos bem nomeados, mas falha para:
- Campos com nomes não padronizados ("documento", "registro")
- Campos com tipos compostos
- Campos com regras de negócio complexas

### 2. Falta de Compreensão Semântica

O sistema não entende o significado dos dados:
- Não sabe que "cliente" tem "nome", "cpf", "endereco"
- Não sabe que "pedido" referencia "cliente" e "produto"
- Não sabe que "data_entrega" > "data_pedido"

### 3. Relacionamentos Não Automatizados

- FK detection existe no SQL extractor, mas não é usada na geração
- Relacionamentos entre entidades não geram dados consistentes
- Lookup tables são detectadas mas não populadas automaticamente

### 4. Regras de Negócio Implícitas

- Validações cross-field não são consideradas
- Regras de negócio em código não são extraídas para constraints
- Formato de dados (máscaras) não é totalmente aproveitado

## Arquitetura Proposta — AI-Driven Synthetic Engine

```
┌─────────────────────────────────────────────────────────────────┐
│                 AI-DRIVEN SYNTHETIC ENGINE v2                    │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │                    INPUT LAYER                               ││
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────┐   ││
│  │  │ Source   │ │ DDL/SQL  │ │ ORM      │ │ Business     │   ││
│  │  │ Code     │ │ Schema   │ │ Models   │ │ Rules Doc    │   ││
│  │  │ (.prg)   │ │ (.sql)   │ │ (.js)    │ │ (.md/.txt)   │   ││
│  │  └────┬─────┘ └────┬─────┘ └────┬─────┘ └──────┬───────┘   ││
│  └───────┴────────────┴────────────┴──────────────┴───────────┘│
│                          │                                       │
│                          ▼                                       │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │              SEMANTIC ANALYSIS LAYER                         ││
│  │                                                              ││
│  │  ┌─────────────┐  ┌──────────────┐  ┌──────────────────┐   ││
│  │  │ Entity      │  │ Relationship │  │ Constraint       │   ││
│  │  │ Extractor   │  │ Mapper       │  │ Inference Engine │   ││
│  │  │             │  │              │  │                  │   ││
│  │  │ - Nome      │  │ - FK/PK      │  │ - VALID clause   │   ││
│  │  │ - Campos    │  │ - Lookup     │  │ - PICTURE mask   │   ││
│  │  │ - Tipos     │  │ - 1:N, N:M   │  │ - RANGE limits   │   ││
│  │  │ - Storage   │  │ - Cascade    │  │ - Cross-field    │   ││
│  │  └──────┬──────┘  └──────┬───────┘  └────────┬─────────┘   ││
│  │         └────────────────┴──────────────────┘              ││
│  │                          │                                   ││
│  │                          ▼                                   ││
│  │               ┌──────────────────┐                          ││
│  │               │  UNIFIED ENTITY  │                          ││
│  │               │  KNOWLEDGE GRAPH │                          ││
│  │               └──────────────────┘                          ││
│  └─────────────────────────────────────────────────────────────┘│
│                          │                                       │
│                          ▼                                       │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │                 GENERATION LAYER                             ││
│  │                                                              ││
│  │  ┌────────────────┐  ┌──────────────┐  ┌───────────────┐   ││
│  │  │ Smart Provider │  │ Relationship │  │ Consistency   │   ││
│  │  │ Router         │  │ Resolver     │  │ Validator     │   ││
│  │  │                │  │              │  │               │   ││
│  │  │ field → type   │  │ FK values    │  │ Cross-entity  │   ││
│  │  │ type → provider│  │ from parent  │  │ rules check   │   ││
│  │  │ context → var  │  │ datasets     │  │ uniqueness    │   ││
│  │  └────────────────┘  └──────────────┘  └───────────────┘   ││
│  └─────────────────────────────────────────────────────────────┘│
│                          │                                       │
│                          ▼                                       │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │                   OUTPUT LAYER                               ││
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────┐   ││
│  │  │ Dataset  │ │ Template │ │ Journey  │ │ Stress       │   ││
│  │  │ JSON/CSV │ │ Engine   │ │ Binding  │ │ Config       │   ││
│  │  └──────────┘ └──────────┘ └──────────┘ └──────────────┘   ││
│  └─────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────┘
```

## Plano de Implementação

### Fase 1: Smart Provider Router

Substituir o mapeamento manual `field_name → provider` por inferência contextual:

```python
class SmartProviderRouter:
    """Roteia campo para provider baseado em:
    1. Nome do campo (análise semântica)
    2. Tipo de dados (inferido do DDL/código)
    3. Contexto da entidade (ex: cliente.cpf vs fornecedor.cnpj)
    4. Constraints (PICTURE, RANGE, VALID)
    5. Relacionamentos (FK → valor da entidade pai)
    """

    def resolve(self, field: FieldSchema, entity: EntityDefinition) -> DataProvider:
        # 1. Matching por nome (CPF, CNPJ, email, etc.)
        provider = self._match_by_name(field.name)
        if provider:
            return provider

        # 2. Matching por tipo (integer, decimal, date, etc.)
        provider = self._match_by_datatype(field.datatype)
        if provider:
            return provider

        # 3. Matching por constraint (PICTURE @R 999.999.999-99 → CPF)
        provider = self._match_by_constraint(field)
        if provider:
            return provider

        # 4. Matching por contexto (entidade "clientes" + campo "documento" → CPF)
        provider = self._match_by_context(field, entity)
        if provider:
            return provider

        # 5. Fallback: text provider
        return TextProvider()
```

### Fase 2: Relationship Resolver

Gerar dados que respeitem relacionamentos:

```python
class RelationshipResolver:
    """Resolve relacionamentos entre entidades na geração de dados."""

    def resolve_fk(self, fk_field: FieldSchema, parent_dataset: Dataset) -> list[Any]:
        """Para campo FK, retorna valores do dataset pai."""
        return [r.data[parent_dataset.pk_field] for r in parent_dataset.records]

    def resolve_lookup(self, lookup_table: str) -> list[str]:
        """Para campo com lookup table, retorna valores válidos."""
        return self._extract_lookup_values(lookup_table)

    def resolve_cross_entity(self, field: FieldSchema, related_entity: EntityDefinition) -> Any:
        """Campo que depende de outra entidade (ex: valor_total = sum(itens.valor))."""
        pass
```

### Fase 3: Consistency Validator

Validar consistência cross-entity:

```python
class ConsistencyValidator:
    """Valida consistência entre datasets relacionados."""

    def validate(self, datasets: dict[str, Dataset]) -> list[ConsistencyError]:
        errors = []
        # FK values exist in parent
        # Unique constraints across related entities
        # Date ordering (data_entrega >= data_pedido)
        # Value ranges (total_pedido = sum(itens))
        return errors
```

## Benefícios Esperados

| Métrica | Atual | Proposto |
|---------|-------|----------|
| Entidades cobertas sem script manual | ~20% | >80% |
| Precisão dos dados gerados | ~70% | >95% |
| Tempo para gerar dados de novo sistema | 2-3 dias | < 1 hora |
| Respeito a FK/relationships | 0% | >90% |
| Detecção automática de regras | Manual | Automática |
