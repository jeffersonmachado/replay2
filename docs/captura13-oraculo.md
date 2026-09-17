# Oráculo funcional — Captura 13 (jornada 3.6.1 Pedido E-Commerce)

Data da análise: 2026-09-17. Responsável: engenharia (análise assistida).
Atualização 2026-09-17 (2ª rodada): **R1 e R3 corrigidos** com TDD
(ver §5 e §7); R2 segue lacuna documentada de plataforma.
Escopo: runs sintéticas da jornada de inclusão de pedido de venda — AIX
(10.5.8.25, runs 94/95/96, captura 13, strict-global, policy adaptive) e Linux
(10.5.8.24, runs 20/21/22/23, captura 51, mesma jornada — trilha sintética
renumerada com os mesmos `seq_global` 9..463).

## 1. Metodologia

1. Falhas extraídas do banco dos servidores (`/tmp/aix-runs-94-96.json`,
   `/tmp/linux-runs-20-23.json`): 288 no AIX (215 swap/low + 60 divergence/low
   + 13 divergence/medium) e 412 no Linux (103 por run: 65 swap/low +
   20 divergence/low + 18 divergence/medium).
2. Cada falha traz na evidência as telas completas (`expected_screen` da
   trilha × `observed_screen` do destino) — a análise comparou as telas linha
   a linha, sem inferência por contagem.
3. De→para: `trail/de-para.json` da captura 13 (AIX) e `synthetic_applied`
   dos params de cada run (Linux). Aplicados:
   - AIX: `modelo g2511→303`, `comb 0000135→00001`, `valor 229,9→399,7`;
     mantidos: cpf, situacao, frete, codigo, parcelas, qtd.
   - Linux: `qtd 1→7`, `valor 229,9→1609,3` (= 7 × 229,90, consistente);
     mantidos: cpf, frete, situacao, modelo, comb, codigo, parcelas.
4. Estado final reconstruído das trilhas observadas assinadas
   (`observed_runs/run-94|95|96`, verify_ok=1) alimentando a TerminalEngine
   canônica local; causa-raiz do item rejeitado confirmada na trilha
   sintética vs audit original (`dev/cap13-check/audit-20260727-102406.part001.jsonl`).
5. Cobertura de checkpoints: a trilha sintética tem exatamente 103
   `deterministic_input` (seq_global 9..463; preâmbulo de login cortado pelo
   `detect_session_entry`). Nota: `metrics.checkpoints_ok=103` NÃO significa
   convergência — o runner incrementa em `on_progress` para todo input
   processado (`runner.py:476`, `executors.py:497`); convergência real =
   103 − falhas registradas.

Classificações usadas: **(a)** substituição sintética explicada e validada;
**(b)** divergência funcional real; **(c)** falha de
sincronização/observação/ambiente; **(d)** evidência insuficiente.

## 2. Cobertura de checkpoints e estado final

| Run | Checkpoints | Convergiram limpos | Falhas | Último checkpoint (463) |
|---|---|---|---|---|
| AIX 94 | 103 | 5 (seqs 9, 23, 41, 48, 51) | 98 | divergiu (low, contexto shell) |
| AIX 95 | 103 | 9 (+379, 381, 387, 392) | 94 | divergiu (low) |
| AIX 96 | 103 | 7 (+381, 392) | 96 | divergiu (low) |
| Linux 20-23 | 103 | **0** | 103 cada | divergiu (low) |

Todas as 7 runs terminaram `status=success` (modo send-anyway), com a sessão
encerrada no shell (`exit` → `Connection to 127.0.0.1 closed.`). No Linux o
"0 convergiram limpos" é dominado por ruído ambiental: a barra de status do
Recital ("IBM Aix (Common)" na captura × "Linux x86" no destino) difere em
TODAS as telas e a máscara volátil cobria só o "Kb livres" — ver §5, R3
(corrigido na 2ª rodada).

## 3. Tabela de grupos de falha — AIX (runs 94/95/96)

