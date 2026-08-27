/**
 * failure_table_row.test.mjs — linha da tabela da aba Falhas (/runs/failures):
 * falha estruturada com link para a run de origem, badge de severidade e
 * status da run vindo do join no payload global.
 * Run: node --test gateway/control/static/js/components/failure_table_row.test.mjs
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { failureTableRow } from './run_views.js';

const BASE = {
  id: 900,
  run_id: 27,
  ts_ms: 1755996000000,
  session_id: '67b6c3b1-7876-4a5e-8e87-66d4e8558285',
  seq_global: 519,
  seq_session: 12,
  failure_type: 'screen_divergence',
  severity: 'medium',
  run_status: 'success',
};

test('failureTableRow renderiza link para a run com o status ao lado', () => {
  const html = failureTableRow(BASE);
  assert.match(html, /href="\/runs\/27"/);
  assert.match(html, />#27<\/a>/);
  assert.match(html, /success/);
});

test('failureTableRow exibe tipo, badge de severidade e seq global', () => {
  const html = failureTableRow(BASE);
  assert.match(html, /screen_divergence/);
  assert.match(html, /medium/);
  assert.match(html, /bg-yellow-900\/30/);
  assert.match(html, />519</);
});

test('failureTableRow trunca o session id mantendo o completo no title', () => {
  const html = failureTableRow(BASE);
  assert.match(html, /67b6c3b1…/);
  assert.match(html, /title="67b6c3b1-7876-4a5e-8e87-66d4e8558285"/);
});

test('failureTableRow cai para seq_session quando seq_global está ausente', () => {
  const html = failureTableRow({ ...BASE, seq_global: null });
  assert.match(html, />12</);
});

test('failureTableRow tolera severidade desconhecida com tom neutro de info', () => {
  const html = failureTableRow({ ...BASE, severity: 'weird' });
  assert.match(html, /weird/);
  assert.match(html, /bg-sky-900\/30/);
});

test('failureTableRow escapa valores da falha (XSS)', () => {
  const html = failureTableRow({ ...BASE, failure_type: '<script>alert(1)</script>', session_id: 'x<y' });
  assert.doesNotMatch(html, /<script>alert/);
  assert.match(html, /&lt;script&gt;/);
});

test('failureTableRow sem run_status não mostra status ao lado do link', () => {
  const html = failureTableRow({ ...BASE, run_status: null });
  assert.doesNotMatch(html, />#27<\/a><span/);
});
