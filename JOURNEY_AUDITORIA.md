# Journey Generation — Auditoria e Roadmap

## Objetivo

A partir do Discovery, produzir automaticamente:
- Jornadas (sequências de telas)
- Waves (grupos de jornadas)
- Fluxos (caminhos condicionais)
- Casos de uso (cenários de negócio)

## O Que Já Existe

### Journey Engine (`gateway/dakota_gateway/synthetic/`)

| Componente | Arquivo | Funcionalidade | Status |
|------------|---------|---------------|--------|
| JourneyDefinition | `journey.py` | Modelo: jornada, passo, dataset binding | ✅ Funcional |
| JourneyInferencer | `journey_inferencer.py` | Infere jornadas de código-fonte (DO, PROCEDURE, MENU) | ✅ Funcional |
| JourneyBuilder | `journey_builder.py` | Constrói jornadas a partir de schemas | ✅ Funcional |
| JourneyVerifier | `journey_verifier.py` | Verifica execução de jornadas | ✅ Funcional |
| ExpandedInferencer | `expanded_inferencer.py` | Infere condicionais, dependências, transações | ✅ Funcional |
| MacroJourneyRunner | `macro_journey.py` | Orquestra múltiplas jornadas em sequência | ✅ Funcional |
| ScreenExplorer | `screen_explorer.py` | Constrói jornada de exploração | ✅ Funcional |

### Capacidades Atuais

1. **Inferência de código-fonte**:
   - Detecta chamadas DO PROGRAM, PROCEDURE, FUNCTION
   - Agrupa programas por módulo (prefixo 3 letras)
   - Cria jornada por módulo com passos sequenciais
   - Detecta títulos de tela

2. **Inferência expandida**:
   - Fluxos condicionais (IF/ELSE/ENDIF)
   - DO CASE com branches
   - Loops (DO WHILE, FOR/SCAN)
   - Dependências de dados (SEEK, SET RELATION, STORE TO)
   - Transações (BEGIN/COMMIT/ROLLBACK)

3. **Construção de jornadas**:
   - JourneyStep com ação (navigate, input, select, submit, wait, verify)
   - Input templates com placeholders
   - Dependências entre passos
   - Dataset bindings (screen → dataset)

## Possibilidade de Uso de Modelos

### Sequelize / ORM

**Viabilidade:** ALTA (para sistemas que usam ORM)

```javascript
// Exemplo: modelo Sequelize → Journey
const Cliente = sequelize.define('Cliente', {
  nome: DataTypes.STRING(100),
  cpf: DataTypes.STRING(14),
  email: DataTypes.STRING(100)
});

// Gera automaticamente:
// Journey: "Cadastro de Cliente"
//   Step 1: Menu → Cadastros → Clientes
//   Step 2: Tela de inclusão (nome, cpf, email)
//   Step 3: Submit (F10)
//   Step 4: Tela de confirmação
```

### DDL (Data Definition Language)

**Viabilidade:** MÉDIA-ALTA (para sistemas com DDL disponível)

```sql
CREATE TABLE clientes (
  id INTEGER PRIMARY KEY,
  nome VARCHAR(100) NOT NULL,
  cpf CHAR(14) UNIQUE,
  email VARCHAR(100),
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Infere:
-- Entity: clientes
-- Fields: id (integer, PK), nome (varchar, required),
--         cpf (char, unique), email (varchar), created_at (timestamp)
-- CRUD Journey: Include → Read → Update → Delete
```

### Metadados / Schemas

**Viabilidade:** ALTA (para sistemas com catálogo de metadados)

Fontes possíveis:
- Dicionário de dados Lianja/Recital
- Catálogo de programas (.prg, .src)
- Arquivos de schema (.dbc no FoxPro)
- Metadados de tela embutidos no código

### Dicionário Recital/Lianja

**Viabilidade:** ALTA (específico para o target Dakota)

O próprio código Recital contém metadados ricos:
- `@ SAY/GET` → campos de tela
- `VALID`, `PICTURE`, `RANGE` → validações
- `USE`, `SELECT` → entidades
- `DO`, `PROCEDURE` → navegação

## O Que Falta

### Geração a Partir de DDL (Alta Prioridade)

- [ ] Parser de DDL (CREATE TABLE, ALTER TABLE)
- [ ] Detecção de relacionamentos (FOREIGN KEY)
- [ ] Geração de ScreenSchema a partir de colunas
- [ ] Templates de tela por tipo de entidade

### Geração a Partir de ORM/Sequelize (Média Prioridade)

- [ ] Parser de modelos Sequelize
- [ ] Extração de validações
- [ ] Extração de associações (belongsTo, hasMany)
- [ ] Geração de jornada CRUD completa

### Jornadas de Negócio (Alta Prioridade)

- [ ] Templates por domínio (fiscal, estoque, financeiro, RH)
- [ ] Composição de jornadas atômicas em macro-jornadas
- [ ] Parametrização por perfil de usuário
- [ ] Jornadas de stress/volume

### Validação de Jornadas (Média Prioridade)

- [ ] Completude: todos os campos da entidade são cobertos?
- [ ] Cobertura: todos os fluxos condicionais são exercitados?
- [ ] Realismo: a jornada reflete uso real?

## Arquitetura Proposta

```
┌────────────────────────────────────────────────────────────┐
│                  JOURNEY GENERATION ENGINE                  │
│                                                            │
│  Fontes de Entrada:                                        │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────┐  │
│  │ Source   │ │ DDL      │ │ ORM      │ │ Metadados    │  │
│  │ Code     │ │ Schema   │ │ Models   │ │ Dicionário   │  │
│  │ (.prg)   │ │ (.sql)   │ │ (.js)    │ │ (.dcx)       │  │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └──────┬───────┘  │
│       └──────────────┴────────────┴──────────────┘         │
│                          │                                  │
│                          ▼                                  │
│               ┌──────────────────┐                         │
│               │  UNIFIED MODEL   │                         │
│               │  Entity + Screen │                         │
│               │  + Relationship  │                         │
│               └────────┬─────────┘                         │
│                        │                                    │
│          ┌─────────────┼─────────────┐                     │
│          ▼             ▼             ▼                     │
│   ┌──────────┐  ┌──────────┐  ┌──────────┐               │
│   │ CRUD     │  │ Business │  │ Stress   │               │
│   │ Journey  │  │ Journey  │  │ Journey  │               │
│   │ Generator│  │ Generator│  │ Generator│               │
│   └────┬─────┘  └────┬─────┘  └────┬─────┘               │
│        └──────────────┴──────────────┘                     │
│                       │                                     │
│                       ▼                                     │
│              ┌──────────────────┐                          │
│              │ JOURNEY CATALOG  │                          │
│              │ + Validator      │                          │
│              └──────────────────┘                          │
└────────────────────────────────────────────────────────────┘
```

## Métricas de Sucesso

- Jornadas geradas automaticamente: > 70% das entidades
- Cobertura de CRUD: > 80% das operações detectadas
- Precisão dos passos: > 90% (passos executáveis sem ajuste manual)
- Tempo de geração para 100 entidades: < 5 min
