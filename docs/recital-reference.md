# Documentação Recital — Relevante para o Projeto Replay2

> Fontes: https://www.recitalsoftware.com/wiki/ (Recital Documentation Wiki)
> Data: 2026-06-26

---

## 1. Modelo de Dados Recital

### Bancos e Tabelas
- **Database**: diretório com catálogo. Criado com `CREATE DATABASE`, aberto com `OPEN DATABASE`
- **Free tables**: tabelas soltas (sem database), acessadas pelo path físico
- **Cursor/workarea**: cada tabela aberta ocupa um cursor (numerado 1..N ou A..Z). O cursor 0 seleciona o menor disponível
- **Alias**: nome lógico da tabela no cursor. Campo de outro cursor: `alias->campo` ou `alias.campo`

### Tipos de arquivo de tabela
| Formato | Descrição |
|---------|-----------|
| RECITAL (padrão) | Formato nativo |
| CLIPPER / CLIPPER5 | Compatível Clipper |
| DBASE3 / DBASE4 | Compatível dBASE |
| FOXPLUS / FOXPRO / VFP | Compatível FoxPro |

→ **Dakota usa `.dbo`** que provavelmente é CLIPPER5 ou DBASE3 compatível

---

## 2. Comandos de Acesso a Dados (Navigational)

Estes são os comandos que o **RecitalExtractor** precisa detectar para inferir entidades:

### Abertura/Fechamento de Tabela
| Comando | Sintaxe | Score |
|---------|---------|-------|
| `USE` | `USE <tabela> [ALIAS <alias>] [IN <cursor>] [INDEX <tag>] [ORDER <tag>]` | 8 |
| `CLOSE` | `CLOSE <alias>` | 2 |
| `SELECT` | `SELECT <cursor \| alias>` | 3 |

### Navegação
| Comando | Função |
|---------|--------|
| `GOTO <n>` / `GO TOP` / `GO BOTTOM` | Posicionamento absoluto |
| `SKIP <n>` | Navegação relativa |
| `SEEK <key>` | Busca por índice |
| `LOCATE` | Busca sequencial |

### Leitura/Escrita
| Comando | Função | Score CRUD |
|---------|--------|------------|
| `APPEND BLANK` | Insere registro vazio | CREATE |
| `APPEND FROM` | Importa dados | CREATE |
| `GATHER FROM <array\|memvar>` | Atualiza registro atual | UPDATE |
| `REPLACE <campo> WITH <valor>` | Atualiza campos | UPDATE |
| `SCATTER TO <array\|memvar>` | Lê registro para array | READ |
| `DELETE` | Marca registro para deleção | DELETE |
| `PACK` | Remove registros marcados | DELETE |
| `ZAP` | Remove todos os registros | DELETE |
| `RECALL` | Desmarca deleção | UPDATE |

### Relacionamentos (FK)
| Comando | Sintaxe | Relevância |
|---------|---------|------------|
| `SET RELATION TO <key> INTO <alias>` | Define FK entre tabelas | **CRÍTICO** para FlowInferencer |
| `SET SKIP TO <alias>` | Habilita scan 1:N | Complementar |

### Agregação
| Comando | Função |
|---------|--------|
| `COUNT` | Conta registros |
| `SUM` | Soma valores |
| `AVERAGE` | Média |
| `TOTAL ON <key> TO <tabela>` | Agrupa em nova tabela |

### Consulta
| Comando | Função |
|---------|--------|
| `SCAN...ENDSCAN` | Loop com filtro |
| `DISPLAY` / `LIST` | Exibe registros |
| `SET FILTER TO` | Filtro persistente |

---

## 3. Comandos SQL

Recital suporta SQL além do acesso navegacional:

| Comando | Relevância |
|---------|------------|
| `CREATE TABLE (<col> <type>, ...)` | Detecta estrutura de tabela |
| `ALTER TABLE` | Modifica estrutura |
| `INSERT` | Insere registros |
| `SELECT ... FROM <tabela>` | Consulta — **já detectado** pelo extrator (score 4) |
| `UPDATE` | Atualiza |
| `DELETE FROM` | Remove |

### DDL — Estrutura de Tabela
```sql
CREATE TABLE customer (
  account_no CHAR(5) DESCRIPTION "Account Code",
  title CHAR(3),
  last_name CHAR(16),
  balance DECIMAL(11,2),
  start_date DATE DEFAULT date(),
  notes LONG VARCHAR
)
```

Constraints: `PRIMARY KEY`, `FOREIGN KEY`, `CHECK`, `DEFAULT`, `UNIQUE`, `NOT NULL`

---

## 4. Tipos de Dados Recital

