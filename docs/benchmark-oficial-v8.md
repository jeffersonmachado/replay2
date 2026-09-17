# Benchmark Oficial v8 — AIX × Linux (cap13) — Runbook do Operador

**Status:** preparado na v0.9.11 (2026-09-17), pendente de execução.
**Público:** operador sem conhecimento do código. Siga as seções em ordem.

Este documento define o experimento oficial v8, que substitui o v7
(marcado **INCONCLUSIVE**). Lições do v7 incorporadas aqui:

1. **Proveniência calculada, nunca digitada** — no v7 os hashes
   `journey_set_sha256`, `dataset_sha256` e `application_version_sha256`
   eram idênticos e o `think_time_profile.sha256` era placeholder
   (`"def1277b"` + zeros). No v8 os quatro hashes são **calculados** dos
   artefatos pelo próprio `benchmark create` (flags `--journey-set`,
   `--dataset`, `--application`, `--think-time-profile`) e registrados em
   `provenance.json` dentro do diretório do experimento.
2. **Cobertura completa de coletores** — no v7 o grupo **rede** estava
   ausente nos dois ambientes. No v8 a coleta de rede é obrigatória
   (amostras `net_rx_kbs`/`net_tx_kbs` ou janela `net_window` por run).
3. **Clock dentro do gate** — no v7 o AIX estava ~171 s atrasado (gate:
   1000 ms). Antes de executar, sincronize os relógios (NTP) dos dois
   servidores e da estação orquestradora.

Regra de ouro: **qualquer evidência essencial ausente → INCONCLUSIVE, sem
recomendação de plataforma/capacidade.** Não existe "PASS por insistência":
repita a coleta até a evidência existir.

---

## 1. Contrato único do experimento (imutável)

| Campo | Valor v8 |
|---|---|
| `experiment_id` | `cap13-aix-linux-oficial-v8` |
| `schema_version` | `1.0` |
| Jornada | captura 13 / sessão 51 — `dev/benchmark-env/journey-cap13.jsonl` (546 eventos, 121 inputs) |
| Dataset (massa) | `dev/benchmark-env/dataset-cap13-seed42.jsonl` (gerado na seção 2), seed **42** |
| Aplicação | espelho local dos programas Recital sob teste (seção 2) |
| `seed` | `42` |
| `terminal_geometry` | `80x24` |
| Política de execução | replay pareado determinístico via SSH (`SSHReplayAdapter`), mesma jornada nos dois ambientes, ordem de fases intercalada registrada em `order_history` |
| Critério funcional (PORTA 1) | equivalência por **texto de tela** (ground truth da captura, máscaras voláteis); qualquer divergência → FAIL, mesmo se o alvo for mais rápido |
| `concurrency_levels` | `[1, 5, 10, 20]` (escada, nessa ordem) |
| Fases | warmup **30 s** / medição **120 s** / cooldown **30 s**; `iterations` = **2** |
| Think time | `deterministic`, perfil `dev/benchmark-env/think-time-profile.json` (deltas reais da captura, cap 5000 ms) |
| Limites de parada (`stop_conditions`) | erro ≥ **5 %**, p99 ≥ **5000 ms**, CPU host ≥ **95 %** sustentada por **3** amostras consecutivas, crescimento de swap ≥ **512 MB** |
| Sonda de recuperação | `recovery_probe_seconds` = **60** (medição real pós-carga) |
| Ambientes | `aix-power` (10.5.8.25, AIX 7.3 POWER8) × `linux-x86` (10.5.8.24, Xeon Gold 5318Y) — baseline = `aix-power` |
| Gate de clock skew | 1000 ms (orquestrador × host, medido na coleta) |

Depois de criado, o contrato é imutável: qualquer mudança de configuração
exige um NOVO `experiment_id` (ex.: `...-v8b`).

---

## 2. Preparação (estação local, offline — NADA roda nos servidores ainda)

Todos os comandos a partir da raiz do projeto
(`/home/jmachado/projetos/dakota/replay2`).

### 2.1 Artefato da massa (dataset, seed 42)

A massa do v8 é a sequência exata de dados digitados na captura 13
(sessão 51), fixada pela seed 42 do contrato. Materialize-a como artefato
auditável próprio (NUNCA reutilize o arquivo da jornada como dataset — no
v7 hashes iguais sem justificativa derrubaram o experimento):

