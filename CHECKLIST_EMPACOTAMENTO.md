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

## Processo de Release

1. Atualizar `VERSION`
2. Executar testes: `npm run test`
3. Build: `bash scripts/build-tarball.sh`
4. Verificar com checklist acima
5. Copiar para `remoto_dakota/artifacts/`
6. Testar instalação limpa em ambiente de homologação
7. Tag no Git: `git tag v$(cat VERSION)`
