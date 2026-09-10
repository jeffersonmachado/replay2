# ADAPTIVE_REPLAY_ENGINE_REPORT — Motor Adaptativo Determinístico (v0.9.8)

Data: 2026-09-10 · Base: dakota-replay2-0.9.7 · Escopo: jornadas sintéticas e
reais com velocidade próxima/superior à de operador humano, sem sacrificar
determinismo, checkpoints, auditabilidade, comparabilidade AIX×Linux,
segurança ou isolamento de rede. **100% offline. Nenhuma IA/SaaS.**

---

## 1. Arquitetura anterior

Existem **dois motores independentes** de replay real, mais um legado morto:

- **replay_control** (`runner.py` + `executors.py` + `deterministic.py`):
  runs auditáveis, trilha com hash-chain/HMAC, checkpoints, falhas
  estruturadas. Serve os fluxos Synthetic → Replay (X5 e 1-clique).
- **benchmark** (`benchmark/adapters.py` + `benchmark/executor.py`): caminho
  totalmente separado, sessão SSH própria, amostras de latência com
  `monotonic_ns`; compartilha com replay_control apenas `dakota_terminal`.
- **synthetic/remote_executor.py**: executor SSH legado com políticas fixas
  (`select(0.5)`, `sleep(0.3)` antes de cada input, ENTER implícito por
  linha), sem trilha auditável.

## 2. Call graph dos executores (medido no código)

```
POST /api/synthetic/stress/real  (synthetic_routes.py:394)
  → services/synthetic_replay_service.py:35  (modo default parallel-sessions,
    concurrency default 10, params.synthetic=True)
  → synthetic/replay_adapter.py generate_synthetic_jsonl  (trilha auditável)
  → run_service.create_run_request_payload → Runner.start_run_async
      → runner.py dispatch: concurrency>1 →
        executors.replay_parallel_sessions_concurrent_controlled  (pacing por ts_ms)

POST /api/captures/{id}/synthetic-replay  (capture_routes.py:612)
  → capture_synthesis_service.start_synthetic_replay_job → start_synthetic_replay
  → synthetic_trail.build_synthetic_trail  (regrava captura com dados sintéticos)
  → run strict-global determinístico (send-anyway)
      → executors.replay_strict_global_controlled  (cadência por checkpoint)

Benchmark (separado):
  benchmark_service.py:716-732 / cli.py:1180-1230
  → SSHReplayAdapter + BenchmarkExecutor.run → run_level → _run_phase
  → adapter.execute_journey: CHECK(texto) → think → SEND → DRAIN(stable_ms)
    → CHECK(sig)
```

Respostas às perguntas da missão:

- **A.** Synthetic → Replay usa os executors de `replay_control`:
  `replay_parallel_sessions_concurrent_controlled` (X5, com pacing por
  `ts_ms`/`speed`) e `replay_strict_global_controlled` (1-clique,
  determinístico, cadência dirigida por checkpoint).
- **B.** O benchmark usa `BenchmarkExecutor` + `SSHReplayAdapter` — caminho
  separado, sem runs/auditoria de replay_control.
- **C.** `synthetic/remote_executor.py` é **legado**: nenhuma rota HTTP,
  subcomando CLI ou service o importa (grep repo-wide); só testes em
  `dry_run` e o re-export lazy do pacote. **Depreciado** nesta versão
  (docstring + `DeprecationWarning` em `mode="real"`).
- **D.** Pacing por fluxo: concurrent = `delta = ts - last_in_ts`, sleep
  `delta/speed` + jitter (executors.py); strict-global = waits de checkpoint
  (`wait_for_signature_match`); benchmark = `stable_ms` do drain + think
  time fora da medição.
- **E.** Interferem no tempo medido: o pacing por `ts_ms` do concurrent
  (alimentado pela cadência artificial de 50 ms/evento do replay_adapter), o
  `stable_ms` do benchmark (é a definição de convergência do passo) e as
  carências de quiet dos checkpoints (política necessária de sincronização).
