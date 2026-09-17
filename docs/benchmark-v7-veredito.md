# Benchmark v7 (cap13-aix-linux-oficial-v7) — correção da apresentação do veredito

> **Nota oficial:** o benchmark histórico v7 **NÃO autoriza recomendação
> AIX×Linux**. O veredito correto é **INCONCLUSIVE** (sem recomendação).
> Os dados brutos do experimento (`runs/`, `aggregates/`,
> `experiment-manifest.json`) estão **preservados e intactos** — apenas os
> artefatos de apresentação foram regenerados.

## O que estava errado

O v7 foi executado antes da FASE 4 (gates de proveniência e cobertura de
coletor). O veredito `WARN` + recomendação "Equivalência funcional
comprovada..." ficou congelado em quatro lugares:

1. `artifacts/benchmarks/cap13-aix-linux-oficial-v7/report.md` / `report.json`
   / `execution-result.json` (veredito gravado na época da execução);
2. `benchmark_comparisons` (payload pré-FASE 4, sem `provenance_problems` nem
   `collector_coverage`), servido verbatim por
   `comparison_payload()` (`gateway/control/services/benchmark_service.py`)
   sem nunca ser recalculado;
3. `benchmark_experiments.verdict='WARN'`, exibido na lista da UI e copiado
   pela importação do boot (`import_experiments_from_artifacts`).

Recalculado com o contrato atual, o v7 é **INCONCLUSIVE** porque:

- os hashes de proveniência (`journey_set_sha256`, `dataset_sha256`,
  `application_version_sha256`) são idênticos sem justificativa e o
  `think_time_profile.sha256` é um placeholder manual (`"def1277b"` + zeros) —
  a comparação não é auditável (`decision.py`, porta de proveniência);
- o grupo "rede" está ausente no sampler (`bottleneck_evidence.ok=False`) —
  gargalo não declarável;
- o clock skew medido (AIX ~171 s atrasado) está fora do gate.

Contrato de regressão: `tests/benchmark/test_provenance_hashes.py`
(`TestRegressaoV7`) e `tests/benchmark/test_v7_apresentacao_service.py`
(serviço/UI sobre os artefatos reais).

## O que mudou

- **`gateway/control/services/benchmark_service.py`** —
  `comparison_payload()`: payload persistido pré-FASE 4 (sem
  `provenance_problems`/`collector_coverage` em `comparison`) é recalculado a
  partir do manifesto + runs em disco (`_payload_comparacao_pre_fase4`).
  Quando o recálculo diverge do persistido, grava o payload novo
  (`bp.save_comparison`) e corrige `benchmark_experiments`
  (`bp.update_experiment_status`) — a lista e o boot não reintroduzem o WARN.
  Sem manifesto/runs ou se o recálculo falhar, cai no persistido
  (experimento sem artefatos não quebra). Experimento `RUNNING` nunca é
  tocado.
- **`gateway/dakota_gateway/cli.py`** — `benchmark report` agora regrava
  `verdict`/`reason` finais no `execution-result.json` antes dos artefatos
  (o caminho `run` já fazia isso; sem isso a importação no boot relia o
  veredito antigo).
- **Artefatos de apresentação do v7 regenerados** —
  `report.md`, `report.json`, `execution-result.json` e
  `evidence-manifest.sha256` de
  `artifacts/benchmarks/cap13-aix-linux-oficial-v7/` passam a exibir
  INCONCLUSIVE + "Sem recomendação". `runs/`, `aggregates/` e
  `experiment-manifest.json` **byte a byte idênticos** (verificado por
  `diff -r` antes da cópia; evidence-manifest revalidado: 285 arquivos, 0
  divergências).
- **UI** (`gateway/control/static/js/pages/benchmark.js`) — nenhuma alteração
  necessária: com o payload corrigido, `verdictBadge("INCONCLUSIVE")` exibe
  "INCONCLUSIVO" (estilo neutro) e o bloco de recomendação fica oculto porque
  `recommendation` é `null`.

## Como verificar

```bash
# regressão do recálculo full-path + apresentação via serviço
python3 -m pytest tests/benchmark/test_provenance_hashes.py \
                  tests/benchmark/test_v7_apresentacao_service.py -q

# artefatos em disco
head -5 artifacts/benchmarks/cap13-aix-linux-oficial-v7/report.md
python3 -c "import json; print(json.load(open(
  'artifacts/benchmarks/cap13-aix-linux-oficial-v7/execution-result.json'))['verdict'])"

# banco local (após abrir o detalhe do experimento na UI ou rodar o CLI)
PYTHONPATH=gateway python3 -m dakota_gateway.cli benchmark status \
  --experiment-id cap13-aix-linux-oficial-v7 --db gateway/state/replay.db
```

Esperado: `verdict=INCONCLUSIVE`, `recommendation=None`, razões citando
"proveniência dos artefatos não comprovada".
