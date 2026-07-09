# Servidor Dakota MIG24 — Documentação Técnica

> Servidor: `10.5.8.25` (MIG_REC24)
> Data: 2026-06-26
> Acesso: `results@10.5.8.25` (SSH, uid=2933, grupo=cpd)

---

## 1. Ambiente

| Item | Detalhe |
|------|---------|
| **OS** | AIX 7 — `MIG_REC24 3 7 00F9EC464C00` |
| **Shell** | ksh (Korn Shell) |
| **Recital** | 8.0 UnixDeveloper — `/usr/recital80/` |
| **Python** | 3.9.17 — `/usr/opt/python3/bin/python3` |
| **Replay2** | 0.1.0 — `/opt/dakota/replay2/` |
| **Grupo** | `cpd` (gid=199), `staff` (gid=1) |

---

## 2. Estrutura de Diretórios

```
/dakota1/
├── caduni/           ← Arquivos .uni (unificados? compilados?)
│   ├── arq300.uni
│   ├── arq500.uni
│   └── ... (10+ arquivos)
├── lib/              ← 97 arquivos .dbo (runtime/compilado) + 563 backups
├── lib.cpio          ← Backup cpio das bibliotecas
├── prg/
│   └── lib/          ← 5 arquivos .prg (código fonte)
│       ├── biblio.prg      (239 KB - biblioteca principal)
│       ├── exodus.prg      (359 KB - exodus/etiquetas)
│       ├── exodus_contato_forn.prg
│       ├── exodus_div.prg
│       └── exodus_val_ie.prg
├── u/                ← Diretórios de usuários
│   ├── ferblo/
│   ├── results/
│   └── root/
└── xml/              ← Arquivos temporários XML
```

### 2.1 Arquivos .dbo (97 no total)

Arquivos grandes (100-444 KB), parcialmente compilados/encodados.
Contêm código misto (binário + strings legíveis).

**Padrões de nomenclatura:**

| Prefixo | Significado | Exemplos |
|---------|-------------|----------|
| `{mod}abre` | Abre/funções de abertura do módulo | `cadabre.dbo`, `fatabre.dbo`, `pedabre.dbo` |
| `{mod}fun` | Funções do módulo | `crefun.dbo`, `estfun.dbo`, `blofun.dbo` |
| `{mod}fun2..4` | Funções complementares | `fatfun2.dbo`, `pedfun2.dbo`, `pedfun4.dbo` |
| `bib{mod}` | Biblioteca do módulo | `bibcpa.dbo`, `bibctb.dbo`, `bibfor.dbo`, `bibpcp.dbo` |
| `{mod}{num}` | Programa específico | `fat310.dbo`, `pcp800.dbo`, `pedonline.dbo` |
| `abre{mod}` | Abertura (ordem inversa) | `abrecpa.dbo`, `abremat.dbo`, `abresig.dbo` |
| Nomes especiais | Funções específicas | `portalweb.dbo`, `ondecomprar.dbo`, `maksys.dbo` |

### 2.2 Arquivos .prg (5 no total)

Código fonte legível. Apenas bibliotecas de sistema (não o código de negócio).
O código de negócio está nos `.dbo`.

### 2.3 Backups (563 arquivos)

Formato: `{nome}.{data}` — ex: `biblio.20200902`, `bibctb.20220720`
Múltiplas versões históricas de cada arquivo.

---

## 3. Módulos do Sistema (70+ prefixos)

Extraídos dos nomes de arquivo `.dbo`:

| Prefixo | Módulo | Arquivos |
|---------|--------|----------|
| `cad` | Cadastros | cadabre.dbo |
| `cre` | Contas a Receber | creabre.dbo, crefun.dbo |
| `cpa` | Contas a Pagar | cpaabre.dbo, cpafunc.dbo, abrecpa.dbo, bibcpa.dbo |
| `ctb` | Contabilidade | bibctb.dbo, ctb.dbo |
| `fat` | Faturamento | fatabre.dbo, fatfun.dbo, fatfun2.dbo, fat310.dbo |
| `ped` | Pedidos | pedabre.dbo, pedfun.dbo, pedfun2/3/4.dbo, pedonline.dbo, pedabre2.dbo |
| `est` | Estoque | estabre.dbo, estfun.dbo, estfun2.dbo |
| `cmp` | Compras | cmpabre.dbo, cmpabre_letranspoc.dbo, cmpabre_pesqdesc.dbo |
| `blo` | Bloqueio | bloabre.dbo, blofun.dbo |
| `pcp` | Produção (PCP) | pcpabre.dbo, pcpfun.dbo, bibpcp.dbo, pcp800.dbo |
| `exp` | Expedição | expabre.dbo, expfun.dbo |
| `fin` | Financeiro | (via cpa/cre) |
| `cto` | Controle? | ctoabre.dbo, ctofun.dbo |
| `ctr` | Controladoria? | ctrabre.dbo, ctrfun.dbo |
| `crm` | CRM | crmabre.dbo, crmfun.dbo |
| `sol` | Solados | solabre.dbo |
| `sor` | ? | sorabre.dbo, sorfun.dbo |
| `slc` | ? | slcabre.dbo |
| `grp` | Grupo | grpabre.dbo, grpfun.dbo |
| `imp` | Impressão | impabre.dbo, impfun.dbo, impabreold.dbo |
| `mkt` | Marketing | mktabre.dbo, mktfun.dbo |
| `wms` | WMS | wmsfun.dbo |
| `mao` | Mão de obra | maofun.dbo |
| `cem` | ? | cemabre.dbo, cemfunc.dbo |
| `eti` | Etiquetas | (prefixo) |
| `sig` | Sistema | sigfunc.dbo, abresig.dbo |
| `spi` | ? | spiabre.dbo, spibib.dbo |
| `epp` | ? | eppbib.dbo |
| `esc` | ? | escabre.dbo |
| `pep` | ? | pepfun.dbo |
| `mat` | Materiais | abremat.dbo |
| `rec` | Recursos? | (prefixo) |