- **F.** Necessários para segurança: waits de checkpoint (autoridade de
  validação), early-exit por divergência estável, waits do preâmbulo de
  entrada (`wait_stable_ms` 1000/800 de `synthetic_trail.py` — **só
  bootstrap**, nunca na trilha medida; verificado: são passos do
  `entry_preamble`, executados antes do primeiro checkpoint).
- **G.** Artificiais (corrigidos): cadência de 50 ms/evento do
  replay_adapter; ENTER implícito após todo input; descarte de `{WAIT:ms}`;
  sleeps do remote_executor (legado).

## 3. Causas comprovadas de latência (instrumentadas)

1. **Cadência artificial de 50 ms/evento** (`replay_adapter.py`,
   `ts_ms = sess_ts + seq_session * 50`): no executor concurrent vira sleep
   real de `50/speed` ms por evento — dominante: no benchmark §13, 11.742 ms
   de pacing por 3 sessões curtas (99,5% do tempo total).
2. **Um write por caractere** (evento por char) — overhead de syscalls e de
   loop por evento.
3. **ENTER artificial após todo input** — byte indevido e dessincronia
   potencial; `{ENTER}` de submit era descartado (submit não enviava nada
   quando o trigger era ignorado) e tecla desconhecida virava `\rKEY\r`.
4. **WAIT descartado** — perda semântica de esperas declaradas pela jornada.
5. **Benchmark SSH**: `_drain_until_stable` faz SEND→DRAIN→CHECK por passo —
   porém é a **definição de convergência** do benchmark (ground truth da
   comparação AIX×Linux); não foi removido (ver §15 riscos/limitações).

## 4. Causas descartadas (com evidência)

- `wait_stable_ms=1000/800` de `synthetic_trail.py`: pertencem **somente** ao
  preâmbulo de entrada (bootstrap antes do primeiro checkpoint); não afetam
  a trilha medida. Mantidos — são âncoras de estado, não pacing.
- `remote_executor.py`: fora de qualquer fluxo suportado; depreciado, não
  otimizado.
- `drain_output(0.05)` do strict-global: não bloqueante por evento (0.0 após
  input); dreno final de 0,25 s só no encerramento.
- Ramp-up (`1/ramp_up_per_sec`): espaça **sessões**, não ações — política de
  carga deliberada, fora da latência por ação.

## 5. Testes RED criados (antes da implementação)

| Arquivo | Falha inicial | Cobre |
|---|---|---|
| `tests/test_adaptive_action_model_unit.py` | módulo inexistente (collection error) | §2.1–2.3, §16.1–5 |
| `tests/test_adaptive_classifier_guard_unit.py` | idem | §5, §9, §16.10 |
| `tests/test_adaptive_telemetry_unit.py` | idem | §3, §16.14–15 |
| `tests/test_adaptive_synchronization_unit.py` | idem | §7, §24, §16.11 |
| `tests/test_adaptive_latency_profile_unit.py` | idem | §13, §16.16 |
| `tests/test_adaptive_scheduler_unit.py` | idem | §6, §8, §18–20, §16.6–13, §16.17 |
| `tests/test_adaptive_executor_integration.py` | — | §8, §16.6–9, §16.12–13, §16.21–23, §16.27 |
| `tests/test_adaptive_offline_unit.py` | — | §17, §16.18 |

Regressões de contrato existentes atualizadas por **mudança intencional de
contrato** (não para esconder regressão): `_expected_session_bytes` em
`tests/test_synthetic_replay_service_unit.py` deixou de anexar `\r`
artificial por input (o novo contrato — bytes exatos das ações — é provado
por `test_bytes_finais_equivalem_as_acoes`).

## 6. Implementação

Novos módulos:

- `gateway/dakota_gateway/synthetic/action_model.py` — `SyntheticAction`
  (INPUT/KEY/WAIT/BARRIER/CHECKPOINT), `KEY_BYTES` (fonte única, F1–F12
  completos — F11 estava ausente), `parse_replay_script` determinístico;
  tecla desconhecida = `ValueError` na síntese.
