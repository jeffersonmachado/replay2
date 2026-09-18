# Relatório de Engenharia — Dakota Replay2 0.9.11 → 0.9.12

**Data:** 2026-09-17
**Base:** 0.9.11 (commit `95a7ce7`, tree hash `901fee84…` — igual ao aceite da 0.9.11)
**Head final:** `2725b48` (master) — 8 PRs mergeados (#32–#39)
**Regra obedecida:** nenhuma afirmação de aprovação depende de evidência ausente;
onde a prova não existe, o veredito é `INCONCLUSIVE` ou `BLOQUEADO POR AMBIENTE`.

---

## 1. Lista de tarefas — estado final

| # | Tarefa | Estado | Evidência |
|---|---|---|---|
| 1 | Baseline (commit, versão, hash, testes) | VERIFICADO | §2 |
| 2 | Oráculo funcional da captura 13 (runs 94–96 AIX, 20–23 Linux) | VERIFICADO | §3, `docs/captura13-oraculo.md` |
| 3 | Benchmark v7 apresenta INCONCLUSIVE sem reescrever histórico | VERIFICADO | §4, PR #32 |
| 4 | Benchmark oficial reproduzível (contrato + hashes reais) | VERIFICADO — executado na 0.9.12, veredito FAIL (bloqueios ambientais, §8 do runbook) | §5, PR #37, `docs/benchmark-oficial-v8.md` |
| 5 | Pacote de evidência verificável das runs reais | VERIFICADO (implementação); export real das runs pós-deploy | §6, PR #34 |
| 6 | Paridade conservative / adaptive_shadow / adaptive | VERIFICADO (AIX + Linux) | §7 |
| 7 | Revisão de riscos do executor | VERIFICADO — 2 bugs reais corrigidos | §8, PRs #36/#38 |
| 8 | Documentação consolidada 0.9.11 (proveniência) | VERIFICADO | PR #33, `docs/medicoes-desempenho.md` |
| 9 | Aceite completo + verificação do pacote (0.9.12) | VERIFICADO — PASSED | §9 |
| 10 | Deploy + re-run funcional AIX (R1) + evidências | VERIFICADO | §10 |

## 2. Baseline (item 1)

- Commit `95a7ce7`, VERSION 0.9.11, tree hash igual ao registrado no aceite.
- Executado **nesta sessão** (não lido de artefato antigo): pytest
  `not slow and not selenium and not external` → **1826 passed**; JS 22/22
  suítes; Tcl 68/68. Log: `/tmp/baseline-0.9.11.log`.
- Falha preexistente conhecida: `ui-tests` do CI falha por ausência do binário
  Chrome no runner (`no chrome binary at /opt/google/chrome/chrome`) — igual no
  PR #31 já mergeado; ambiental, sem relação com as mudanças.
- Pós-merge dos 8 PRs: JS 22/22 e Tcl 68/68 re-verificados; pytest completo
  re-executado (§9).

## 3. Oráculo funcional da captura 13 (item 2) — veredito: jornada NÃO APROVADA na 0.9.11

Auditoria individual de todas as falhas (AIX 94–96: 96 falhas/run; Linux
20–23: 103/run), com run, sessão, seq_global, assinaturas, de→para e
classificação por grupo em `docs/captura13-oraculo.md`:

- **AIX — falha do dado sintético (bug do Replay2, corrigido):** o comb
  `0000135` (7 teclas, auto-avanço comb→Tam) virou `00001` + 2 eventos vazios
  na trilha sintética; o Tam nunca foi preenchido → ERP rejeitou o item
  ("Codigo nao cadastrado", 9×/run) e o pedido **D00011140 persistiu com
  Total 0,00 / Qtd 0** nas 3 runs (trilha observada verify_ok=1, reconstruída
  na TerminalEngine). 24 grupos de seq (215–372) estavam mascarados como
  `synthetic_data_swap/low` com o erro do ERP visível na tela.
- **Linux — divergência funcional real do destino (ambiente/ERP):**
  `Recital error(118): Cannot lock record` na finalização
  (`pest360atualizacabecalho:2036 ← est361.dbo:877`), 4/4 runs. De→para íntegro
  (qtd 1→7, valor 229,9→1609,3). Ressalva ambiental: bases dessincronizadas
  (mesmo CPF → endereço/situação diferentes entre servidores).
- **Descoberta de medição:** `checkpoints_ok=103` NÃO é convergência — o runner
  incrementa por input em `on_progress`. Convergência limpa real: AIX 5–9/103;
  Linux 0/103 (barra de status de plataforma fora da máscara volátil).

### Correções (TDD, RED→GREEN, PRs #35 e #39)

1. `synthetic_data_swap` exige eco de um **par** do de→para
   (`substitution_pair_echo_present`) — eco só de identificador gerado não
   classifica mais; máscara de→para case-insensitive. Reclassificação medida:
   AIX 144/215 swaps → screen_divergence; Linux 256/260.
2. **R1**: valor sintético mais curto **e prefixo** preserva a tecla original
   nos eventos excedentes (synthetic_trail.py). Prova com a trilha real:
   seqs 185–206 `'0','0','0','0','1','','','\r'` → `'0','0','0','0','1','3','5','\r'`.
3. **R3**: rótulo de plataforma ("IBM Aix (Common)" × "Linux x86") na máscara
   volátil, ancorado ao `|` da linha de status; validado com bytes reais do
   checkpoint seq 89; divergência real continua divergindo (teste negativo).
4. R2 (Recital 118 Linux) = **lacuna documentada** — fora do replay2.

Critério de aprovação da jornada: AIX — repetir a run com R1 até o pagamento
confirmado (§10); Linux — bloqueada por R2.

## 4. Benchmark v7 (item 3) — INCONCLUSIVE apresentado, histórico preservado

- `comparison_payload` detecta payload persistido pré-FASE 4 (sem
  `provenance_problems`/`collector_coverage`) e recalcula dos artefatos,
  corrigindo `benchmark_experiments`/`benchmark_comparisons` (self-healing;
  RUNNING nunca tocado; falha → fallback ao persistido com log).
- CLI `benchmark report` regrava o veredito final no `execution-result.json`.
- Artefatos de apresentação regenerados; `runs/` e `experiment-manifest.json`
  **byte a byte idênticos** (diff -r); evidence-manifest revalidado (285
  arquivos, 0 divergências).
- Testes RED→GREEN (`tests/benchmark/test_v7_apresentacao_service.py`);
  regressão `tests/benchmark/`: 213 passed + TestRegressaoV7 verde.
- UI já traduzia INCONCLUSIVE → badge neutro sem bloco de recomendação.
- Doc: `docs/benchmark-v7-veredito.md`. **O v7 não autoriza recomendação
  AIX × Linux.**

## 5. Benchmark oficial reproduzível (item 4) — preparado E executado; veredito FAIL sem recomendação

- `benchmark/provenance.py`: hashes sha256 REAIS calculados dos artefatos
  (arquivo streaming 1 MiB; conjunto no esquema do evidence-manifest);
  string divergente do calculado → ContractViolation; `provenance.json` gravado
  como evidência; placeholder óbvio → rc=2 legível; fluxo legado retrocompatível.
- Gates de evidência essencial com testes: rede ausente, clock skew não medido,
  sem evidência funcional, proveniência placeholder → **INCONCLUSIVE sem
  recomendação**.
- Runbook operacional completo: `docs/benchmark-oficial-v8.md` (contrato único:
  captura 13/51, seed 42, 80x24, níveis [1,5,10,20], warmup 30/medição 120/
  cooldown 30 s, 2 iterações, stop conditions, sonda de recuperação 60 s, gate
  de clock 1000 ms; pré-requisitos NTP + coletor de rede).
- **EXECUTADO em 2026-09-17 (0.9.12)** — tentativa 1 abortada por queda de VPN
  (`environment_unreachable_mid_run`, comprovado: timeout SSH porta 22 nos dois
  hosts durante a janela; VPN estável 6/6 antes da tentativa 2). Tentativa 2
  COMPLETED com veredito **FAIL**, sem recomendação. Quatro bloqueios
  ambientais, todos documentados com evidência no runbook §8:
  (1) divergência funcional em 21 passos — bases de dados AIX×Linux
  dessincronizadas + drift da massa desde a captura (PORTA 1 reprova por
  ambiente, não por plataforma); (2) escada parada em concorrência 1 por
  `host_cpu_pct=100` no AIX — carga estranha de 6 usuários interativos
  (load ~9), `saturacao_comprovada`; (3) offset de clock medido acima do gate
  (1209/2116 ms — inclui latência do SSH de medição; hosts a ~1 s entre si,
  estação fora de NTP); (4) cobertura parcial (rede só em pacotes no AIX,
  paginação ausente no Linux) → gargalo não declarável.
- O que ficou provado na ferramenta: proveniência calculada, coleta real de
  host_metrics (51/3224 amostras), janela de rede nos dois hosts, correção de
  skew auditável, parada classificada por evidência, recovery probe medido
  (AIX 11,28 s; Linux 3,71 s) e evidence-manifest cobrindo o resultado final.
- Para a tentativa conclusiva (v8b): bases alinhadas, janela exclusiva no
  MIG24, estação em NTP, novo `experiment_id` — runbook §8.

## 6. Pacote de evidência verificável (item 5)

- `GET /api/runs/{id}/evidence-bundle` → tar.gz: `manifest.json` (sha256 por
  arquivo + `bundle_sha256`), `run.json` (params **sanitizados** — segredos
  viram placeholder, `credential_ref` preservado), `failures.json` completo,
  `sessions.json`, `host-metrics.json`, `verify-results.json`, trilhas
  observadas **byte a byte** (preserva hash-chain/HMAC).
- Verificador independente: CLI `dakota-gateway runs verify-evidence --bundle
  <pacote>` — confere hashes, re-roda `verify_log`, re-computa o resumo de
  falhas dos brutos (pega falha inventada), arquivos extras = problema; sem
  chave HMAC → `hmac: not_verified` explícito; exit 0 VALID / 2 INVALID.
- Lacunas viram `gaps` explícitos no manifesto (sem trilhas, sem métricas).
- 18 testes novos (RED→GREEN); regressão gateway/tests 199 passed.
- **Export real feito na 0.9.12:** bundles das runs 96/99/100 (AIX) e 29
  (Linux) exportados nos servidores e verificados localmente com o verificador
  independente — todos VALID, gaps [] (§10.3). Os JSONs de `dev/tmp/` deixam de
  ser a única fonte dos números de referência.

## 7. Paridade funcional das políticas (item 6)

Mesma jornada (captura 13 AIX = captura 51 Linux — mesmo UUID de sessão), mesma
seed 42, strict-global, 1 sessão, nos servidores de homologação com a 0.9.11:

| Política | AIX run / duração / falhas | Linux run / duração / falhas |
|---|---|---|
| conservative | 97 / 95,6 s / 95 | 24 / 158,5 s / 103 |
| adaptive_shadow | 98 / 90,0 s / 96 | 25 / 157,1 s / 103 |
| adaptive | 99 / 90,1 s / 96 | 26 / 156,3 s / 103 |
| (Linux, preamble corrigido) | — | ver §7.3 |

### 7.1 Equivalência comprovada

- **Mesma massa:** hash de `synthetic_applied` **idêntico** nas 3 políticas de
  cada ambiente (AIX `f4300533…`; Linux `7c3a46e8…` — o mesmo das runs 22/23 da
  0.9.9, provando continuidade da massa entre versões).
- **Mesmo ponto de entrada:** entry_preamble idêntico nas 6 runs AIX
  (`cb9b1c8e…`, incluindo as runs 94–96 da 0.9.9).
- **Mesmos checkpoints:** `last_seq_global_applied=466` idêntico (AIX).
- **Auditoria:** `verify_ok=1` em todas.
- **Falhas:** distribuições por tipo/severidade idênticas entre shadow e
  adaptive; conservative difere por 1 medium (seq 379) já classificado pelo
  oráculo como cascata da dessincronia anterior (posição na saída do sistema) —
  não é diferença funcional induzida pela política.
- **Bytes:** equivalência write-a-write travada por teste (shadow ×
  conservative); síntese determinística na massa (`dataset.jsonl` idêntico em
  passagens repetidas; divergem só `report.json`/`template.json` — metadados
  com timestamps).

### 7.2 Decomposição de tempo (AIX, metrics.adaptive)

| Política | journey_total | erp_response | overhead | sync_wait | checkpoint_wait | ratio |
|---|---|---|---|---|---|---|
| conservative | 58,6 s | 12,3 s | 46,3 s | 38,2 s | 50,5 s | 0,79 |
| adaptive_shadow | 53,6 s | 12,7 s | 40,9 s | 33,1 s | 45,8 s | 0,76 |
| adaptive | 53,3 s | 11,9 s | 41,4 s | 33,0 s | 44,9 s | 0,78 |

Nesta jornada strict-global o batching não engatou (`batch_count=0` — a cadência
é dirigida por checkpoint); o ganho veio de waits adaptativos. Leitura honesta:
adaptive ≈ shadow < conservative, mas a diferença (≈5 s) está dentro da
variância entre runs (95/96 da 0.9.9: 93,8/89,3 s com mesma política).

### 7.3 Achado de configuração (Linux)

As runs 24–26 usaram entry_preamble com âncora do prompt do AIX
(`(ferblo)MIG24:`) — a captura 51 é a mesma sessão gravada no AIX, e a derivação
do preâmbulo usa as teclas gravadas. O passo shell estourava timeout (20 s) a
cada run, inflando sync_wait para ~82 s (vs ~42 s nas runs 22/23, cuja âncora
era `recital24`). **A paridade entre políticas no Linux permanece justa**
(mesmo preâmbulo nas 3). Correção operacional aplicada ao cache da captura 51
(preâmbulo das runs 22/23, documentado no próprio registro) e trio re-executado
— resultados em §7.4 quando disponíveis. Dívida de produto registrada: a âncora
do passo shell deve ser host-agnóstica (usuário sem hostname).

### 7.4 Linux com preâmbulo corrigido (trio v2, 0.9.11)

Mesma jornada/seed após a correção operacional do entry_point da captura 51
(âncora `recital24`, versionado `entry_point_version='0.9.11'`):

| Política | Run | Duração | Falhas (low/medium/swap) | applied hash | last_seq | verify |
|---|---|---|---|---|---|---|
| conservative | 27 | 47,2 s | 103 (20/18/65) | `7c3a46e8a677` | 466 | ok |
| adaptive_shadow | 28 | 46,1 s | 103 (20/18/65) | `7c3a46e8a677` | 466 | ok |
| adaptive | 29 | 45,6 s | 103 (20/18/65) | `7c3a46e8a677` | 466 | ok |

Confirma duas coisas: (1) **45–47 s é o tempo real da jornada no Linux** — os
156–158 s do trio v1 eram integralmente o timeout do preâmbulo; (2) a **paridade
entre políticas se mantém** com o preâmbulo correto: mesma massa (hash idêntico,
o mesmo das runs 22/23 da 0.9.9), mesma distribuição de falhas (20 low / 18
medium / 65 swap em todas), mesmos seqs aplicados, trilhas íntegras.

**Sintético × humano:** a sessão humana gravada (captura 13/51) levou
**127,4 s**; o replay sintético leva ~90 s no AIX e ~46 s no Linux na 0.9.11
(com R1 na 0.9.12, percorrendo o fluxo completo com o item aceito: 94,4 s AIX /
79,8 s Linux — §10.2/§10.4) — o sintetizado permanece **mais rápido que o
humano nos dois ambientes e nas duas versões**, com checkpoints, auditoria e
de→para preservados. O ganho não vem de remover sincronização:
vem de não digitar com cadência humana onde a evidência prova type-ahead
seguro e de waits dirigidos por estado em vez de constantes.

## 8. Revisão de riscos do executor (item 7)

| Gap | Veredito | Teste |
|---|---|---|
| Pause/resume dentro de batch | correto (batch atômico), travado | `test_pause_dentro_de_batch_nao_escreve_e_resume_completa_intacto` |
| Flush parcial em fail-fast | correto por construção, travado | `test_falha_failfast_envia_batch_inteiro_antes_e_nada_depois` + stop global |
| **Jitter × pacing-skip** | **bug real corrigido** — jitter era zerado com o delta | `test_jitter_configurado_sobrevive_ao_skip_de_convergencia` |
| Política adaptive no parallel simples | aviso estruturado (`policy_effective`/`policy_warning` + evento) | `test_runner_avisa_quando_adaptive_cai_no_parallel_simples` |
| **Corrida de cancel (modo continue)** | **bug real corrigido** — run cancelada podia terminar `success`; re-cheque `check_now()` sem TTL antes do sucesso | `test_cancel_visto_so_pelo_worker_nunca_termina_success` |
| Shadow ausente no strict-global | lacuna corrigida — ShadowTracker por sessão; §19 agora vale no modo mais usado | `tests/test_shadow_strict_global_unit.py` (4) |
| Shadow do concurrent atravessava checkpoint no relatório | corrigido (checkpoint = fronteira dura, paridade com offline) | `test_shadow_checkpoint_quebra_run_candidata` |

Regressão da área: 183→184 passed; 10 testes de gaps + 4 shadow + 1 fronteira.

## 9. Aceite completo e pacote (0.9.12) — PASSED

- `bash scripts/final-acceptance.sh` (fases 01–08): **PASSED**.
  `release_run_id`: `release-20260917T153719Z-cf4499e1`; tree hash
  `e6fe1e142b9db1eb26e232abcc11d50cd0a77db89a97f3777e00967550b8fc46`
  (before == after, suíte completa aprovada, version == VERSION).
- `./scripts/build-tarball.sh` → `dist/dakota-replay2-0.9.12-20260917-125315.tar.gz`,
  sha256 `8df56f16cb833852925137ba8e4590545b7a1b9ac952af098e01f5cddbe985bf`.
  Gates de mesma árvore/versão e verify-tarball passaram (build_validate.py).
- `bash scripts/build-selfinstall.sh` → `dist/dakota-replay2-0.9.12-20260917-125315.run`
  (+ `.sha256`).
- Higiene do pacote verificada por listagem: 1160 entradas, **0** segredos
  (`*.key`/`*.pem`/`.env*`), **0** bancos, **0** `gateway/state/`.
- Regressão pós-merge da release: pytest **1881 passed / 2 skipped**, JS 22/22,
  Tcl 68/68.

## 10. Deploy e verificação funcional final (0.9.12)

### 10.1 Deploy

- `remoto_dakota/scripts/deploy.sh --target aix` → self-installer
  `dakota-replay2-0.9.12-20260917-160423.run`, UPDATE sobre a 0.9.11, backup do
  banco, health `{"status":"ok","version":"0.9.12"}`, capture-daemon OK.
- `… --target linux` → `…-20260917-160533.run`, mesmo fluxo, health ok.
- Ambos os servidores em **0.9.12** (`cat /opt/dakota/replay2/VERSION`).

### 10.2 Re-run funcional AIX com R1 — run 100 (jornada APROVADA no AIX)

Driver `/tmp/adaptive_policy_comparison.py /opt/dakota/replay2 13 42 adaptive`
(mesma jornada/seed das runs 94–99): síntese 53,3 s, `journey_total` 94,4 s,
status success, `verify_ok=1`, `last_seq_global_applied=466`, 95 falhas
(20 low / 49 medium / 26 swap — as medium são "checkpoint não estabilizado,
dados substituídos, envio forçado", inerentes ao modo send-anyway sintético).

Prova funcional na trilha observada (`observed_runs/run-100/…/audit-*.jsonl`
reconstruída na TerminalEngine local, evento a evento):

- **"cadastrado": 0 ocorrências** — o erro "Codigo nao cadastrado" das runs
  94–96 desapareceu; o item é aceito (grade: Modelo 303, Comb 00001, **Tam 35
  preenchido** — o R1 preservou as teclas '3','5' do auto-avanço —, Qtd 1,
  Valor 37,90; seq 150 e 220);
- **pedido D00011140 persiste com Total 37,90 > 0** (seq 220, situação
  "04 EM SEPARACAO", item 01 listado na grade definitiva);
- **grade de pagamento alcançada** (seq 180/200: forma 15 PIX preenchida);
- sessão termina limpa: menu "3 MOVIMENTOS" → shell → exit (seqs 230–254).

**Veredito funcional AIX: APROVADO na 0.9.12** (critérios do §3 cumpridos com
evidência reconstruída; as divergências residuais são as cascatas já
classificadas no oráculo, não falhas funcionais novas).

### 10.3 Evidence bundles exportados e verificados (item 5 completo)

Export in-process nos servidores (`control.services.run_evidence_service.
export_run_evidence`) e verificação independente local
(`dakota-gateway runs verify-evidence --bundle …`):

| Pacote | bundle_sha256 | Verificação |
|---|---|---|
| `bundle-run100-aix.tar.gz` (AIX, pós-R1) | `10bee39b…c2b1a` | VALID, gaps: [] |
| `bundle-run99-aix.tar.gz` (AIX, adaptive 0.9.11) | `ac18e5c5…b7ee` | VALID, gaps: [] |
| `bundle-run96-aix.tar.gz` (AIX, 0.9.9/0.9.11) | `32879ebf…8059` | VALID, gaps: [] |
| `bundle-run29-linux.tar.gz` (Linux, adaptive trio v2) | `17bfb39b…2715f` | VALID, gaps: [] |
| `bundle-run31-linux.tar.gz` (Linux, 0.9.12 com R1) | `c1202a06…4565` | VALID, gaps: [] |

`hmac: not_verified` nos 4 por ausência proposital da chave HMAC do servidor
(não copiada) — hash-chain e resumo de falhas re-computados dos brutos e
íntegros. Os números de `dev/tmp/` deixam de ser a única fonte: as runs de
referência agora têm pacote verificável.

### 10.4 Paridade Linux sob 0.9.12

Dois achados na re-execução pós-deploy:

1. **A invalidação por VERSION mordeu de novo:** o cache corrigido do
   entry_point da captura 51 era versionado `0.9.11`; ao subir a 0.9.12 a
   síntese re-derivou o preâmbulo das teclas gravadas (âncora AIX
   `(ferblo)MIG24:`) e a run 30 (síntese completa, 769 s) voltou a estourar o
   timeout do passo shell: `sync_wait` 82,8 s, journey 88,1 s — mesmo
   comportamento das runs 24–26. Reforça a dívida de produto §11.3 (âncora
   host-específica).
2. **Run 31 (referência Linux 0.9.12):** clonada da run 30 (mesma trilha
   materializada pela 0.9.12, já com R1) via `clone_run_parallel.py` com
   `CLONE_MODE=strict-global CLONE_PREAMBLE=linux` — sem re-síntese. Resultado:
   **success, 81,9 s, verify_ok=1, last_seq=466**, falhas 100
   (20 low / 79 medium / 1 swap — a reclassificação estrita do oráculo da
   0.9.12 moveu os swaps de dados para screen_divergence, como projetado no
   PR #35). Varredura da trilha observada: **0 "cadastrado"** (R1 funciona no
   Linux também) e **1 "Cannot lock"** — o R2 persiste, como esperado
   (ambiente/ERP).

Leitura de desempenho: o journey subiu de ~43 s (run 29, 0.9.11) para ~80 s
(run 31, 0.9.12) **porque a jornada agora funciona de verdade** — com o item
aceito, a sessão percorre o fluxo completo (mais telas, mais checkpoints com
dados sintéticos divergentes, cada um pagando a carência de divergência do
send-anyway). O mesmo efeito aparece no AIX (journey 53,3 s na run 99 → 94,4 s
na run 100). Não é regressão de engine: é o custo de percorrer o caminho feliz
com checkpoints sintéticos divergindo por dados.

Bundle `bundle-run31-linux.tar.gz` sha256 `c1202a06…4565` exportado e
verificado localmente: **VALID**, gaps [] (`hmac: not_verified`, idem §10.3).

## 11. Riscos remanescentes

1. **R2 (Recital error 118 no Linux)** — ambiente/ERP; bloqueia aprovação
   funcional da jornada no Linux. Fora do replay2.
2. **R1 residual** — valor sintético não-prefixo em campo com auto-avanço ainda
   esvazia o sufixo; correção plena exige levar a largura da PICTURE ao
   report.json (mudança de contrato, dívida registrada).
3. **Âncora de preâmbulo host-específica** — derivação usa o hostname gravado;
   correção operacional aplicada no Linux; generalização é dívida de produto.
4. **checkpoints_ok ≠ convergência** — métrica conta inputs, não checkpoints
   convergidos; a UI/relatórios devem usar a convergência real (dívida).
5. **Bases AIX × Linux dessincronizadas** — comparações funcionais cruzadas
   exigem massa alinhada.
6. **ui-tests do CI** — falha ambiental preexistente (Chrome ausente no runner).

## 12. Vereditos separados

- **Aptidão do Replay2:** **GO** — aceite 0.9.12 PASSED (§9), engine
  determinística, auditoria/hash-chain íntegras (verify_ok=1 nas runs de
  referência + bundles VALID), pause/cancel/fail-fast travados por teste,
  paridade de políticas medida nos dois ambientes (§7), overhead conhecido e
  decomposto (§7.2), operação 100% offline (§15).
- **Aprovação funcional da jornada (captura 13):** **APROVADA no AIX na
  0.9.12** — run 100 com R1: item aceito, pedido persistido com Total 37,90,
  pagamento alcançado, 0 ocorrências de "Codigo nao cadastrado" (§10.2).
  **Linux: BLOQUEADO POR AMBIENTE/ERP** — R2 (`Recital error(118) Cannot lock
  record` na finalização, 4/4 runs) é falha do destino, fora do replay2 (§3).
- **Decisão de capacidade AIX × Linux:** **INCONCLUSIVE** — v7 histórico não
  autoriza recomendação; v8 **executado** com veredito FAIL (divergência
  funcional por bases dessincronizadas + escada parada por CPU saturada de
  carga estranha no AIX), sem recomendação — as causas e o caminho da
  tentativa conclusiva estão em `docs/benchmark-oficial-v8.md` §8.

## 13. Arquivos alterados (por PR)

- PR #32 `fix/benchmark-v7-inconclusive`: benchmark_service.py, cli.py,
  test_v7_apresentacao_service.py, docs/benchmark-v7-veredito.md
- PR #33 `docs/medicoes-desempenho`: docs/medicoes-desempenho.md
- PR #34 `feature/run-evidence-bundle`: evidence_bundle.py,
  run_evidence_service.py, run_routes.py, verifier.py, cli.py,
  test_run_evidence_bundle.py
- PR #35 `fix/synthetic-swap-oracle`: replay_compare.py, deterministic.py,
  test_synthetic_swap_oracle_unit.py, docs/captura13-oraculo.md
- PR #36 `fix/executor-risk-review`: executors.py, runner.py,
  test_adaptive_executor_gaps_unit.py
- PR #37 `feature/benchmark-v8-provenance`: benchmark/provenance.py, cli.py,
  test_benchmark_prepare_hashes.py, docs/benchmark-oficial-v8.md, AGENTS.md
- PR #38 `feature/shadow-strict-global`: executors.py,
  test_shadow_strict_global_unit.py, test_adaptive_executor_integration.py,
  AGENTS.md
- PR #39 `fix/r1-comb-tam-r3-platform-mask`: synthetic_trail.py, volatile.py,
  test_synthetic_trail_unit.py, test_volatile_mask_unit.py,
  docs/captura13-oraculo.md

## 14. Comandos executados (principais)

- Baseline: `python3 -m pytest -m "not slow and not selenium and not external" -q`
  (1826 passed), `./scripts/test.sh --quick` (22/22), `tclsh tests/all.tcl` (68/68).
- Paridade: `python3 /tmp/adaptive_policy_comparison.py /opt/dakota/replay2
  <capture> 42` nos dois servidores (runs 97/98/99 AIX; 24/25/26 + trio v2
  27/28/29 Linux, captura 51).
- Análise: `policy_parity_analyze.py`, `synth_determinism_check.py`,
  `synth_determinism_fine.py` (dev/tmp/, deployados em /tmp dos servidores).
- CI: 8 PRs com checks verdes (ui-tests = falha ambiental preexistente).
- Release: `bash scripts/final-acceptance.sh` (PASSED), `./scripts/build-tarball.sh`,
  `bash scripts/build-selfinstall.sh` (§9).
- Deploy: `remoto_dakota/scripts/deploy.sh --target aix` e `--target linux`
  (self-installer, health ok nos dois).
- Re-run funcional: `python3 /tmp/adaptive_policy_comparison.py
  /opt/dakota/replay2 13 42 adaptive` (AIX, run 100); reconstrução de telas da
  trilha observada com a TerminalEngine local (0 "cadastrado", Total 37,90).
- Evidências: `export_run_evidence` in-process nos servidores →
  `dakota-gateway runs verify-evidence --bundle …` local → VALID ×4 (§10.3).

## 15. Prova de operação offline

Nenhum componente novo usa rede externa: proveniência = sha256 local; evidence
bundle = leitura local de banco/trilhas; verificador = hash-chain/HMAC local;
shadow = análise in-process. Suíte inteira roda sem Internet (marcadores
`external`/`selenium` desligados). Nenhum SDK de IA, DNS ou socket externo.
