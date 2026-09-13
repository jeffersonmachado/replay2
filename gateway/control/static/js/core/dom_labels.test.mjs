/**
 * dom_labels.test.mjs — rótulos leigos (pt-BR) para tipos de falha e
 * severidade. O usuário final é leigo: a UI deve mostrar "tela diferente
 * do esperado" em vez de "screen_divergence", mantendo o código técnico
 * acessível (title/parênteses) para suporte.
 * Run: node --test gateway/control/static/js/core/dom_labels.test.mjs
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { failureTypeLabel, severityLabel } from './dom.js';
import { failureTableRow } from '../components/run_views.js';

test('failureTypeLabel traduz a taxonomia conhecida para pt-BR leigo', () => {
  assert.equal(failureTypeLabel('timeout'), 'tempo esgotado');
  assert.equal(failureTypeLabel('screen_divergence'), 'tela diferente do esperado');
  assert.equal(failureTypeLabel('synthetic_data_swap'), 'troca de dados esperada');
  assert.equal(failureTypeLabel('navigation_error'), 'erro de navegação');
  assert.equal(failureTypeLabel('concurrency_error'), 'erro de concorrência');
  assert.equal(failureTypeLabel('checkpoint_mismatch'), 'ponto de verificação divergente');
  assert.equal(failureTypeLabel('technical_error'), 'erro técnico');
  assert.equal(failureTypeLabel('integrity_error'), 'erro de integridade');
  assert.equal(failureTypeLabel('functional'), 'falha funcional');
  assert.equal(failureTypeLabel('cancelled'), 'cancelada');
});

test('failureTypeLabel é case-insensitive e preserva desconhecidos crus', () => {
  assert.equal(failureTypeLabel('TIMEOUT'), 'tempo esgotado');
  assert.equal(failureTypeLabel('tipo_novo_2027'), 'tipo_novo_2027');
  assert.equal(failureTypeLabel(''), '—');
  assert.equal(failureTypeLabel(null), '—');
});

test('severityLabel traduz severidades', () => {
  assert.equal(severityLabel('low'), 'baixa');
  assert.equal(severityLabel('medium'), 'média');
  assert.equal(severityLabel('high'), 'alta');
  assert.equal(severityLabel('critical'), 'crítica');
  assert.equal(severityLabel('info'), 'informativa');
  assert.equal(severityLabel('weird'), 'weird');
});

test('failureTableRow mostra rótulo leigo e mantém código técnico no title', () => {
  const html = failureTableRow({
    id: 1, run_id: 27, ts_ms: 1755996000000,
    session_id: '67b6c3b1-7876-4a5e-8e87-66d4e8558285',
    seq_global: 519, failure_type: 'screen_divergence',
    severity: 'medium', run_status: 'success',
  });
  assert.match(html, /tela diferente do esperado/);
  assert.match(html, /title="screen_divergence"/);
  assert.match(html, />média</);
  assert.match(html, /title="medium"/);
});

test('failureTableRow escapa o rótulo de tipos desconhecidos (XSS)', () => {
  const html = failureTableRow({
    id: 1, run_id: 27, ts_ms: 1755996000000, session_id: 'x',
    seq_global: 1, failure_type: '<script>alert(1)</script>',
    severity: 'low', run_status: null,
  });
  assert.doesNotMatch(html, /<script>alert/);
  assert.match(html, /&lt;script&gt;/);
});
