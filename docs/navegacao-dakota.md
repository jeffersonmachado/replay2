# Estrutura de Navegação do Sistema Dakota

> Descoberto por engenharia reversa dos `.dbo` no servidor MIG24 (10.5.8.25)
> Data: 2026-06-26

---

## 1. Hierarquia de Navegação

```
Login (usuário + senha)
  │
  ├── Seleção de Marca: 1-Dakota, 2-Kolosh, 3-Dakotinha, 7-Tanara, 8-Curio, 9-Skien
  │
  ├── Seleção de Região: (1)Sul, (2)Nordeste, (3)Sergipe, (4)Quixada
  │
  └── MENUSIG (Menu Principal)
        │
        ├── PCP (Produção)
        │   ├── Turno: Manhã / Tarde / Noite / Dia / Todos
        │   ├── Produção de Calçados
        │   ├── Produção de Solados (PU / TR / EVA / Saltos / PVC / Todos)
        │   ├── Produção de Bolsas
        │   ├── Dakota / Atelier / Todos
        │   └── MENUPCP2 (submenu)
        │
        ├── [Cadastros] (FMENUCAD genérico)
        │   ├── Incluir (I)
        │   ├── Modificar (M)
        │   ├── Excluir (E)
        │   ├── Consultar (C)
        │   ├── Primeiro (P)
        │   ├── Ultimo (U)
        │   ├── Avançar (A)
        │   ├── Voltar (V)
        │   ├── Listar (L) / Finalizar (F)
        │   └── Replicar (R) — em alguns módulos
        │
        └── [Outros módulos...]
```

---

## 2. Mecanismo de Menu

### 2.1 GMENUCAD — Menu CRUD Genérico

Definido em `biblio.dbo`. Gera menus de cadastro padronizados.

```
Função: FMENUCAD(PLIN, PCOL, PNUMERICO)
  → Renderiza opções via @ pLin+N, pCol PROMPT fTraduz(p_idioma, texto, ...)
  → Menu to LOPCA
  → Seleção: substr("IMECPUAVLF", lPrompt, 1) ou "IMECPUAVLR"
  
Opções padrão:
  I - Incluir
  M - Modificar  
  E - Excluir
  C - Consultar
  P - Primeiro
  U - Ultimo
  A - Avançar
  V - Voltar
  L - Listar (ou F - Finalizar)
  R - Replicar (alguns módulos)
```

### 2.2 GMENU — Menu Customizado

Definido em `exodus.dbo`. Exibe menu com opções configuráveis.

```
Função: GMENU(pcOpsMenu)
  → pcOpsMenu: string com opções em blocos de 9 caracteres
  → Cada bloco = 1 opção do menu
  → Renderiza @ row,col PROMPT para cada opção
```

### 2.3 MENUSIG — Menu Principal

Definido em `sigfunc.dbo`. Entry point após login.

```
save screen to telamenu
MENUSIG:
  gmenu(wopcao)           ← exibe menu principal
  vescolha[wopcao]        ← dispatch para submenu
    → MENUPCP             ← Produção
    → MENUESTOQUE         ← Estoque (provável)
    → MENUFAT             ← Faturamento (provável)
    → ...
restore screen from telamenu
```

### 2.4 Seleção por gmenu()

Padrão de uso em todos os módulos:
```recital
wopcao = 0
gmenu(wopcao)              && exibe opções e retorna seleção
do case
   case wopcao = 1         && opção 1
        do programa1
   case wopcao = 2         && opção 2
        do programa2
   ...
endcase
```

---

## 3. Filiais/Empresas (maksys.dbo)

Sistema multi-empresa com 22+ filiais:

| Código | Nome |
|--------|------|
| MATRIZ | Matriz |
| NORDESTE | Filial Nordeste |
| SERGIPE | Filial Sergipe |
| QUIXADA | Filial Quixada |
| RUSSAS | Filial Russas |
| IGUATU | Filial Iguatu |
| MARAN | Filial Maranguape? |
| SARANDI | Filial Sarandi |
| NOVAPE | Nova Petrópolis? |
| CAJAZ | Cajazeiras? |
| SIMAO | Simão? |
| POCOVE | Poço Verde? |
| KOLOSH | Marca Kolosh |
| MDM | Marca MDM |
| ROJALE | Marca Rojale |
| MEDEMA | Marca Medema |
| CRIACOES | Marca Criações |
| MJLM | ? |
| DLB | ? |
| MRP | ? |
| DEPA | ? |
| ASSOCIAC | Associação |
| CALCADOS | Dakota Calçados |