---

## 4. Padrões de Código Recital (descobertos no servidor)

### 4.1 Estrutura de Programa

```recital
*deploy: /dakota1/lib/         ← diretiva de deploy (comentário especial)

**************
Function gSet                 ← funções prefixadas com g (global?) ou f (função)
**************

Set key 29 to gselimp         ← atalhos de teclado
Set mouse OFF
Set hours to 24
Set exclusive OFF
Set deleted ON
Set date to BRITISH
Set separator to ","
Set point to "."

public nom_arquivo            ← variáveis globais prefixadas com p_
public p_multiusuario, p_usuario, p_grupo
public P_cornormal, P_coracento  ← cores em maiúsculas
```

### 4.2 Convenções de Nomenclatura

| Prefixo | Significado | Exemplo |
|---------|-------------|---------|
| `p_` | Variável pública/global | `p_usuario`, `p_multiusuario` |
| `g` | Função global | `gSet`, `gselimp` |
| `f` | Função de biblioteca | `fSets`, `fTraduz`, `fEscolheLingua` |
| `flib` | Função de lib | `flibAbreArq`, `flibabreCadArq` |
| `gabre` | Abre tabela (custom) | `gabre(path, conteudo, alias)` |
| `P_` | Cor/constante | `P_cornormal`, `P_corjanel1` |
| `mens` | Mensagem | `mens("texto", .t.)` |

### 4.3 Funções Customizadas Detectadas

| Função | Descrição |
|--------|-----------|
| `gabre(path, conteudo, alias)` | Abre tabela com path dinâmico |
| `flibAbreArq(alias, ordem, dir)` | Abre arquivo via biblioteca |
| `flibabreCadArq()` | Abre arquivo de cadastro |
| `fTraduz(idioma, texto, ...)` | Tradução multi-idioma (EN/PT) |
| `fEscolheLingua()` | Seleção de idioma |
| `gexistenx(conteudo, arq)` | Verifica existência |
| `gnomeind(arquivo)` | Gera nome de índice |
| `savescreen()`, `restscreen()` | Salva/restaura tela |
| `mens(texto, flag)` | Exibe mensagem |
| `ABRIU()` | Verifica se tabela abriu |
| `LACHOU` | Label de controle de fluxo |

### 4.4 Padrões de Abertura de Tabela

```recital
* Padrão 1: gabre() — caminho dinâmico
gabre("/dakota2/ped/arq3a4.ped", conteudo, "ped3a4")

* Padrão 2: flib — via biblioteca
flibAbreArq(pcAlias, pnOrdem, pcDir)

* Padrão 3: select alias
select &pcAlias
```

### 4.5 Padrões de Índice

```recital
* Índice simples
index on cgccic to &nomeind1

* Índice composto
index on repres + lancamento + sequencia to &nomeind1
index on cliente + nota + serie to &nomeind3

* Múltiplos índices
set index to &nomeind1, &nomeind2, &nomeind3
set order to &jordem
```

### 4.6 Caminhos de Dados

| Path | Conteúdo |
|------|----------|
| `/dakota1/lib/` | Bibliotecas (.dbo) |
| `/dakota1/prg/lib/` | Fontes (.prg) |
| `/dakota2/ped/` | Dados de pedidos |
| `/dakota2/cmp/` | Dados de compras |
| `/dakota2/...` | Demais dados de módulos |

**Importante**: Código e dados em volumes separados (`/dakota1/` vs `/dakota2/`).

### 4.7 Recursos de UI

