/**
 * pages_lay_labels.test.mjs — as páginas JS devem rotular valores crus da
 * API com as funções leigas de core/dom.js em vez de exibir o código
 * técnico direto na tela (4ª leva de simplificação para o usuário leigo).
 * Run: node --test gateway/control/static/js/components/pages_lay_labels.test.mjs
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const PAGES = join(here, '..', 'pages');
const read = (name) => readFileSync(join(PAGES, name), 'utf8');
const captures = read('captures.js');
const dashboard = read('dashboard.js');
const runs = read('runs.js');
const runDetail = read('run_detail.js');
const benchmark = read('benchmark.js');
const gateway = read('gateway.js');
const admin = read('admin.js');
const catalogViews = readFileSync(join(here, 'catalog_views.js'), 'utf8');
const hostMetrics = readFileSync(join(here, 'host_metrics_panel.js'), 'utf8');
const sessionJs = readFileSync(join(here, '..', 'core', 'session.js'), 'utf8');
const statusbar = readFileSync(join(here, '..', '..', '..', 'templates', 'partials', 'statusbar.html'), 'utf8');

test('captures.js: status da captura usa rótulo leigo, não o código cru', () => {
  assert.match(captures, /captureStatusLabel\(/);
  assert.doesNotMatch(captures, />\$\{status \|\| "-"\}<\/span>/);
});

test('captures.js: política de execução do replay sintético usa rótulo leigo', () => {
  assert.match(captures, /executionPolicyLabel\(/);
});

test('captures.js: botões de sessão em português (sem "View"/"runs/new")', () => {
  assert.doesNotMatch(captures, />View</);
  assert.doesNotMatch(captures, />runs\/new</);
});

test('captures.js: contador de teclas verificadas sem abreviação "det:"', () => {
  assert.doesNotMatch(captures, />det: </);
});

test('dashboard.js: status da captura na lista recente usa rótulo leigo', () => {
  assert.match(dashboard, /captureStatusLabel\(/);
});

test('runs.js: filtro e chips de falhas usam rótulo leigo do tipo', () => {
  assert.match(runs, /failureTypeLabel\(/);
});

test('run_detail.js: tabela de falhas usa rótulos leigos de tipo e gravidade', () => {
  assert.match(runDetail, /failureTypeLabel\(/);
  assert.match(runDetail, /severityLabel\(/);
});

test('benchmark.js: status do experimento usa statusLabel', () => {
  assert.match(benchmark, /statusLabel\(/);
});

test('benchmark.js: papel "baseline" aparece como referência para o leigo', () => {
  assert.match(benchmark, /referência/);
});

test('gateway.js: monitor resume teclas verificadas em linguagem leiga', () => {
  assert.match(gateway, /teclas verificadas/);
  assert.doesNotMatch(gateway, /deterministic=\$/);
});

test('admin.js: perfil do usuário usa rótulo leigo, sem "role=" cru', () => {
  assert.match(admin, /roleLabel\(/);
  assert.doesNotMatch(admin, /role=\$/);
});

test('catalog_views.js: cenário usa termos leigos e português acentuado', () => {
  assert.match(catalogViews, /acesso flexível/);
  assert.match(catalogViews, /sem descrição/);
  assert.doesNotMatch(catalogViews, /runs=\$/);
});

test('host_metrics_panel.js: select de run traduz status e modo', () => {
  assert.match(hostMetrics, /statusLabel\(/);
  assert.match(hostMetrics, /modeLabel\(/);
});

test('statusbar: botão global em português leigo', () => {
  assert.match(statusbar, />Nova execução</);
  assert.doesNotMatch(statusbar, />Nova run</);
});

test('session.js: chip de usuário sem "usuario=/perfil=" crus', () => {
  assert.doesNotMatch(sessionJs, /usuario=\$\{/);
  assert.doesNotMatch(sessionJs, /perfil=\$\{/);
  assert.match(sessionJs, /roleLabel\(/);
  assert.match(sessionJs, /indisponível/);
});

test('run_views.js: status da run na linha de falha usa statusLabel', () => {
  const runViews = readFileSync(join(here, 'run_views.js'), 'utf8');
  assert.match(runViews, /statusLabel\(f\.run_status\)/);
});

test('detail_views.js: painel "falhas por tipo" usa rótulo leigo', () => {
  const detailViews = readFileSync(join(here, 'detail_views.js'), 'utf8');
  // failureTypeList deve rotular o código técnico (ex.: screen_divergence)
  const fn = detailViews.match(/export function failureTypeList[\s\S]*?\n}/);
  assert.ok(fn, 'failureTypeList encontrada');
  assert.match(fn[0], /failureTypeLabel\(/);
});

test('dashboard.js: resumo sem "compliance"/"runs" crus', () => {
  assert.match(dashboard, /Conformidade bloqueada/);
  assert.match(dashboard, /execuções carregadas/);
  assert.doesNotMatch(dashboard, /Compliance bloqueado/);
  assert.doesNotMatch(dashboard, /runs carregadas/);
});