- `gateway/dakota_gateway/replay_control/action_classifier.py` —
  `classify_bytes(data, key_kind=)` determinístico; `UNKNOWN` conservador;
  hint `key_kind` da trilha vale quando consistente, bytes vencem conflito.
- `gateway/dakota_gateway/replay_control/safety_guard.py` — `SafetyGuard`
  com `record_divergence`/`record_resync`; bloqueios: `UNKNOWN_ACTION`,
  `CHECKPOINT_PENDING`, `DIVERGED_STATE`, `NO_EVIDENCE`,
  `CRITICAL_TRANSITION`, ...
- `gateway/dakota_gateway/replay_control/adaptive_scheduler.py` —
  `AdaptiveScheduler.plan_events` (decisões auditáveis com `reason_code` +
  `evidence`), `apply_decisions` (prova de equivalência de bytes),
  `ShadowTracker` (shadow online), `AdaptiveRuntime`, políticas
  `conservative`/`adaptive`/`adaptive_shadow`.
- `gateway/dakota_gateway/replay_control/execution_telemetry.py` —
  `SessionTelemetry`/`RunTelemetry` com buckets disjuntos.
- `gateway/dakota_gateway/replay_control/synchronization.py` —
  `quiet_points` (quiet ≠ convergência) e `extract_typeahead_evidence`
  (`TypeaheadEvidence` auditável).
- `gateway/dakota_gateway/replay_control/latency_profile.py` —
  `LatencyProfile` por ambiente (isolado), percentis exatos, Welford,
  timeout = max(floor, p99 × fator) — nunca p50; JSON local.

Alterados:

- `synthetic/replay_adapter.py` — materialização semântica: sem ENTER
  artificial; WAIT vira evento auditável (`key_kind="wait"`, payload vazio,
  avança o relógio da sessão); chars do mesmo campo dividem `ts_ms`;
  `key_kind` em todo evento de input; hash-chain/HMAC inalterados.
- `synthetic/journey_builder.py` — `generate_replay_script` declara
  confirmações explicitamente (`{KEY:ENTER}` após navegação/campo/seleção;
  submit honra o trigger, ex.: `{KEY:F10}`; verify vira `{VERIFY:sig}`).
  Compatível: bytes na trilha idênticos aos anteriores para
  navigate/input/select; submit passa a enviar a tecla certa (bug fix).
- `replay_control/executors.py` — executor concurrent com batching streaming
  conservador (policy `adaptive`), telemetria (pacing/explicit_wait/send/
  checkpoint wait separando ERP de carência), shadow tracker, guard com
  divergência→conservador; strict-global instrumentado (sem batching — a
  cadência já é por estado).
- `replay_control/runner.py` — cria `AdaptiveRuntime` a partir de
  `params.execution_policy` (default conservative), publica
  `metrics_json["adaptive"]`, grava `gateway/state/latency_profile.json`.
- `replay_control/__init__.py` — fachada exporta o motor.
- `control/services/synthetic_replay_service.py` — aceita
  `execution_policy` no body.
- `synthetic/remote_executor.py` — depreciado.

## 7. Arquitetura nova

```
jornada → generate_replay_script (marcadores explícitos)
        → parse_replay_script → [SyntheticAction]  (semântica preservada)
        → ReplayAdapter → audit-*.jsonl (key_kind, ts semântico, WAIT
          auditável, hash-chain+HMAC)
        → Runner (AdaptiveRuntime: policy + RunTelemetry + LatencyProfile)
        → executor concurrent:
            adaptive: classifica → guard → batch de imprimíveis contíguos /
                      barreira / fallback; pacing só em deltas semânticos
            shadow:   caminho conservador + ShadowTracker (would_*,
                      predicted_saved_ms, false_safe_decisions)
            conservative: comportamento histórico (default, rollback)
```

## 8. Política de batching (Fase 1 — conservadora)

- Elegível: **somente** `PRINTABLE_INPUT` contíguo na mesma sessão.
- Nunca atravessa: checkpoint (fronteira dura), ENTER/ESC/TAB/F-key/setas
  (barreiras), WAIT explícito, ação desconhecida, evento que exige
  comparação determinística.