| Tipo | Descrição | Tamanho |
|------|-----------|---------|
| `CHAR(n)` | Caractere fixo | 1-255 |
| `VARCHAR(n)` | Caractere variável | 1-255 |
| `LONG VARCHAR` | Texto longo | ilimitado |
| `NUMERIC(p,s)` | Numérico | até 32 dígitos |
| `DECIMAL(p,s)` | Decimal | até 32 dígitos |
| `INT` | Inteiro 4 bytes | - |
| `BIGINT` | Inteiro 8 bytes | - |
| `SMALLINT` | Inteiro 2 bytes | - |
| `FLOAT` | Ponto flutuante | - |
| `DOUBLE` | Dupla precisão | - |
| `DATE` | Data | 8 bytes |
| `DATETIME` | Data/hora | 8 bytes |
| `LOGICAL` | Booleano | 1 byte |
| `MEMO` | Memo texto | ilimitado |
| `OBJECT` | OLE/Object | - |
| `GENERAL` | General | - |

---

## 5. Comandos de Interface (UI)

| Comando | Função |
|---------|--------|
| `@ row,col SAY` | Exibe texto |
| `@ row,col GET` | Campo editável |
| `@ row,col MENU` | Menu |
| `DEFINE WINDOW` | Cria janela |
| `DEFINE MENU` | Cria menu |
| `DEFINE POPUP` | Popup menu |
| `BROWSE` | Grid editável |
| `EDIT` | Edição formulário |
| `APPEND` | Tela de inclusão |

---

## 6. Ambiente e Configuração

| Comando | Função |
|---------|--------|
| `SET TALK ON/OFF` | Controle de saída |
| `SET DELETED ON/OFF` | Visibilidade de deletados |
| `SET EXCLUSIVE ON/OFF` | Modo de abertura |
| `SET ORDER TO <tag>` | Índice mestre |
| `SET RELATION TO/OFF` | Relacionamentos |
| `SET AUTOCATALOG ON/OFF` | Catálogo automático |
| `DB_DATADIR` | Diretório de dados padrão |

---

## 7. Implicações para o Replay2

### 7.1 RecitalExtractor — comandos que FALTAM detectar

| Comando | Importância | Status |
|---------|-------------|--------|
| `SET RELATION TO <key> INTO <alias>` | **CRÍTICO** — FKs explícitas | ❌ Não detectado |
| `APPEND BLANK` | CREATE | ❌ |
| `GATHER FROM` | UPDATE | ❌ |
| `REPLACE <campo> WITH` | UPDATE | ❌ |
| `SCATTER TO` | READ | ❌ |
| `DELETE` | DELETE | ❌ |
| `PACK` / `ZAP` | DELETE | ❌ |
| `SEEK <key>` | READ | ❌ |
| `LOCATE` | READ | ❌ |
| `CREATE TABLE / DBF` | DDL | ❌ |
| `INDEX ON <key> TAG <tag>` | Índice (pistas de FK) | ❌ |

### 7.2 RelationshipMapper — melhorias possíveis

- `SET RELATION TO <key> INTO <alias>` é a FK explícita mais confiável
- Campos com sufixo `_id`, prefixo `id_`, `cod_`, `cd_` → **já detectado**
- `INDEX ON campo1+campo2 TAG nome` → índices compostos sugerem FKs
- Constraints SQL `FOREIGN KEY` → FK explícita em DDL

### 7.3 Extensões de arquivo

A documentação menciona `.prg` para programas, `.dbo`/`.dbf` para tabelas, `.dbx`/`.cdx`/`.ndx` para índices.

**Parser atual**: `.prg`, `.src`, `.sql`, `.rsp`, `.php`, `.scx`, `.vcx`, `.pjx`, `.pjt`
**Dakota usa**: `.dbo` (tabelas/programas)

→ Adicionar `.dbf`, `.cdx`, `.ndx` ao parser

### 7.4 Estrutura de programas Recital

Programas Recital (.prg):
```
* comentario
&& comentario inline
USE tabela ALIAS alias
SELECT alias
SEEK "chave"
IF FOUND()
  SCATTER TO array
  @ 10,10 GET campo
  READ
  GATHER FROM array
ENDIF
CLOSE alias
```

### 7.5 Convenções de nomenclatura Dakota

- Módulos de 3 letras: `cad`, `cre`, `est`, `fat`, `ped`, `fin`, `cop`, `com`, `ven`, `fis`, `pro`, `sup`
- Tabelas: `{modulo}{numero}.dbo` (ex: `cad110.dbo`, `fat210.dbo`)
- Programas: `{modulo}{numero}.prg` (ex: `cad110.prg`)
- Índices: `{modulo}{numero}.cdx` ou `.ndx`

---

## 8. Padrões Detectados no Código (NÃO documentados no Wiki Oficial)

Itens encontrados nos extratores do Replay2 que NÃO constam na documentação oficial do Recital.

### 8.1 Padrão Dakota: XxxAbreNNN / XxxFecNNN

**RecitalExtractor** (score 10 — o mais confiável):
```
CreAbre100(1)    → entidade "cre100", operação "open"
FatAbre210(0)    → entidade "fat210", operação "open"
```
Este é um padrão **específico da Dakota**, não documentado no wiki Recital.
Provavelmente é uma convenção interna: `<Modulo>Abre<Numero>(<flag>)` para abrir tabela.