```recital
* Definição de cores
P_cornormal = [W /N,N/W, , N/W]
P_coracento = [N /W]

* Menu
@ pLin+0, pCol prompt fTraduz(p_idioma, "Incluir", "P", 9, .f., "")
@ pLin+1, pCol prompt fTraduz(p_idioma, "Modificar", "P", 9, .f., "")
menu to LOPCA

* Validação de usuário
seek(p_usuario, cadusu)
if empty(cadusu->idioma)
   mens("ATENCAO! Usuario nao cadastrado...", .t.)
endif
```

### 4.8 Sistema Multi-Idioma

O sistema suporta **Português e Inglês (EUA)**:
```recital
p_idioma = "EUA"  && ou "PTB"
fTraduz(p_idioma, "Incluir", "P", 9, .f., "")
```

---

## 5. Usuários do Sistema

| Usuário | Grupo | Home |
|---------|-------|------|
| `results` | cpd (199) | `/home/results/` |
| `recital` | cpd | `/home/recital/` |
| `usuario` | cpa | `/home/usuario/` |

---

## 6. Replay2 no Servidor

| Item | Detalhe |
|------|---------|
| **Instalação** | `/opt/dakota/replay2/` |
| **Banco** | `/opt/dakota/replay2/gateway/state/replay.db` |
| **Secrets** | `/opt/dakota/replay2/.local-secrets/{cookie-secret,hmac-key}` |
| **Gateway** | `/opt/dakota/replay2/gateway/dakota-gateway` (binário Go) |
| **Status** | NÃO está rodando atualmente |

---

## 7. Implicações para o Replay2

### 7.1 Extrator precisa evoluir

O `RecitalExtractor` atual detecta:
- `XxxAbreNNN(flag)` → score 10
- `USE arquivo ALIAS nome` → score 8
- `alias->campo` → score 1
- `select("alias")` → score 3
- `close alias` → score 2
- `SELECT FROM tabela` → score 4

**Padrões NÃO detectados que existem no código real:**

| Padrão Real | Relevância |
|-------------|------------|
| `gabre(path, conteudo, alias)` | 🔴 Abertura de tabela — função custom Dakota |
| `flibAbreArq(alias, ordem, dir)` | 🔴 Abertura via biblioteca |
| `index on campo1+campo2 to arquivo` | 🟠 FK composta — pistas de relacionamento |
| `set index to &nomeind1, &nomeind2` | 🟡 Múltiplos índices |
| `seek(p_usuario, cadusu)` | 🟠 Navegação por índice em outra tabela |
| `select &pcAlias` | 🟠 Seleção dinâmica de cursor |
| `cadusu->idioma` | 🟡 Alias pointer |

### 7.2 FK Detection via Índices

O padrão `index on repres + lancamento + sequencia` sugere que `repres`, `lancamento` e `sequencia` são campos que formam uma chave composta — potencialmente FKs para outras tabelas.

O padrão `index on cliente + nota + serie` sugere relacionamento entre cliente e nota.

### 7.3 Extensões de Arquivo

- `.dbo` → código compilado/encodado (principal formato Dakota)
- `.prg` → código fonte (apenas bibliotecas de sistema)
- `.uni` → arquivos unificados (em `/dakota1/caduni/`)
- `.wsp` → workspace files

### 7.4 Volume de Código

| Tipo | Quantidade |
|------|------------|
| `.dbo` (runtime) | 97 arquivos, ~20 MB |
| `.prg` (fonte) | 5 arquivos, ~600 KB |
| Backups (.data) | 563 arquivos |
| `.uni` | ~10 arquivos |

### 7.5 Código fonte real vs local

O código local em `/opt/dados/sistema/programas/` (992 `.dbo`) é **diferente** do código no servidor (97 `.dbo`). O local parece ser uma cópia mais completa ou de outro ambiente (fusão/desenvolvimento).

---

## 8. O que NÃO está documentado (descobertas inéditas)

1. **Função `gabre()`** — Não é Recital padrão. É uma abstração Dakota para abrir tabelas com path dinâmico.
2. **Padrão `flib*()`** — Funções de biblioteca customizadas para abertura de arquivos.
3. **Multi-idioma** — Sistema suporta PT/EN via `fTraduz()`.
4. **Volumes separados** — `/dakota1/` (código) e `/dakota2/` (dados) em filesystems diferentes.
5. **563 backups históricos** — Retenção agressiva de versões.
6. **Arquivos `.uni`** — Formato não documentado, possivelmente compilados unificados.
7. **Diretiva `*deploy:`** — Comentário especial que indica path de deploy.
8. **`letranspoc`** — Transportadora, `pesqdesc` — pesquisa descrição (padrões de nomenclatura de sub-funções).
9. **5 .prg vs 97 .dbo** — O código fonte (.prg) é mínimo; a lógica real está nos .dbo compilados.
10. **Sintaxe de cor `[W /N,N/W]`** — Formato de pares de cores Recital.