| seq_global | tipo/severidade registrada | runs | classificação oráculo | justificativa (evidência) |
|---|---|---|---|---|
| 16 | screen_divergence/medium | 94,95,96 | **(c) sincronização** | Esperada "0 MENU PRINCIPAL", observada "3 MOVIMENTOS" — sessão um nível de menu à frente; ressincroniza já no seq 23 (converge limpo). |
| 57–155, 164–206 (45 seqs) | synthetic_data_swap/low | 94,95,96 | **(a) troca validada** | Únicas diferenças: nº do pedido gerado (D00011073→D00011140), data de emissão (27/07/26→12/09/26), Kb livres volátil e os valores do de→para na grade (G2511→303). Amostras inspecionadas: 57, 65, 164, 179, 185, 203. |
| 209 | synthetic_data_swap/low | 94,95,96 | **(b) divergência real — início** | Esperada mostra o 6º dígito do comb original caindo no Tam ("00001│3"); observada tem Tam vazio. É a causa-raiz visível: o comb original "0000135" preenchia comb(5)+Tam("35") por auto-avanço; o sintético "00001" (5 chars + 2 eventos vazios) nunca preenche o Tam (trilha sintética seqs 185–206: '0','0','0','0','1','','' × captura original seqs 265–286: '0','0','0','0','1','3','5'). |
| 215, 218, 224, 241, 245, 265 | synthetic_data_swap/low | 94,95,96 | **(b) divergência real MASCARADA** | Observada traz "Codigo nao cadastrado." (ausente na esperada) — o ERP rejeitou o produto (lookup modelo+comb+tam com Tam vazio). Item fica sem Tam, Qtd 0, Valor 0,00. |
| 232, 249 | synthetic_data_swap/low | 94,95,96 | **(b) divergência real MASCARADA** | Sem a mensagem de erro no snapshot, mas a grade observada segue desvalorizada (Qtd 0, Valor 0,00); em 249 o popup "Loja origem" da esperada não existe na observada. |
| 274–327 (10 seqs) | synthetic_data_swap/low | 94,95,96 | **(b) divergência real MASCARADA** | Esperada = grade de PAGAMENTO (Cd/Descricao/Valor/Parc); observada = grade de ITENS travada em "<^U>DELETA <+>CONFIRMA <ESC>ABANDONA". As teclas do pagamento (codigo 15, valor 399,7) caíram na grade de itens ("15 " e ",7 " aparecem na coluna Tam da linha 02 — seqs 288, 301, 311). A sessão nunca alcançou o pagamento. |
| 349–372 (6 seqs) | synthetic_data_swap/low | 94,95,96 | **(b) divergência real MASCARADA** | Esperada = pedido finalizado (D00011074, Total 229,90); observada = pedido D00011140 com Total 0,00, item sem valor e erro "Codigo nao cadastrado" — o swap classificou porque a linha da grade ecoa 303/00001 nos dois lados (política "1 linha de eco"). |
| 374 | medium (94) / swap (95,96) | todas | **(b) cascata** | Esperada = tela do pedido; observada (94) = menu "3 MOVIMENTOS" (sessão abandonou a grade travada); 95/96 ainda na tela do pedido com dados sintéticos. |
| 379, 381, 387, 389, 392 | screen_divergence/medium | subconjuntos | **(b) cascata + (c) navegação final** | Esperada = menu "3.6 E-COMMERCE"; observada = menu principal, menu 3, diálogo "Deseja realmente sair do sistema?" ou tela do pedido (96/379) — posições diferentes na saída do sistema, consequência da dessincronia anterior. Run 96 seq 379 comprova o pedido D00011140 persistido com item Qtd 0 / Valor 0,00. |
| 402–463 (20 seqs) | screen_divergence/low | 94,95,96 | **(c) saída para o shell** | Esperada = cauda da captura (menu do usuário, "0 - Fim", shell, typo "exti"); observada = shell da run (date, exit, conexão fechada). Overrides `context_switch`/`content_present` corretos: as duas sessões terminam no shell. |

### Estado final no AIX (trilha observada, verify_ok=1)

- Pedido exibido ao fim: **D00011140**, Consumidor 001.098.290-69 FERNANDO
  GABRIEL BLOEDORN, Situacao "04 EM SEPARACAO", **Total: 0,00**, grade
  `│01│303│00001│ │ 0│ 0,00│` — item não valorizado.
- "Codigo nao cadastrado" aparece **9×** na saída de cada uma das 3 runs.
- O mesmo número D00011140 aparece nas 3 runs (pedido preexistente no
  servidor; a inclusão nova da captura — D00011074 — não se concretizou com
  item válido).

## 4. Tabela de grupos de falha — Linux (runs 20/21/22/23, 4× cada)

