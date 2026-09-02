# PERFORMANCE_CORRECTIONS_REPORT.md — Correções, endurecimento metodológico e performance

Missão executada sobre o Dakota Replay2 a partir da versão 0.8.87 (commit 7d1ed4a).
Data: 2026-09-02. Baseline e resultado final em `artifacts/performance-corrections/`.

---

## 1. Resumo executivo

Dez frentes de correção implementadas com TDD obrigatório (teste de regressão
vermelho antes de cada correção), sem afrouxar assertions, sem mocks em
produção e sem fabricar métricas. Destaques medidos:

- Throughput do benchmark deixou de ser subestimado em ~12,6× (erro de
  metodologia confirmado e corrigido; recálculo do v7 com desvio < 0,01% das
  referências).
- Caminho de captura do terminal 3,0× mais rápido (67,00 ms → 22,55 ms no
  payload de scroll de 6,4 KB; a variante com slots=True chegou a 3,7× mas
  foi revertida por incompatibilidade com o Python 3.9 do AIX), assinaturas
  byte a byte idênticas.
- Inserções SQLite em lote: ~410× (101 → 41.469 linhas/s, mesmos defaults de
  durabilidade).
- Auditoria em lotes: 888 → 3.536 ev/s (1 sessão) e até 7.755 ev/s em lotes
  de 16, com integridade da trilha intacta.
- Replay paralelo de verdade: 1.000 sessões com concurrency=10 usam 10
  workers e 0 threads extras; memória por sessão dezenas de MB → índice de
  1,3 MB em capture de 21,9 MB.
- Monitor de 40 eventos em captura de 1 GB: 0,598 s → 0,002 s.
- Pacote de release: ~10,3 MB → ~3,65 MB gz (−65%), reproduzível e
  vinculado ao aceite da árvore exata.
- Aceite/benchmark agora recusam conclusões sem evidência: v7 recalcula como
  INCONCLUSIVE (skew de relógio de 171 s, hashes placeholder, rede ausente)
  em vez de "equivalência OK".

## 2. Causa raiz de cada problema

1. **Throughput** — `_janela_segundos`/`_level_stats` usavam max(fins)−min(inícios)
   sobre repetições sequenciais, incluindo intervalos ociosos entre runs
   (~2.500 s de janela para ~190 s de medição real por nível → erro ~12,6×).
2. **Aceite desvinculado** — `final-acceptance.sh` registrava
   `SOURCE_TREE_UNCHANGED=False` e seguia; `build-tarball.sh` só exigia a
   existência dos artefatos; o results JSON não tinha `version`.
3. **Benchmark sem gargalo** — `host_series = []` hard-coded; sem gate de
   clock skew; cobertura de coletores não medida; falha de admissão tratada
   como saturação; sem fase de recuperação.
4. **Equivalência funcional** — veredito "OK" emitido com basis `per_env`,
   evidência única e hashes placeholder; relatório v7 contraditório.
5. **SQLite autocommit** — `isolation_level=None` + INSERT por linha =
   fsync por linha; pool não contava as conexões de `min_size` e podia
   exceder `max_size` sob concorrência.
6. **Terminal/snapshots** — 4 travessias independentes da matriz por snapshot
   (dict + text_sig + visual_sig + semantic_sig) e 2.000 alocações por reset.
7. **Auditoria** — append por evento com checkpoint de estado por evento;
   writer quebrava com 8 threads (flock não serializa threads do processo).
8. **Replay** — modo "parallel" era sequencial; modo concurrent criava 1
   thread por sessão; capture inteiro materializado por sessão; query SQLite
   de pause/cancel por evento; metadados relidos várias vezes por run.
9. **Verificação/índice** — `read_bytes()` do log inteiro; verificação lia
   cada arquivo duas vezes; monitor lia o arquivo inteiro para 40 linhas;
   condição de duplicata logicamente impossível; faltavam índices SQLite
   quentes de `replay_failures`.
10. **Control plane** — auth 2× por request; threads ilimitadas; broadcast
    sob lock global sem remoção de clientes mortos; mermaid.min.js (3,3 MB)
    relido do disco por request; `/ready` vazava conexão em exceção.
11. **Empacotamento** — 7 experimentos de benchmark (85 MB) no pacote; tar
    sem normalização de owner/ordem/mtime; sem validação do pacote extraído.

## 3. Testes adicionados