- Boundary com pacing > 0 só colapsa com evidência: trilha sintética
  (deltas artificiais por construção) ou `safe_typeahead_evidence` da
  captura (`params.typeahead_evidence`).
- Divergência em qualquer checkpoint da sessão → guard divergente → todos
  os passos seguintes em FALLBACK_CONSERVATIVE até ressincronizar
  (checkpoint casado).
- Buffers são locais ao worker: batching nunca mistura sessões; a ordem dos
  bytes dentro da sessão é idêntica (teste §16.21/22); pause/cancel são
  checados por evento e antes de cada write do batch (§16.23).

## 9. Política de barriers

`BARRIER` com `reason_code`: `SCREEN_TRANSITION` (ENTER/ESC/TAB/setas),
`FUNCTION_KEY_BARRIER`, `EXPLICIT_WAIT`, `STATE_DEPENDENCY`. Checkpoint →
`WAIT_FOR_STATE` + `CHECKPOINT_REQUIRED`. Desconhecido →
`FALLBACK_CONSERVATIVE` + `UNKNOWN_ACTION`.

## 10. Safety guard

Regras em ordem: estado divergente → checkpoint pendente → ação desconhecida
→ classe barreira no batch → colapso de delta sem evidência. Fallback
conservador não é erro: é execução evento a evento com o pacing original,
com `conservative_fallback_count` e motivo registrados.

## 11. Shadow mode

`execution_policy=adaptive_shadow`: execução conservadora inalterada +
`ShadowTracker` online (1 evento de memória) registrando
`total_actions`, `batch_candidates`, `safe_candidates`,
`rejected_candidates`, `fallbacks`, `checkpoint_crossing_attempts`,
`divergences`, `false_safe_decisions`, `potential_saved_ms` — agregado em
`metrics_json["adaptive"]["shadow"]`. Critério de promoção (§20):
**`false_safe_decisions = 0`** obrigatório; uma decisão rápida que quebra
estado é muito pior que perder um batch.

### 11.1 Avaliação shadow offline sobre capturas reais

`replay_control/shadow_eval.py` (`evaluate_capture`/`evaluate_captures`) +
`scripts/shadow_eval_adaptive_replay.py` rodam o MESMO ShadowTracker +
AdaptiveScheduler + extração de evidência sobre os `audit-*.jsonl`
gravados, sem enviar byte nenhum, e verificam em dados reais:
equivalência de bytes do batching (§16.7), inviolabilidade do checkpoint
(`checkpoint_violations == 0`) e falso-seguro (sessão sem `session_end`
vira divergência no tracker — qualquer run "segura" nela contaria como
falso-seguro). Resultado local (`artifacts/adaptive-shadow-evaluation.json`,
3 capturas com trilha / 4 sessões / 162 ações):

- 17 candidatos a batch → 11 seguros (evidência de type-ahead), 6 rejeitados
  por ausência de evidência;
- `potential_saved_ms = 145`; writes 162 → 149;
- **`false_safe_decisions = 0`, `checkpoint_violations = 0`,
  `bytes_equivalent = true`** → `promotion_criteria_met = true`.

A massa local é pequena; a validação de promoção em volume deve repetir o
script no MIG24 (as demais capturas locais tiveram a trilha expurgada —
só restam os diretórios vazios). Correção trazida por esta avaliação:
`apply_decisions` passou a emitir os bytes de decisões BARRIER como write
individual (o executor real sempre os enviou — flush + send isolado; a
prova de equivalência é que estava errada ao descartá-los).

## 12. Métricas (definições matemáticas — sem dupla contagem)

- `journey_total_ms`: soma do tempo de parede das sessões (1º→último evento);
- `erp_response_ms`: porção dos waits de checkpoint atribuível ao ERP
  (elapsed − carência quiet quando casado; elapsed total quando divergiu);
- `replay_overhead_ms = journey_total − erp_response = pacing +
  explicit_wait + sync_wait + send + compare + other` (invariante testado);