| seq_global | tipo/severidade registrada | classificação oráculo | justificativa (evidência) |
|---|---|---|---|
| 9, 23, 51 | screen_divergence/medium | **(c) ambiente** | ÚNICA linha divergente é a barra de status: "IBM Aix (Common) │ 792,000 Kb livres" × "Linux x86 │ 024,930 Kb livres". Conteúdo funcional idêntico (19/20, 20/21 linhas comuns). |
| 41, 48 | screen_divergence/medium | **(c) sincronização (render)** | Observada = só cabeçalho + barra de status, corpo em branco (form ainda não redesenhado no timeout); o checkpoint seguinte (51) traz o form completo. |
| 57–327 (65 seqs) | synthetic_data_swap/low | **(a) troca validada, com ressalva ambiental** | De→para íntegro: qtd 1→7 na grade e valor 229,9→1609,3 = 7×229,90 (item aceito: `│01│G2511│00001│ 35│ 7│ 229,90│`). Ressalva: dados cadastrais do ambiente divergem da captura — mesmo CPF 00109829069 retorna nome/endereço diferentes ("FERNANDO GABRIEL BLOEDORN / ARCO IRIS / 383" × "FERNANDO BLOEDORN / MANOEL DE ABREU / 858") e o pedido consultado está em situação "04 FATURADO" × "04 EM SEPARACAO" — bases AIX×Linux dessincronizadas, não falha do replay. |
| 349–392 (12 seqs) | screen_divergence/medium | **(b) divergência funcional REAL** | O ERP **caiu** na finalização: `Cannot lock record - errno 9.` / `*** Recital error(118)` com pilha `gapblank:349 ← pest360atualizacabecalho:2036 ← est361.dbo:877 ← est360.dbo:57 ← est300.dbo:59 ← est.dbo:264 ← db_compat:3` e a sessão caiu no shell `[ferblo@recital24 est]$`. Reproduzível em 4/4 runs, com item válido (G2511/00001/35, qtd 7). |
| 402–463 (20 seqs) | screen_divergence/low | **(b) cascata do crash** | Observada = tela do pedido congelada + traço do erro + shell; o rótulo "mudança de contexto app↔shell — divergência de gravação" não se aplica (a sessão observada morreu no shell pós-crash). Severidade low aqui é artefato do override; o sinal real está nos mediums 349–392. |

## 5. Achados e recomendações

- **R1 (síntese, causa-raiz AIX — CORRIGIDO na 2ª rodada)**: o
  `capture_parametrizer`/de→para tratou "0000135" como um campo só (comb),
  mas na grade o valor atravessa o auto-avanço comb(5)→Tam("35") — o salto
  de cursor de 2 colunas coube na tolerância de máscara do parametrizer
  (fusão mantida: endurecê-la quebraria máscaras legítimas). Na trilha
  sintética, `synthetic_trail._apply_substitutions` distribuía o valor novo
  mais curto ("00001", 5 chars) pelos 7 eventos do run e **esvaziava os 2
  eventos excedentes** — apagando o Tam e fazendo o lookup do produto falhar
  ("Codigo nao cadastrado"). Correção em
  `gateway/dakota_gateway/synthetic/synthetic_trail.py`: quando o valor novo
  é mais curto **e prefixo** do run original, os eventos excedentes
  preservam a tecla original do operador (o sufixo pertence ao campo
  seguinte); valor mais curto que não é prefixo (campo único de largura
  variável, ex.: 'g2511'→'ab') mantém o comportamento histórico. Limitação
  conhecida (documentada no docstring): valor sintético de campo com
  auto-avanço que NÃO seja prefixo do original ainda esvazia o sufixo — a
  correção plena exige a largura da PICTURE no mapeamento, indisponível na
  trilha. Antes/depois real (trilha regenerada localmente da captura 13 com
  o mesmo de→para das runs 94-96): seqs 185/188/191/194/197/203/206
  `'0','0','0','0','1','',''` → `'0','0','0','0','1','3','5'` — as 7 teclas
  do comb/Tam preservadas, ENTER no seq 209, cadeia íntegra no verify.
- **R2 (plataforma, Linux — lacuna documentada, fora do escopo desta
  correção)**: Recital error(118) "Cannot lock record - errno 9"
  na finalização do pedido (est361.dbo:877 → pest360atualizacabecalho →
  gapblank) — divergência real do destino Linux, 4/4 runs, com dados válidos.
  Investigar lock de registro no Recital 24 Linux.
- **R3 (comparação — CORRIGIDO na 2ª rodada)**: a máscara volátil não cobria
  o rótulo de plataforma da barra de status ("IBM Aix (Common)" ×
  "Linux x86") — em replay cross-platform nenhum checkpoint convergia limpo
  (Linux: 0/103). Correção em `gateway/dakota_terminal/volatile.py`: novo
  padrão case-insensitive `(IBM Aix \(Common\)|Linux x86)\s*(?=\|)` — o
  separador `|` ancora a máscara à linha de status (não toca o texto em
  outro contexto) e o preenchimento em branco até o separador é absorvido.
  Validado com os bytes reais do checkpoint seq 89 da captura: tela AIX ×
  simulada Linux ficam idênticas mascaradas; divergência real em outro
  trecho continua sem match. Assinaturas gravadas na trilha não mudam (a
  máscara é só a segunda chance da comparação).
