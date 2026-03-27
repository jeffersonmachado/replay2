Engine de Automação Screen-Oriented (Expect/Tcl)
================================================

Este projeto é uma engine nova, robusta e reutilizável para automação
de sistemas legados em modo texto (estilo Recital/Clipper), usando Expect/Tcl.

Principais ideias:
- Captura de tela determinística a partir do buffer completo
- Normalização de ANSI, Unicode e caracteres de box-drawing
- Geração de assinatura estável de tela (screen signature)
- Máquina de estados explícita, orientada a assinatura
- Handlers isolados por tela

Estrutura sugerida:

- `bin/main.exp`                 - Script principal (entrypoint Expect)
- `lib/capture.tcl`              - Captura de tela/buffer
- `lib/normalize.tcl`            - Normalização de texto, ANSI, box-drawing
- `lib/signature.tcl`            - Geração de assinatura de tela
- `lib/state_machine.tcl`        - Máquina de estados e despacho de handlers
- `screens/*.tcl`                - Seus handlers de tela (plugins)

Requisitos importantes já contemplados:
- Encoding definido como UTF-8 desde o início (`encoding system utf-8`)
- Captura centralizada via namespace `::capture`
- Normalização centralizada via namespace `::normalize`
- Assinatura de tela via namespace `::signature`
- Máquina de estados clara em `::state_machine`
- Handlers de tela nos arquivos em `screens/`

Para executar:

```bash
cd /home/jmachado/projetos/dakota/replay2
expect bin/main.exp
```

No exemplo, o "backend" legado é simulado; na vida real, você apontaria o
`spawn` para o binário/servidor Recital/Clipper real.

## Exemplo (demo)

O demo executável (simulador + telas de exemplo) fica em `examples/`:

```bash
expect examples/demo.exp
```

## Testes

Os testes usam `tcltest` (Tcl puro), para serem portáveis entre **Linux e AIX**.

Rodar:

```bash
tclsh tests/all.tcl
```

## Distribuição (Linux + AIX)

Este repositório inclui um empacotamento **portável** em `tar.gz`, com instalador POSIX.

### Gerar o tarball

```bash
sh ./scripts/build-tarball.sh
ls -la dist/
```

Isso gera um arquivo como `dist/dakota-replay2-0.1.0.tar.gz`.

### Instalar

Extraia o `tar.gz` e rode o instalador. Por padrão ele tenta instalar dependências (Expect/Tcl) automaticamente.

```bash
tar -xzf dist/dakota-replay2-0.1.0.tar.gz
cd dakota-replay2-0.1.0
sudo ./install.sh --prefix /opt/dakota-replay2
```

Se você não quiser que o instalador mexa em dependências:

```bash
./install.sh --prefix /tmp/dakota-replay2 --no-deps
```

### Executar após instalar

O instalador cria um wrapper em:
- `<prefixo>/bin/replay2`

E, quando roda como root e `/usr/local/bin` é gravável, cria também um symlink:
- `/usr/local/bin/replay2`

Teste:

```bash
/opt/dakota-replay2/bin/replay2
```

### Dependências (auto-instalação)

- **Linux**: tenta `apt-get`, `dnf`, `yum` ou `zypper` para instalar `expect` e `tcl`.
- **AIX**: tenta usar o `dnf` do AIX Toolbox (`/opt/freeware/bin/dnf`) para instalar `expect` e `tcl`.

Se o nome do pacote for diferente no seu ambiente, você pode sobrescrever:

```bash
DEPS_EXPECT=expect DEPS_TCL=tcl ./install.sh --prefix /opt/dakota-replay2
```

### Desinstalar

```bash
sudo /opt/dakota-replay2/uninstall.sh
```

## Gestão Web e APIs

Control Server (gerenciamento de replays):

```bash
python3 gateway/control/server.py \
	--listen 127.0.0.1:8090 \
	--cookie-secret-file /caminho/cookie.secret \
	--hmac-key-file /caminho/hmac.key \
	--bootstrap-admin admin:admin123
```

Dashboard (eventos em tempo real):

```bash
python3 dashboard/server.py --events-file /caminho/events.jsonl --listen 127.0.0.1:8080
```

Especificação OpenAPI disponível em:

- `gateway/control/openapi.yaml`

## Testes Web

Teste rápido de API:

```bash
python3 tests/quick-test-api.py
```

Teste de browser (Selenium):

```bash
python3 tests/test_web_ui_selenium.py
```

## Performance

Benchmark de captura/normalização/assinatura:

```bash
tclsh tests/benchmark.tcl
```

## Troubleshooting

- Erro `ModuleNotFoundError: dakota_gateway`: rode os comandos a partir da raiz do projeto.
- Erro de login `401` após autenticar: verifique relógio do host e validade do cookie.
- SQLite com lock: confirme uso de WAL e `busy_timeout` (já habilitados por padrão).
- Selenium falhando no CI: valide se `chromium/chromedriver` estão instalados no runner.

## CI/CD

Pipeline em `GitHub Actions`:

- Testes Tcl
- Testes Python de integridade
- Testes API
- Benchmark de performance
- Lint Python
- Coverage report
- Smoke test de UI com Selenium (best effort)

Arquivo:

- `.github/workflows/ci.yml`


