# Medições de Desempenho — Registro Consolidado com Proveniência

**Propósito:** separar inequivocamente as medições das versões 0.9.7, 0.9.8,
0.9.9, 0.9.10 e 0.9.11, cada número com versão, captura, seed, host,
política, data, artefato de origem e método de cálculo.

**Regra deste documento:** nenhum número sem fonte. Quando a fonte é um
arquivo `dev/tmp/` (não empacotado no release), isso está marcado como
**fonte não empacotada** — ver item 5 do plano de engenharia da 0.9.11.

---

## 1. Tipos de medição (não confundir)

| Tipo | O que mede | Onde roda | Autoridade |
|---|---|---|---|
| **Benchmark com ERP ideal** | velocidade pura do executor contra um terminal simulado local | local, offline | prova de overhead do Replay2, não prova de ERP |
| **Teste local / shadow offline** | decisões do scheduler sobre trilhas gravadas, sem rede | local, offline | prova de segurança da otimização |
| **Replay em homologação** | jornada real contra o ERP Recital 24 nos servidores | MIG24 (AIX) / recital24 (Linux) | evidência funcional e de tempo real |
| **Benchmark oficial de capacidade** | decisão AIX × Linux com contrato, proveniência e coleta completa | ambos os servidores | **único que autoriza recomendação de plataforma** |

---

## 2. Baseline humana

| Métrica | Valor | Método | Fonte |
|---|---|---|---|
| Tempo do operador humano na jornada da captura 13 | **127,4 s** | span primeiro → último `deterministic_input` da captura 13, medido por `dev/tmp/human_span.py` sobre o audit-*.jsonl da captura | captura 13 (AIX/MIG24); script em `dev/tmp/` (**fonte não empacotada**); citado em `ADAPTIVE_REPLAY_ENGINE_REPORT.md` §21 |

---

## 3. Campanha de replay em homologação (jornada da captura 13)

Captura 13 no AIX (mesma jornada = captura 51 no Linux), seed 42, política
`adaptive`, replay sintético real contra o ERP. Tabela por iteração de
desenvolvimento (nomes v1–v4 são os sufixos dos arquivos de dev/tmp):

| Iteração | Versão | AIX/POWER (MIG24) | Linux/x86 (recital24) | Fonte |
|---|---|---|---|---|
| v1 (strict) | 0.9.7 | ~365 s (run 87) | 78,9 s (run 17) | `dev/tmp/v-strict-*.json` (**não empacotado**) |
| m (strict) | 0.9.8 (intermediária, não publicada no relatório) | 277,4 s (run 85) | 189,6 s (run 14) | `dev/tmp/m-strict-*.json` (**não empacotado**) |
| v2 (strict) | 0.9.8 | 221,6–223,1 s (runs 89/90) | 56,4–56,7 s (runs 18/19) | `dev/tmp/v2-strict-*.json` (**não empacotado**) |
| v3 (strict, instrumentado) | 0.9.8 + telemetria | 223,4–223,6 s (runs 93/94) | — | `dev/tmp/v3-strict-aix.json` (**não empacotado**) |
| **v4 (strict)** | **0.9.9** | **93,8 s (run 95) / 89,3 s (run 96)** | **46,2 s (run 22) / 45,4 s (run 23)** | `dev/tmp/v4-strict-*.json` (**não empacotado**); verificado nos bancos dos servidores em 2026-09-17 |
| v2 (paralelo-8) | 0.9.8 | 227,7–230,4 s (runs 91/92) | 63,6 s (runs 20/21) | `dev/tmp/v2-par8-*.json` (**não empacotado**) |

Datas dos runs: 2026-09-11 e 2026-09-12 (registros `created_at_ms` dos
bancos `replay.db` dos servidores). Falhas por run: AIX 94–98, Linux 103
(análise de classificação em `docs/captura13-oraculo.md`).

**Decomposição v4 (run 95 AIX):** `journey_total_ms` 57,3 s;
`erp_response_ms` 11,9 s; `replay_overhead_ms` 45,4 s (ratio 0,79);
`pacing_ms` 0. Fonte: `metrics_json.adaptive` da run 95, exportado em
`dev/tmp/v4-strict-aix.json`.

**Leitura autorizada:** v0.9.9 colocou o replay sintético strict-global
**abaixo do tempo humano nos dois ambientes** (AIX −26% a −30%; Linux −64%).
Isso é evidência de *aptidão do Replay2*, **não** de capacidade AIX × Linux.

## 4. Versões 0.9.10 e 0.9.11 — sem nova medição, por construção

`git diff v0.9.9..v0.9.11 -- gateway/dakota_gateway/replay_control/
replay_compare.py replay.py` → **zero alterações de engine**. As versões
0.9.10 e 0.9.11 contêm exclusivamente simplificação de UI (templates, JS de
apresentação, testes de rótulos) e documentação. Portanto a evidência de
desempenho da v0.9.9 **vale para a 0.9.11 por identidade do engine** —
verificável com o comando acima. Nenhuma medição nova foi feita nessas
versões; qualquer número novo será registrado aqui com sua própria linha.

## 5. Benchmark oficial de capacidade (v7) — NÃO autoriza decisão

Experimento `cap13-aix-linux-oficial-v7` (`artifacts/benchmarks/`):
o relatório empacotado original exibia `WARN`, mas o recálculo pelas regras
atuais (`benchmark/decision.py`) produz **INCONCLUSIVE**:

- proveniência quebrada: `journey_set_sha256`, `dataset_sha256` e
  `application_version_sha256` idênticos; `think_time_profile.sha256`
  placeholder;
- cobertura de coleta incompleta: grupo `rede` ausente nos coletores;
- clock skew fora do gate (offset máximo > 100 s);
- paridade funcional `per_env` (não comprovada entre ambientes).

Contrato de regressão: `tests/benchmark/test_provenance_hashes.py`
(TestRegressaoV7). **O v7 não autoriza recomendação AIX × Linux.** Dados
brutos preservados em `artifacts/benchmarks/cap13-aix-linux-oficial-v7/`.

## 6. Evidências offline (locais, sem rede)

| Artefato | Versão | Conteúdo |
|---|---|---|
| `artifacts/adaptive-replay-readiness.json` | 0.9.9 | status GO, suíte 2008 passed, shadow 64 capturas/100.157 ações no MIG24 com `false_safe_decisions=0` |
| `artifacts/adaptive-shadow-evaluation.json` | 0.9.9 | avaliação offline local: 3 capturas, 162 ações, equivalência de bytes, 0 violações de checkpoint |
| `artifacts/adaptive-replay-benchmark.json` | 0.9.9 | benchmark com ERP ideal (offline) |
| `artifacts/adaptive-replay-server-evidence.json` | 0.9.9 | evidência de servidor da campanha 0.9.9 |

## 7. Lacunas declaradas

1. Os JSONs de `dev/tmp/` que sustentam a tabela da §3 **não acompanham o
   pacote de release** — item 5 do plano da 0.9.11 trata da exportação
   verificável.
2. Não existe benchmark oficial de capacidade reproduzível executado com
   contrato válido — o v7 é INCONCLUSIVE (§5). Item 4 do plano define o
   contrato.
3. As iterações v1/m/v2/v3 são registros de desenvolvimento; apenas v4
   (0.9.9) foi verificado diretamente nos bancos dos servidores em
   2026-09-17.