```bash
python3 - <<'EOF'
import json
ent = "dev/benchmark-env/journey-cap13.jsonl"
sai = "dev/benchmark-env/dataset-cap13-seed42.jsonl"
with open(ent, encoding="utf-8") as fh, open(sai, "w", encoding="utf-8") as out:
    for linha in fh:
        ev = json.loads(linha)
        if ev.get("type") == "deterministic_input":
            out.write(json.dumps({
                "seed": 42,
                "seq_global": ev.get("seq_global"),
                "key_b64": ev.get("key_b64"),
            }, sort_keys=True) + "\n")
print("dataset materializado:", sai)
EOF
```

### 2.2 Espelho da aplicação sob teste

```bash
mkdir -p dev/benchmark-env/app-recital24
rsync -a --delete ferblo@10.5.8.24:/dakota11/prg/ dev/benchmark-env/app-recital24/
```

(O espelho é do servidor Linux; a paridade funcional com o AIX é
comprovada pela PORTA 1 durante a execução.)

### 2.3 Contrato JSON

Crie `dev/benchmark-env/contract-oficial-v8.json` **sem nenhum campo
`*_sha256`** (eles são calculados no passo 2.4):

```json
{
  "experiment_id": "cap13-aix-linux-oficial-v8",
  "seed": 42,
  "terminal_geometry": "80x24",
  "concurrency_levels": [1, 5, 10, 20],
  "warmup_seconds": 30,
  "measurement_seconds": 120,
  "cooldown_seconds": 30,
  "iterations": 2,
  "think_time_profile": {"type": "deterministic", "params": {}},
  "stop_conditions": {
    "error_rate_pct": 5,
    "p99_limit_ms": 5000,
    "host_cpu_pct": 95,
    "swap_growth_mb": 512,
    "host_cpu_sustained_samples": 3
  },
  "recovery_probe_seconds": 60,
  "environments": ["aix-power", "linux-x86"]
}
```

### 2.4 Criação com hashes calculados (caminho oficial)

```bash
./gateway/dakota-gateway benchmark create \
  --contract dev/benchmark-env/contract-oficial-v8.json \
  --journey-set dev/benchmark-env/journey-cap13.jsonl \
  --dataset dev/benchmark-env/dataset-cap13-seed42.jsonl \
  --application dev/benchmark-env/app-recital24 \
  --think-time-profile dev/benchmark-env/think-time-profile.json
```

O comando:

- calcula o SHA-256 real de cada artefato (arquivo → conteúdo; diretório →
  hash de conjunto, mesmo esquema do `evidence-manifest.sha256`);
- **recusa** a criação se um hash digitado no JSON divergir do calculado,
  ou se algum hash for placeholder (não-hex, `unknown`, ≥ 32 zeros);
- grava `artifacts/benchmarks/cap13-aix-linux-oficial-v8/` com
  `experiment-manifest.json` + `provenance.json` (fonte e tipo de cada
  hash — evidência auditável).

Sem os flags `--journey-set/--dataset/--application/--think-time-profile`
o `create` continua aceitando as strings no JSON (fluxo legado), mas
placeholders são recusados e hashes vazios viram INCONCLUSIVE na decisão.
**Não use o fluxo legado no experimento oficial.**

Verifique:

```bash
cat artifacts/benchmarks/cap13-aix-linux-oficial-v8/provenance.json
# os 4 hashes devem ser DISTINTOS entre si
```

---

## 3. Pré-execução nos servidores (obrigatório)

1. **Relógio sincronizado** nos dois servidores e na estação
   (`chronyc tracking` / `ntpq -p`; divergência > 1 s reprova o gate de
   clock skew). No v7 o AIX estava 171 s atrasado.
2. **Coletor de rede ativo** nos dois hosts (o sampler de host_metrics deve
   reportar `net_rx_kbs`/`net_tx_kbs`; confira o painel
   `/observability/resources` de cada control plane).
3. **Sem carga estranha**: nenhum outro replay/run/backup nos hosts durante
   a janela; janitor de órfãos (`orphan_reap_cmd`) já configurado nos
   modelos de ambiente.
4. Modelos de ambiente prontos:
   `dev/benchmark-env/environment-aix.json` e
   `dev/benchmark-env/environment-linux.json` (credenciais por referência
   `file:`~`/.ssh/id_ed25519` — nunca segredo em texto claro).