### 8.2 Funções DBF Driver (DBFExtractor)

| Função | Equivalente | Documentada? |
|--------|-------------|--------------|
| `dbUseArea(TRUE, "TABELA")` | `USE tabela` | ❌ Não |
| `dbCreate("TABELA")` | `CREATE TABLE` | ❌ Não |
| `dbAppend()` | `APPEND BLANK` | ❌ Não |
| `dbCommit()` | COMMIT | ❌ Não |
| `dbGoTop()` / `dbGoBottom()` | `GO TOP` / `GO BOTTOM` | ❌ Não |

Estas são funções de **driver DBF nativo** (Harbour/xHarbour/Clipper), não Recital puro.

### 8.3 Funções ISAM/xBase (ISAMExtractor)

| Função/Comando | Documentada? |
|----------------|-------------|
| `DBSEEK(chave)` | ❌ Não |
| `FIELDGET(n)` | ❌ Não |
| `FIELDPUT(n, valor)` | ❌ Não |
| `SET ORDER TO tag` | ✅ Sim (como SET ORDER) |

`FIELDGET`/`FIELDPUT` são funções xBase (Clipper/dBASE) para acesso posicional a campos.

### 8.4 Funções de Validação Recital (ValidationExtractor)

| Função | Uso |
|--------|-----|
| `EMPTY(campo)` | Verifica se campo está vazio |
| `ISBLANK(campo)` | Similar a EMPTY |
| `LEN(ALLTRIM(campo)) = 0` | Verifica campo vazio |
| `BETWEEN(campo, min, max)` | Range check |
| `INLIST(campo, val1, val2, ...)` | Pertence a lista |
| `VALID` / `PICTURE` / `PICT` | Máscara de validação |

### 8.5 Dicas de Tipo por Nome de Campo (ValidationExtractor)

O extrator infere tipos semânticos por convenção de nomenclatura brasileira:
`CPF`, `CNPJ`, `RG`, `IE`, `NOME`, `RAZAO`, `FANTASIA`, `EMAIL`, `E_MAIL`,
`TELEFONE`, `FONE`, `CELULAR`, `CEP`, `ENDERECO`, `LOGRADOURO`, `CIDADE`,
`BAIRRO`, `UF`, `ESTADO`, `DATA`, `DT_*`, `VALOR`, `PRECO`, `TOTAL`, `SALDO`,
`CODIGO`, `COD_*`, `DESCRICAO`, `OBS`, `OBSERVACAO`

### 8.6 Comandos de Tela (ScreenExtractor)

| Comando | Documentado? |
|---------|-------------|
| `@ row,col SAY "texto"` | ✅ Sim |
| `@ row,col GET campo` | ✅ Sim |
| `@ row,col PROMPT "opcao"` | ✅ Sim |
| `DEFINE WINDOW` | ✅ Sim |
| `DEFINE SCREEN` | ❌ Não |
| `DEFINE FORM` | ❌ Não |
| `TITLE` / `TITULO` / `CAPTION` | ✅ Sim (como TITLE) |

### 8.7 Extensões de Arquivo no Código

| Arquivo | Extensões | Fonte |
|---------|-----------|-------|
| `parser.py` | `.prg`, `.src`, `.sql`, `.rsp`, `.php`, `.scx`, `.vcx`, `.pjx`, `.pjt` | Coleta de fontes |
| `csv_exporter.py` | `.prg`, `.src`, `.sql`, `.dbo` | Exportação |
| `journey_inferencer.py` | `.prg`, `.src` | Inferência |
| `ddl_parser.py` | `.sql` | DDL parser |
| `menu_analyzer.py` | `.prg`, `menu*.prg` | Menu |

**Divergência**: O `parser.py` (ponto de entrada principal) NÃO inclui `.dbo`, `.dbf`, `.cdx`, `.ndx`.

### 8.8 Mapa de Módulos Dakota (RecitalExtractor)

30+ prefixos de módulo mapeados de abreviação técnica → nome semântico:
`cad→cadastros`, `cre→contas_receber`, `est→estoque`, `fat→faturamento`,
`ped→pedidos`, `pcp→producao`, `cmp→compras`, `fin→financeiro`,
`exp→expedicao`, `mat→materiais`, `sig→sistema`, `blo→bloqueio`,
`uni→unificado`, `sol→solados`, `sgm→gestao_modelos`, `ses→sesmt`,
`ctb→contabilidade`, `imo→imoveis`, `sac→sac`, `mkt→marketing`,
`loj→lojas`, `cpr→compras`, `ndm→nota_devolucao`, `mao→mao_de_obra`

### 8.9 SQL Exec (SQLExtractor)

`SQLEXEC(handle, "SELECT ... FROM tabela")` — função de execução SQL remota,
não documentada no wiki Recital. Provavelmente de driver ODBC/JDBC.
