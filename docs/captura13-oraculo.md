# Oráculo funcional — Captura 13 (jornada 3.6.1 Pedido E-Commerce)

Data da análise: 2026-09-17. Responsável: engenharia (análise assistida).
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
TODAS as telas e a máscara volátil cobre só o "Kb livres" — ver §5, R3.

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

- **R1 (síntese, causa-raiz AIX)**: o `capture_parametrizer`/de→para tratou
  "0000135" como um campo só (comb), mas na grade o valor atravessa o
  auto-avanço comb(5)→Tam("35"). Trocar por "00001" apagou o Tam e o lookup
  do produto falhou ("Codigo nao cadastrado"). Recomendação: ao substituir
  valores que cruzam fronteira de campo por auto-avanço, preservar o sufixo
  do campo seguinte ou escolher valores de mesmo comprimento.
- **R2 (plataforma, Linux)**: Recital error(118) "Cannot lock record - errno 9"
  na finalização do pedido (est361.dbo:877 → pest360atualizacabecalho →
  gapblank) — divergência real do destino Linux, 4/4 runs, com dados válidos.
  Investigar lock de registro no Recital 24 Linux.
- **R3 (comparação)**: a máscara volátil não cobre o rótulo de plataforma da
  barra de status ("IBM Aix (Common)" × "Linux x86") — em replay
  cross-platform nenhum checkpoint converge limpo (Linux: 0/103). Candidato a
  entrar em `mask_volatile_screen_text`.
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

Critério de aprovação futuro: repetir a jornada com R1 corrigido (comb/tam)
no AIX até o pagamento confirmado ("Confirma a inclusao do registro? → Sim"
com pedido novo valorizado), e no Linux somente após resolver R2.

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