Preflight (na estação, valida acessibilidade SSH dos dois ambientes):

```bash
./gateway/dakota-gateway benchmark preflight \
  --experiment-id cap13-aix-linux-oficial-v8 \
  --env dev/benchmark-env/environment-aix.json \
  --env dev/benchmark-env/environment-linux.json
# deve responder "ok": true nos DOIS ambientes
```

---

## 4. Execução

Um único comando orquestra os dois ambientes (escada pareada, ordem de
fases intercalada e registrada). Janela estimada: ~25–35 min por iteração.

```bash
./gateway/dakota-gateway benchmark run \
  --experiment-id cap13-aix-linux-oficial-v8 \
  --env dev/benchmark-env/environment-aix.json \
  --env dev/benchmark-env/environment-linux.json \
  --journeys dev/benchmark-env/journey-cap13.jsonl
```

Se a escada parar por `stop_condition`, **não reinicie por cima**: a parada
é um achado de capacidade e o veredito máximo será WARN. Investigue a
classificação no `report.md` antes de repetir.

---

## 5. Coleta e relatório

```bash
# veredito e razões (recalcula comparação + decisão dos artefatos)
./gateway/dakota-gateway benchmark report \
  --experiment-id cap13-aix-linux-oficial-v8 \
  --env dev/benchmark-env/environment-aix.json \
  --env dev/benchmark-env/environment-linux.json

# status persistido no banco
./gateway/dakota-gateway benchmark status \
  --experiment-id cap13-aix-linux-oficial-v8
```

Artefatos em `artifacts/benchmarks/cap13-aix-linux-oficial-v8/`:
`experiment-manifest.json`, `provenance.json`, `execution-result.json`,
`report.md`, `report.json`, `aggregates/`, `runs/<env>-iterN-concN/`
(application-samples, host-samples, functional-diffs, logs) e
`evidence-manifest.sha256` (hash de todos os arquivos).

Nos servidores, nada é gravado fora das sessões de replay e da tabela
`host_metrics` do control plane local — não há etapa de coleta manual.

---

## 6. Checklist de evidências essenciais (antes de aceitar o veredito)

Marque cada item no `report.json` / `report.md`:

- [ ] **Proveniência**: `provenance.json` presente, 4 hashes de 64 hex
      distintos; `provenance_problems` vazio no `report.json`.
- [ ] **Cobertura de coletores** (seção "Cobertura por coletor" do
      `report.md`): grupos **cpu, memoria, paginacao, disco, rede,
      run_queue** `coberto` nos DOIS ambientes (`grupos_ausentes` vazio).
- [ ] **Clock skew**: medido (`measured: true`) e `max_abs_offset_ms` ≤
      1000 em ambos os ambientes.
- [ ] **Paridade funcional**: `functional_validation.status` =
      `comprovada`; cobertura de checkpoints 100 % (ou exceções
      auditadas); zero divergências, zero erros/timeouts adicionais no
      alvo.
- [ ] **Amostras completas**: todas as runs COMPLETED com amostras de
      MEASUREMENT; 2 iterações × 4 níveis × 2 ambientes.
- [ ] **Sonda de recuperação**: `recovery_seconds` medido (não `null`)
      nos níveis executados.
- [ ] **Integridade**: `evidence-manifest.sha256` gerado por último e
      cobrindo `execution-result.json` com o veredito final.

Faltando qualquer item, o veredito correto é **INCONCLUSIVE** — corrija a
coleta e repita; não edite artefatos à mão.

---

## 7. O que reprova o experimento (resumo dos gates)

| Evidência ausente | Veredito |
|---|---|
| Hash de proveniência placeholder/ausente/igual sem justificativa | INCONCLUSIVE |
| Grupo essencial de coletor ausente (ex.: rede) ou parcial | INCONCLUSIVE (gargalo não declarável) |
| Clock skew não medido (amostras válidas sem offset) | INCONCLUSIVE |
| Clock skew medido acima de 1000 ms | no máximo WARN |
| Nenhuma comparação de tela no alvo / cobertura < 100 % sem exceção | INCONCLUSIVE |
| Divergência funcional (visual, erro/timeout adicional) | **FAIL** |
| Escada interrompida por stop_condition | no máximo WARN (parada classificada pela evidência) |
| IC95 fora de 10 % da média / normalização incompleta | no máximo WARN |