- `pacing_ms`: sleeps por delta de `ts_ms`; `explicit_wait_ms`: waits
  `{WAIT:ms}` da jornada; `sync_wait_ms`: carências quiet/dreno do Replay2;
  `checkpoint_wait_ms`: total informativo dos waits (nunca somado de novo);
- `time_to_first/last_byte_ms`, `time_to_expected_state_ms`: por ponto de
  sincronização;
- contadores: `batch_count`, `batched_action_count`, `barrier_count`,
  `adaptive_wait_count`, `conservative_fallback_count`, `batching_saved_ms`;
- `replay_overhead_ratio = replay_overhead_ms / journey_total_ms` ∈ [0,1].

## 13. Benchmark antes/depois (§15/§22)

Script reproduzível e offline: `scripts/benchmark_adaptive_replay.py`
(sessões fake com ERP ideal — tudo medido é Replay2; cenário §23-A/B: 3
sessões × 4 campos × 12 chars + navegação + submit F10). Resultado em
`artifacts/adaptive-replay-benchmark.json`:

| métrica | ANTES (legado+conservative) | DEPOIS (materialização) | DEPOIS (adaptive) | Δ adaptive×antes |
|---|---|---|---|---|
| journey_total_ms | 11.803,8 | 26,1 | 38,1 | **−99,7%** |
| pacing_ms | 11.742,1 | 0,0 | 0,0 | **−100%** |
| replay_overhead_ms | 11.803,8 | 26,1 | 38,1 | **−99,7%** |
| writes (syscalls) | 237 | 237 | 33 | −86% |
| batches | 0 | 0 | 12 (216 ações) | — |

Leitura: o Replay2 era ~99,5% do tempo da jornada sintética (pacing
artificial); com timing semântico + batching o overhead cai a dezenas de ms
— o gargalo passa a ser o ERP, como deve ser. Throughput implícito: 216
ações em ~38 ms ≈ 5.700 ações/s (ERP ideal), contra ~20 ações/s antes.

## 14. AIX/POWER × Linux/x86 (§14)

- A política de decisão **não recebe** `environment_id` como entrada — teste
  §16.17 prova planos idênticos para os mesmos eventos nos dois ambientes.
- O perfil de latência é **isolado por ambiente** (teste §16.16: amostras
  AIX e Linux nunca se misturam).
- A espera é por estado (`WAIT_FOR_STATE`/checkpoint): quem responde antes
  termina antes — AIX espera ~420 ms, Linux ~130 ms no exemplo da missão;
  nenhum `sleep(420)` compartilhado.
- Benchmarks reais AIX×Linux: fora do alcance desta estação (requer o MIG24);
  o mecanismo de comparabilidade está preservado (mesma jornada/massa/seed/
  checkpoints — o executor de benchmark não foi alterado).

## 15. Riscos

- **Raw mode sem checkpoints** pode mandar type-ahead mais rápido que o ERP
  processe (PTY buffers preservam ordem, mas telas que drenam input podem
  perder teclas) — mitigado: batching só de imprimíveis contíguos; barreiras
  intactas; policy default continua `conservative`.
- **`_drain_until_stable` do benchmark** impede type-ahead entre passos —
  mantido de propósito: é o critério de convergência que torna AIX×Linux
  comparável; removê-lo exigiria ground truth de estado equivalente.
- Métricas `erp_response_ms` em strict-global aproximam ERP por
  `elapsed − quiet` (o wait não expõe o instante exato do estado) —
  suficiente para overhead ratio, documentado.
- Trilhas antigas sem `key_kind` classificam pelos bytes — conservador por
  construção.

## 16. Limitações

- Batching restrito a imprimíveis (Fase 1); ENTER/F-keys com prova forte de
  type-ahead ficam para fase posterior.
- Type-ahead evidence: o Runner extrai automaticamente da captura
  (`extract_capture_typeahead_evidence`, uma sessão por vez via índice de
  offsets — memória limitada) quando a política é não-conservadora e a
  trilha é real; `params.typeahead_evidence` explícito vence e
  `params.typeahead_stable_ms` (default 150) ajusta o critério.
- Shadow mode cobre o executor concurrent; strict-global tem telemetria mas
  não shadow (sua cadência já é dirigida por estado).