- **R4 (classificação — CORRIGIDO nesta entrega)**: a classificação
  `synthetic_data_swap` aceitava UMA linha de eco qualquer, e o eco de
  identificador gerado (nº do pedido — presente em toda reexecução) bastava.
  Era o vetor que mascarou os seqs 288–327 no AIX. Correção em
  `replay_compare.py`/`deterministic.py`: (i) máscara dos pares de→para
  case-insensitive (o Recital exibe em maiúsculas o valor digitado em
  minúsculas — sem isso a linha da grade nunca era reconhecida como eco);
  (ii) novo portão `substitution_pair_echo_present`: a classificação exige
  eco de um PAR do de→para (placeholder bilateral, linha igualada pela
  máscara ou par curto); eco só de id gerado não classifica mais.
  Reclassificação medida sobre as falhas reais com o código novo: AIX
  144/215 swaps virariam `screen_divergence` (inclui os seqs 274–327 da
  grade travada e as telas de formulário sem troca real — 57–155, cuja
  divergência é só dado gerado/ambiente); Linux 256/260 (a única troca real
  de tela é o valor 1609,3 no seq 304).
- **R5 (residual conhecido)**: mesmo com o portão, os seqs 215–265 e 349–372
  do AIX continuariam swap — a linha da grade ecoa 303/00001 nos dois lados
  e a política documentada aceita 1 linha de eco por tela. Um portão mais
  estrito (resto da linha explicado) foi prototipado e rejeitado: inverte
  telas legítimas (comb exibido truncado "00001" nos dois lados é mascarado
  só na observada). Fica como dívida: relatório por run de "swap com
  divergência residual" ou regra de exibição truncada na máscara.
- **R6 (ambiente)**: dados cadastrais divergentes entre servidores para o
  mesmo CPF (endereço/nome/situação) — esperado em bases distintas, mas
  registrado como ressalva do oráculo.

## 6. Veredito

**Jornada NÃO APROVADA nos dois ambientes** (não por contagem de falhas —
pelas evidências acima):

- **AIX (runs 94/95/96)**: a inclusão NÃO se cumpriu funcionalmente — item
  rejeitado pelo ERP ("Codigo nao cadastrado" ×9/run), grade de itens
  travada, teclas de pagamento desviadas, pedido persistido com Total 0,00 e
  item sem valor. A falha é do DADO SINTÉTICO (R1), não do ERP-alvo — o ERP
  se comportou corretamente ao rejeitar. 24 seqs de divergência real estavam
  mascarados como `synthetic_data_swap/low` (R4/R5).
- **Linux (runs 20/21/22/23)**: a jornada avançou corretamente com dados
  sintéticos válidos até a finalização, onde o ERP **abortou com erro fatal**
  (Recital error 118, Cannot lock record) e a sessão caiu no shell (R2) —
  divergência funcional real do destino, corretamente classificada medium
  nos seqs 349–392.

Critério de aprovação futuro: repetir a jornada no AIX (R1 já corrigido —
comb/Tam preservados na trilha sintética) até o pagamento confirmado
("Confirma a inclusao do registro? → Sim" com pedido novo valorizado), e no
Linux somente após resolver R2. Com R3 corrigido, os checkpoints da run
Linux deixam de divergir pelo rótulo de plataforma (ruído ambiental) e a
contagem de convergência passa a refletir só diferenças funcionais.

## 7. Testes de regressão

`tests/test_synthetic_swap_oracle_unit.py` (novo, 9 casos — positivos com
telas reais da run 94 e negativos: campo ausente na observada, placeholder
unilateral, valor sem par no de→para, tela disjunta, id gerado com shape
diferente, erro do ERP sem eco, run sem de→para). Os casos
"campo ausente" e "troca longa na grade" estavam RED no código anterior e
motivaram a correção R4. Suítes relacionadas:
`tests/test_synthetic_data_swap_unit.py`,
`tests/test_checkpoint_failure_classification_unit.py`,
`tests/test_input_echo_stale_unit.py` — verde (57 testes).

2ª rodada (R1/R3):

- R1: `tests/test_synthetic_trail_unit.py` —
  `test_substituicao_teclas_auto_avanco_preserva_sufixo` (RED no código
  anterior: run '0000135'→'00001' virava `['0','0','0','0','1','','']`;
  exige `['0','0','0','0','1','3','5']` + verify da cadeia) e
  `test_substituicao_teclas_mais_curta_nao_prefixo_esvazia` (controle do
  ramo variável: '229,9'→'45,3' mantém excedente vazio). O teste
  pré-existente `test_substituicao_teclas_valor_mais_curto` ('g2511'→'ab')
  NÃO foi alterado e segue verde — o comportamento de campo único de
  largura variável é preservado.
- R3: `tests/test_volatile_mask_unit.py` — 5 casos novos (3 RED no código
  anterior: rótulo de plataforma, igualdade AIX×Linux mascarada e fallback
  de comparação; 2 negativos já verdes: texto fora da linha de status não é
  mascarado e divergência real além da plataforma não casa).
- Regressão dirigida final:
  `python3 -m pytest tests/ -q -k "parametriz or synthetic_trail or swap or
  volatile or screen or synthetic"` → **497 passed**.