| Arquivo | Testes | Fase |
|---|---|---|
| `tests/benchmark/test_throughput_methodology.py` | 15 | 1 |
| `tests/benchmark/test_host_coverage.py`, `test_clock_skew.py`, `test_stop_classification.py`, `test_recovery_probe.py` | — | 3 |
| `tests/benchmark/test_functional_coverage.py`, `test_provenance_hashes.py` | — | 4 |
| `tests/benchmark/test_session_tails_isolation.py` | 2 | 8/3 |
| `tests/benchmark/test_persistence_batch_unit.py` | 3 | 5 |
| `tests/test_db_connection_unit.py` | 14 | 5 |
| `tests/test_terminal_performance_unit.py` | 13 (+46 subtests) | 6 |
| `gateway/tests/test_audit_writer_batch.py` | 19 | 7 |
| `tests/test_capture_daemon_unit.py` | +7 | 7 |
| `tests/test_replay_concurrency_unit.py` | 14 | 8 |
| `tests/test_verifier_streaming_unit.py` | 9 | 9 |
| `tests/test_capture_index_unit.py` | 11 | 9 |
| `tests/test_gateway_observability_tail_unit.py` | 9 | 9 |
| `tests/test_db_layer_unit.py` | +1 (índices quentes) | 9 |
| `gateway/tests/test_auth_single_per_request.py`, `test_static_assets_cache.py`, `test_websocket_broadcaster.py`, `test_http_connection_limit.py`, `test_ready_db_release.py` | 16 | 10 |
| `tests/test_engineering_route_support_unit.py` | 4 | 10 (integração) |
| `tests/test_build_hardening_unit.py` | 20 | 2/11 |

Todos demonstrados vermelhos antes da correção correspondente.

## 4. Arquivos alterados

52 arquivos modificados + 26 criados (lista completa: `git show --stat` do
commit da missão). Principais: `benchmark/{comparison,coverage,contract,
decision,degradation,executor,models,normalize,persistence,report,adapters}.py`,
`db/{connection,schema}.py`, `dakota_terminal/{model,attributes,engine,
snapshot}.py`, `audit_writer.py`, `capture_daemon.py`, `audit_client.py`,
`replay_control/{window,executors,runner}.py`, `replay.py`, `verifier.py`,
`session_index_cache.py`, `gateway_observability_service.py`,
`control/{server,auth_support,websocket_support,engineering_route_support}.py`,
`control/routes/ui_routes.py`, `scripts/{final-acceptance.sh,build-tarball.sh,
tree_hash.py,build_validate.py}`, `scripts/acceptance/{gen-evidence-manifest.sh,
regen-baseline.sh}`. Removido: `scripts/acceptance/generate-final-report.py`
(escritor órfão de results JSON com schema incompatível com o novo gate).

## 5. Resultado antes/depois (funcional)

- Aceite: árvore podia mudar durante os testes sem abortar → hash
  antes/depois exigido igual, build reprova aceite de outra árvore/versão.
- Benchmark: "saturation" por falha de admissão → 7 categorias de parada
  (licença/login/launcher/orquestrador/inacessível/saturação comprovada/
  não determinada); gargalo sem cobertura → `unknown` + INCONCLUSIVE.
- Equivalência: v7 dizia "OK" com dados por ambiente → agora INCONCLUSIVE
  com `functional_coverage`, proveniência de hashes e cobertura por coletor.

## 6. Benchmarks antes/depois

| Medição | Antes | Depois | Fonte |
|---|---|---|---|
| Terminal: scroll 6,4 KB (capture path) | 67,00 ms | 22,55 ms (3,0×) | `dev/bench_terminal.py` (mediana de 7, medido após a reversão do slots=True) |
| Terminal: jornada real captura 8 | 16,27 ms | 12,23 ms (1,3×) | idem |
| SQLite: 5.000 inserts (ext4, delete/FULL) | 49,27 s (101 l/s) | 0,121 s (41.469 l/s, ~410×) | `dev/bench_sqlite_batch.py` |
| Auditoria C=1 (ev/s) | 888 | 3.536 (7.755 em lotes de 16) | `dev/bench_audit_writer.*.json` |
| Auditoria C=32 p99 | 363 ms | 176 ms (lotes: < 10 ms) | idem |
| Replay: 1.000 sessões conc=10 | 1.000 threads + capture inteiro em RAM | 10 workers, índice 1,3 MB | teste medido |
| verify_log 100 MB / 1 GB | 6,25 s / 63,6 s | 5,43 s / 58,2 s | `dev/tmp/fase9/` |
| monitor 40 eventos 1 GB | 0,598 s | 0,002 s | idem |
| Pacote release | 10,3 MB gz / 85 MB | ~3,65 MB gz / 27 MB (−65%) | build_validate |