- `ttfb` por checkpoint não é medido (o wait agrega); `ttlb`/`tts`
  aproximados pela porção ERP do wait.
- Nenhuma IA/LLM — por exigência; a interface de evidência/metadados está
  pronta para um classificador futuro sem mudar o motor.

## 17. Arquivos alterados

Novos: `synthetic/action_model.py`, `replay_control/action_classifier.py`,
`replay_control/safety_guard.py`, `replay_control/adaptive_scheduler.py`,
`replay_control/execution_telemetry.py`, `replay_control/synchronization.py`,
`replay_control/latency_profile.py`, `replay_control/shadow_eval.py`,
`scripts/benchmark_adaptive_replay.py`,
`scripts/shadow_eval_adaptive_replay.py`,
9 arquivos de teste (`tests/test_adaptive_*`), artefatos
`artifacts/adaptive-replay-benchmark.json`,
`artifacts/adaptive-shadow-evaluation.json` e
`artifacts/adaptive-replay-readiness.json`.

Alterados: `synthetic/replay_adapter.py`, `synthetic/journey_builder.py`,
`synthetic/remote_executor.py` (depreciação), `replay_control/executors.py`,
`replay_control/runner.py`, `replay_control/__init__.py`,
`control/services/synthetic_replay_service.py`,
`control/services/capture_synthesis_service.py` (execution_policy no
1-clique), `control/routes/capture_routes.py`, `cli.py`
(`runs create --execution-policy`), `static/js/components/detail_views.js`
(cartão "Motor de execução"), `static/js/pages/run_detail.js`,
`static/js/pages/captures.js` + `templates/captures.html` (select
"Execução"), `scripts/js-tests.manifest`,
`tests/test_synthetic_replay_service_unit.py` (contrato intencional §2.2),
`tests/test_synthetic_replay_job_unit.py`, `AGENTS.md`.

## 18. Comandos executados

```bash
python3 -m pytest -q tests/test_journey_synthesizer.py tests/test_synthetic_replay_service_unit.py \
  tests/test_synthetic_entry_unit.py tests/test_checkpoint_early_exit_unit.py \
  tests/benchmark/test_ssh_replay_adapter_unit.py        # baseline: 85 passed
python3 -m pytest -m "not slow and not selenium and not external" -q  # baseline: 1663 passed
python3 -m pytest tests/test_adaptive_* tests/test_synthetic_replay_service_unit.py ...  # por fase (RED→GREEN)
python3 scripts/benchmark_adaptive_replay.py --json artifacts/adaptive-replay-benchmark.json
python3 scripts/shadow_eval_adaptive_replay.py   # → artifacts/adaptive-shadow-evaluation.json
./scripts/test.sh --js        # 17/17 suítes
tclsh tests/all.tcl           # 68/68
python3 -m pytest -m "not slow and not selenium and not external" -q  # final
```

## 19. Resultados dos testes

- Baseline (antes de qualquer alteração): 85 passed (subconjunto) + 1663
  passed (suíte não-lenta), JS 17/17, Tcl 68/68 — árvore limpa, v0.9.7.
- Novos testes do motor: 98 testes em 9 arquivos `test_adaptive_*`, verdes
  (inclui `test_adaptive_shadow_eval_unit.py` — avaliação offline §19–§20).
- **Regressão final: 1764 passed, 0 failed** (suíte Python não-lenta) +
  JS 18/18 suítes + Tcl 68/68. Nenhum teste desabilitado, nenhum timeout
  aumentado, nenhum checkpoint removido.

## 20. Evidência offline (§17)

`tests/test_adaptive_offline_unit.py`: (a) análise estática — nenhum módulo
do motor importa `socket`/`http`/`urllib`/`requests`/SDK de IA; (b) análise
dinâmica — pipeline completo (parse → classificação → scheduler → evidência
→ telemetria → perfil de latência com persistência) executa com
`socket.socket`/`getaddrinfo`/`create_connection` sabotados para lançar
`AssertionError`. Verde: o motor não toca rede nem DNS.
