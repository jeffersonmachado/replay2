# Discovery Automático — Auditoria e Roadmap

## Objetivo

Capacidade de analisar aplicações alvo automaticamente, sem necessidade de scripts manuais por sistema.

## O Que Já Existe

### Source Analyzer (`gateway/dakota_gateway/source_analyzer/`)

| Componente | Arquivo | Funcionalidade | Status |
|------------|---------|---------------|--------|
| SourceParser | `parser.py` | Orquestrador de análise de código-fonte | ✅ Funcional |
| SQLExtractor | `sql_extractor.py` | Extrai entidades de SQL (INSERT, UPDATE, SELECT, DELETE, JOIN) | ✅ Funcional |
| ISAMExtractor | `isam_extractor.py` | Extrai entidades de arquivos ISAM | ✅ Funcional |
| DBFExtractor | `dbf_extractor.py` | Extrai entidades de DBF/xBase | ✅ Funcional |
| RecitalExtractor | `recital_extractor.py` | Extrai entidades de código Recital | ✅ Funcional |
| ScreenExtractor | `screen_extractor.py` | Extrai definições de tela (@ SAY/GET, PROMPT, TITLE) | ✅ Funcional |
| ValidationExtractor | `validation_extractor.py` | Extrai regras de validação (VALID, PICTURE, RANGE) | ✅ Funcional |
| EntityCatalog | `entity_catalog.py` | Modelo de dados: Entity, Field, Operation, Screen | ✅ Funcional |

### Screen Explorer (`gateway/dakota_gateway/synthetic/screen_explorer.py`)

| Funcionalidade | Status |
|---------------|--------|
| Modo passivo (análise de código-fonte) | ✅ Funcional |
| Modo ativo (conexão real e navegação) | ⚠️ Estrutura existe, não testado |
| Detecção de campos (regex patterns) | ✅ Funcional |
| Detecção de menus (padrões numéricos) | ✅ Funcional |
| Construção de jornada de exploração | ✅ Funcional |

### Capacidades Atuais

1. **Descoberta de entidades** a partir de:
   - SQL (CREATE TABLE, INSERT, UPDATE, SELECT, DELETE)
   - ISAM (arquivos .dbf, índices .ntx/.cdx)
   - Código Recital (USE, APPEND, REPLACE, SCATTER, GATHER)
   - Definições de tela (@ SAY/GET)

2. **Extração de metadados**:
   - Nome da entidade
   - Campos com tipo inferido
   - Índices
   - Operações CRUD
   - Constraints/validações

3. **Extração de telas**:
   - Nome do programa
   - Título da tela
   - Campos com prompts
   - Assinatura de tela

## O Que Falta

### Descoberta de Formulários (Alta Prioridade)

- [ ] **Detecção de tipo de campo**: numérico, data, hora, máscara
- [ ] **Detecção de obrigatoriedade**: campo required vs opcional
- [ ] **Detecção de domínio**: lookup tables, valores permitidos
- [ ] **Detecção de formatação**: PICTURE, RANGE, VALID
- [ ] **Relacionamento campo-entidade**: qual campo da tela mapeia para qual coluna

### Descoberta de Menus (Alta Prioridade)

- [ ] **Hierarquia de menus**: menu principal → submenus → programas
- [ ] **Navegação por teclas**: atalhos, F-keys, ESC
- [ ] **Menus dinâmicos**: construídos por permissão/perfil
- [ ] **Menus em múltiplos arquivos**: referências cruzadas

### Descoberta de Workflows (Média Prioridade)

- [ ] **Sequências de telas**: fluxo natural de navegação
- [ ] **Condicionais**: IF/ELSE, DO CASE
- [ ] **Loops**: DO WHILE, FOR, SCAN
- [ ] **Transações**: BEGIN/COMMIT/ROLLBACK
- [ ] **Dependências entre telas**: campo da tela A → campo da tela B

### Descoberta de CRUDs (Alta Prioridade)

- [ ] **Identificação de CRUD completo**: Create, Read, Update, Delete para cada entidade
- [ ] **Mapeamento tela→operação**: qual tela faz INSERT, qual faz UPDATE
- [ ] **Fluxo de inclusão**: include → confirma → grava
- [ ] **Fluxo de alteração**: localiza → edita → confirma → grava
- [ ] **Fluxo de exclusão**: localiza → confirma exclusão

### Discovery Ativo (Média Prioridade)

- [ ] **Conexão real ao sistema**: SSH/Telnet
- [ ] **Navegação automática**: percorrer menus, abrir programas
- [ ] **Captura de telas**: screenshot de cada tela visitada
- [ ] **Detecção de campos por OCR**: alternativa a regex para telas complexas
- [ ] **Mapeamento completo**: árvore de navegação do sistema

## Como Implementar

### Fase 1: Enriquecer Source Analyzer (Sprint 2)

```
source_analyzer/
├── parser.py           # (existente) melhorar orquestração
├── sql_extractor.py    # (existente) adicionar FK detection
├── isam_extractor.py   # (existente) adicionar multi-arquivo
├── dbf_extractor.py    # (existente) adicionar memo fields
├── recital_extractor.py # (existente) adicionar DO CASE, procedimentos
├── screen_extractor.py  # (existente) adicionar hierarquia de menus
├── validation_extractor.py # (existente) adicionar cross-field validation
├── entity_catalog.py    # (existente) adicionar relationships
├── crud_detector.py     # NOVO: identifica CRUDs completos
├── menu_analyzer.py     # NOVO: analisa hierarquia de menus
├── workflow_detector.py # NOVO: detecta sequências de telas
├── field_classifier.py  # NOVO: classifica tipos de campo
└── relationship_mapper.py # NOVO: mapeia relacionamentos entre entidades
```

### Fase 2: Discovery Ativo (Sprint 3)

```
synthetic/
├── screen_explorer.py   # (existente) implementar modo ativo
├── active_explorer.py   # NOVO: navegação automática
├── screen_capturer.py   # NOVO: captura de telas em exploração
└── navigation_tree.py   # NOVO: árvore de navegação completa
```

### Fase 3: Integração (Sprint 4)

- Source Analyzer + Screen Explorer → Discovery Report
- Discovery Report → Journey Generation
- Journey Generation → Synthetic Data
- Synthetic Data → Replay

## Métricas de Sucesso

- Cobertura de entidades detectadas vs documentadas: > 90%
- Precisão na classificação de campos: > 85%
- CRUDs completos identificados: > 80%
- Tempo de discovery para sistema médio (500 programas): < 10 min