Mediana de ≥5 execuções onde exigido (terminal: 7; SQLite: 5; auditoria: 5;
verifier/índice: 5).

## 7. Compatibilidade AIX/Linux

- **Incidente real e correção**: o primeiro deploy da 0.9.0 no MIG24 falhou
  no boot do control plane — `dataclass(slots=True)` (3.10+) em
  `dakota_terminal/model.py` e `attributes.py` é incompatível com o Python
  3.9 do AIX. Revertido para `frozen=True` puro e guardado pelo teste de
  regressão `CompatibilidadePython39Tests` (varre todo `gateway/`).

- Sem bash-isms novos; tar reproduzível feature-tested (AIX cai no caminho
  clássico, documentado em CHECKLIST_EMPACOTAMENTO.md).
- WebSocket: deadline de envio via `MSG_DONTWAIT`+`select` com fallback
  portável; `settimeout` não é tocado.
- SQLite: WAL/`synchronous=NORMAL` apenas opt-in via env; defaults intactos.
- Coletor de rede: `/proc/net/dev` (Linux) e `netstat -i` (AIX).
- Auditoria: formato da trilha byte a byte idêntico (verifier valida trilhas
  gravadas pelo código novo; `test_integrity.py` intacto).
- Suíte Tcl 68/68; JS 244/244; Python completa — ver final.json.

## 8. Riscos residuais

- `audit_writer`: heurística de cura por mtime mantida (risco teórico de
  cegueira se escrita parcial e state caírem no mesmo tick de clock do fs;
  pré-existente, documentado).
- `session_index_cache`: linha parcial no fim de arquivo em gravação não é
  indexada até o delta seguinte (fail-safe → varredura).
- Detalhe de sessão "morno" ainda relê as linhas da sessão por seek; o ganho
  cresce com sessões minoritárias em capturas grandes.
- Índice global só para capturas planas ≥ 4 MiB (abaixo disso a varredura
  direta é barata).
- Flake pontual observado uma vez em `test_secrets_and_state_never_packaged`
  (7 rodadas seguintes verdes; `verify-tarball` agora imprime diff por
  arquivo para diagnóstico se recurrir).
- Sem fsync por lote (default), queda de energia pode perder lotes no page
  cache; `DAKOTA_AUDIT_FSYNC=1` reduz a janela ao lote em voo.

## 9. Tarefas impossíveis por ausência de ambiente externo

- **FASE 12 (benchmark oficial com carga real AIX×Linux)**: não executado —
  exige ambientes formalmente configurados e autorizados, clock sincronizado
  e janela de stress. Sem autorização formal nesta sessão, nenhuma carga foi
  improvisada. A validação metodológica foi feita pelo recálculo dos
  artefatos reais do v7 (sem nova carga).

## 10. Comandos exatos executados

Ver `artifacts/performance-corrections/final.json` (lista completa com
durações e códigos de saída). Principais:

```sh
python3 -m pytest tests/ gateway/tests/ -q -m "not slow and not selenium and not external"
node --test $(grep -v '^#' scripts/js-tests.manifest | grep -v '^\s*$')
tclsh tests/all.tcl
python3 scripts/tree_hash.py --root . --manifest-out /tmp/manifest.json
bash scripts/final-acceptance.sh
./scripts/build-tarball.sh
python3 scripts/build_validate.py verify-tarball --root . --tarball dist/<pkg>.tar.gz
```

## 11. Hash final da árvore

Ver `artifacts/performance-corrections/final.json` (`tree_sha256_final`) —
calculado por `scripts/tree_hash.py` sobre a árvore commitada, idêntico ao
registrado no aceite e ao da árvore extraída do tarball.

## 12. Validação do tarball

`verify-tarball`: extração em tmpdir, VERSION presente e igual,
`gateway/control/server.py` presente, scan de itens proibidos (segredos,
bancos, estado, caches), hash da árvore extraída == hash do aceite. Resultado
em `final.json` (`tarball_validation`).