---

## 4. Funções de Navegação Padrão

### 4.1 Abertura de Módulo

Cada módulo tem um padrão `{MOD}ABRE`:

```recital
Function CADABRE205(JORDEM)
  gabre("/dakota2/cad/arq205.cad", conteudo, "cad205")
  NOMEIND1 = "/dakota2/cad/icad205.001"
  index on campo1 + campo2 to &nomeind1
  set index to &nomeind1
  set order to &jordem
Return
```

### 4.2 CRUD via FMENUCAD

```recital
Function CADMENU()
  FMENUCAD(pLin, pCol, pNumerico)
  do case
     case lOpca = "I"  && Incluir
          append blank
          read
     case lOpca = "M"  && Modificar
          read
     case lOpca = "E"  && Excluir
          delete
     case lOpca = "C"  && Consultar
          read noedit
     ...
  endcase
Return
```

### 4.3 Navegação entre Registros

- `skip` / `skip -1` — próximo/anterior
- `go top` / `go bottom` — primeiro/último
- `seek chave` — busca por índice
- `M_ACTION` com `dbmode` (0-4) — ações de banco (defined in biblio.dbo)

---

## 5. Implicações para o Replay2

### 5.1 Menu Analyzer precisa ser estendido

| Limitação atual | Correção necessária |
|-----------------|---------------------|
| Só lê `.prg` | Ler `.dbo` também |
| Só busca `menu*.prg` | Buscar funções `MENU*`, `GMENU`, `FMENUCAD` |
| Regex `@ row,col SAY "N. Label"` | Também detectar `@ row,col PROMPT fTraduz(...)` |
| Regex `DO programa` | Também detectar `gmenu()`, `case wopcao = N` |

### 5.2 Padrões a detectar para simulação de usuário

Para simular um usuário real, o Replay2 precisa:

1. **Login**: `seek(usuario, cadusu)` → valida credenciais
2. **Marca/Região**: menus de seleção (0-9)
3. **MENUSIG** → dispatch para submódulos
4. **Seleção de opção**: `gmenu(wopcao)` → `do case wopcao = N`
5. **CRUD**: `FMENUCAD` → `substr("IMECPUAVLF", opcao, 1)`
6. **Navegação entre registros**: `skip`, `go top/bottom`, `seek`
7. **Índices**: `set order to` define ordem de navegação

### 5.3 Entidades e Tabelas por Módulo

Cada módulo acessa tabelas em `/dakota2/{mod}/arq{n}.{mod}`:

| Módulo | Path | Exemplo |
|--------|------|---------|
| cad | `/dakota2/cad/arq*.cad` | arq205.cad |
| ped | `/dakota2/ped/arq*.ped` | arq3a4.ped |
| fat | `/dakota2/fat/arq*.fat` | arq310.fat |
| cmp | `/dakota2/cmp/arq*.cmp` | arq250.cmp |
| est | `/dakota2/est/arq*.est` | (provável) |
| pcp | `/dakota2/pcp/arq*.pcp` | (provável) |
| caduni | `/dakota1/caduni/arq*.uni` | arq205.uni |

### 5.4 Fluxo Típico de Sessão

```
1. Login → usuário + senha → cadusu.dbo
2. Seleciona marca (Dakota/Kolosh/etc.)
3. Seleciona região/filial
4. MENUSIG → escolhe módulo (ex: PCP)
5. Submenu → escolhe função (ex: Produção de Calçados)
6. FMENUCAD → navega registros (Primeiro/Ultimo/Avançar/Voltar)
7. FMENUCAD → Consultar/Modificar/Incluir
8. Sai do módulo → volta ao MENUSIG
9. Sai do sistema
```
