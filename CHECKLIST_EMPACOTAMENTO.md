# Checklist de Empacotamento — Replay2

## Regras de Exclusão do Artefato

Os seguintes itens NUNCA devem ser incluídos no tarball de distribuição:

### Dados de Estado e Runtime

- [ ] `gateway/state/` — Estado local do gateway
- [ ] `gateway/state/captures/` — Screenshots/dumps de sessão
- [ ] `logs/` — Logs locais de desenvolvimento
- [ ] `log/` — Logs locais (alternativo)

### Credenciais e Segredos

- [ ] `.env` — Variáveis de ambiente com valores reais
- [ ] `.env.*` — Qualquer variante de .env
- [ ] `.token.env` — Tokens de acesso
- [ ] `*.pem` — Chaves PEM
- [ ] `*.key` — Chaves privadas
- [ ] `*.crt` — Certificados
- [ ] `*.pfx` — PKCS#12
- [ ] `*.ppk` — Chaves PuTTY
- [ ] `id_rsa*` — Chaves SSH
- [ ] `id_ed25519*` — Chaves SSH
- [ ] `id_ecdsa*` — Chaves SSH

### Bancos de Dados

- [ ] `*.db` — SQLite database
- [ ] `*.db-wal` — SQLite WAL
- [ ] `*.db-shm` — SQLite shared memory
- [ ] `*.sqlite` — SQLite database
- [ ] `*.sqlite3` — SQLite database

### Cache e Artefatos de Build

- [ ] `__pycache__/` — Python bytecode
- [ ] `*.pyc` — Python compiled
- [ ] `*.pyo` — Python optimized
- [ ] `.pytest_cache/` — Cache de testes
- [ ] `.venv/` — Virtualenv local
- [ ] `venv/` — Virtualenv local
- [ ] `node_modules/` — Dependências Node.js

### Outros

- [ ] `.git/` — Repositório Git
- [ ] `.DS_Store` — macOS
- [ ] `Thumbs.db` — Windows
- [ ] `*.tmp` — Arquivos temporários
- [ ] `*.swp` — Vim swap
- [ ] `*.swo` — Vim swap
- [ ] `dist/` — Builds anteriores

## Verificação Pós-Build

Após gerar o tarball, verificar:

```bash
# Listar conteúdo do tarball
tar tzf dist/dakota-replay2-*.tar.gz | sort

# Verificar itens proibidos
tar tzf dist/dakota-replay2-*.tar.gz | grep -E '\.(db|env|pem|key|crt|pfx)$$' && echo "FALHA: item proibido" || echo "OK"

# Verificar caches Python
tar tzf dist/dakota-replay2-*.tar.gz | grep '__pycache__' && echo "FALHA: cache Python" || echo "OK"
```

## Evidências Incluídas no Artefato

Além do código, o tarball carrega as evidências da cadeia de aceitação:

- [ ] `artifacts/final-acceptance-report.md` e `final-acceptance-results.json`
- [ ] `artifacts/source-tree-manifest.sha256` e `source-tree-hash.json`
- [ ] `artifacts/evidence-manifest.sha256` (regenerado no stage pelo gerador
  único `scripts/acceptance/gen-evidence-manifest.sh --root <stage>` — cobre
  EXATAMENTE o que foi empacotado; validado por `sha256sum -c`)
- [ ] `artifacts/acceptance-test-baseline.sha256` (hashes reais, sem linha vazia `e3b0c442...  -`)
- [ ] `artifacts/acceptance-logs/` — logs das fases de aceitação
- [ ] `artifacts/benchmarks/<experimento-oficial>/` — SOMENTE o experimento
  oficial de benchmark AIX×Linux (contrato imutável, runs com amostras de
  aplicação/host, agregados, relatório e manifesto de evidência do
  experimento). Seleção (FASE 11):
  - default: o mais recente com `experiment-manifest.json` válido;
  - `--with-benchmarks <id>`: experimento específico;
  - históricos (ex.: `cap13-*-v1..v6`) NUNCA entram automaticamente no
    pacote de runtime — ficam no repositório de evidências, fora do pacote.

Verificar a presença da evidência de benchmark (exatamente UM experimento):

```bash
tar tzf dist/dakota-replay2-*.tar.gz | grep 'artifacts/benchmarks/.*/experiment-manifest.json' \
  || echo "SEM evidência de benchmark no pacote"
tar tzf dist/dakota-replay2-*.tar.gz | grep -o 'artifacts/benchmarks/[^/]*' | sort -u
```

## Vinculação Aceite × Árvore × Pacote (FASE 2)

Desde a correção do incidente 0.8.85 (pacote com aceite de outra árvore):

1. `final-acceptance.sh` calcula o hash da árvore ANTES dos testes, recalcula
   IMEDIATAMENTE DEPOIS e **aborta o pipeline** se divergirem (com diff por
   arquivo no log);
2. `final-acceptance-results.json` grava `version` + hashes before/after;
3. `build-tarball.sh` (com `artifacts/` presente) exige results JSON da MESMA
   árvore e da MESMA VERSION, com a suíte completa aprovada — aceite antigo
   reaproveitado reprova o build;
4. após gerar o tarball, o build o extrai, hasheia a árvore extraída com o
   `tree_hash.py` do próprio pacote e exige igualdade com o aceite, além de
   sanity checks (VERSION, `gateway/control/server.py`, nenhum
   `*.key`/`*.db`/segredo/estado local).

## Reprodutibilidade do Tarball (FASE 11)

Com GNU tar + gzip (Linux), o build usa `--sort=name --owner=0 --group=0
--numeric-owner --mtime=@$SOURCE_DATE_EPOCH` e `gzip -n`: dois builds da
mesma árvore com `DAKOTA_TARBALL_TIMESTAMP` e `SOURCE_DATE_EPOCH` pinados
produzem o MESMO sha256 (coberto por
`tests/test_build_hardening_unit.py::test_two_builds_same_tree_same_sha256`).

```bash
DAKOTA_TARBALL_TIMESTAMP=fixo SOURCE_DATE_EPOCH=1700000000 bash scripts/build-tarball.sh
```

Compromisso AIX: o tar do AIX não suporta essas flags — o build detecta e cai
no caminho clássico (tar + gzip), SEM reprodutibilidade bitwise. O build de
release oficial é feito em Linux; o AIX só consome o `.run` gerado lá.

## Processo de Release

1. Atualizar `VERSION`
2. Executar testes: `npm run test`
3. Aceitação completa: `bash scripts/final-acceptance.sh` (se arquivos
   protegidos mudaram, regerar antes a baseline:
   `bash scripts/acceptance/regen-baseline.sh`)
4. Build: `bash scripts/build-tarball.sh` (ou já chamado pelo passo 3)
5. Verificar com checklist acima
6. Copiar para `remoto_dakota/artifacts/`
7. Testar instalação limpa em ambiente de homologação
8. Tag no Git: `git tag v$(cat VERSION)`
