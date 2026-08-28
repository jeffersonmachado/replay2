/**
 * run_synthetic_badge.test.mjs — rastreabilidade do replay sintético na UI
 * (badge "sintético • captura #N" a partir do params_json da run).
 * Run: node --test gateway/control/static/js/components/run_synthetic_badge.test.mjs
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { runSyntheticOrigin, runSyntheticBadgeHtml, runSyntheticSubstitutions } from './run_views.js';

const SYNTHETIC_PARAMS = JSON.stringify({
  input_mode: 'deterministic',
  synthetic: true,
  source_capture_id: 13,
  journey_id: 'capture-13-replay',
  term: 'xterm',
});

test('runSyntheticOrigin identifica run sintética e a captura de origem', () => {
  const origin = runSyntheticOrigin({ params_json: SYNTHETIC_PARAMS });
  assert.deepEqual(origin, { captureId: 13, journeyId: 'capture-13-replay' });
});

test('runSyntheticOrigin aceita params já parseado (objeto)', () => {
  const origin = runSyntheticOrigin({ params: { synthetic: true, source_capture_id: 7 } });
  assert.deepEqual(origin, { captureId: 7, journeyId: '' });
});

test('runSyntheticOrigin retorna null para run comum (sem flag synthetic)', () => {
  assert.equal(runSyntheticOrigin({ params_json: '{}' }), null);
  assert.equal(runSyntheticOrigin({ params_json: '{"synthetic": false}' }), null);
  assert.equal(runSyntheticOrigin({ params_json: '{"mode": "strict-global"}' }), null);
});

test('runSyntheticOrigin tolera params_json ausente ou inválido', () => {
  assert.equal(runSyntheticOrigin({}), null);
  assert.equal(runSyntheticOrigin({ params_json: '' }), null);
  assert.equal(runSyntheticOrigin({ params_json: 'não é json' }), null);
  assert.equal(runSyntheticOrigin(null), null);
});

test('runSyntheticOrigin sem source_capture_id válido devolve captureId null', () => {
  const origin = runSyntheticOrigin({ params_json: '{"synthetic": true}' });
  assert.deepEqual(origin, { captureId: null, journeyId: '' });
});

test('runSyntheticBadgeHtml renderiza badge com link para a captura', () => {
  const html = runSyntheticBadgeHtml({ id: 12, params_json: SYNTHETIC_PARAMS });
  assert.match(html, /sintético/);
  assert.match(html, /href="\/captures\/13"/);
  assert.match(html, /captura #13/);
});

test('runSyntheticBadgeHtml não renderiza nada para run comum', () => {
  assert.equal(runSyntheticBadgeHtml({ id: 1, params_json: '{}' }), '');
  assert.equal(runSyntheticBadgeHtml({ id: 1 }), '');
});

test('runSyntheticBadgeHtml sem captura conhecida indica "captura n/d" sem link', () => {
  const html = runSyntheticBadgeHtml({ params_json: '{"synthetic": true}' });
  assert.match(html, /sintético/);
  assert.match(html, /captura n\/d/);
  assert.doesNotMatch(html, /href=/);
});

test('runSyntheticSubstitutions lê os pares do params_json', () => {
  const run = { params_json: JSON.stringify({ synthetic: true, synthetic_substitutions: [['1', '2'], ['g2511', 'n9580']] }) };
  assert.deepEqual(runSyntheticSubstitutions(run), [['1', '2'], ['g2511', 'n9580']]);
});

test('runSyntheticSubstitutions aceita params parseado e tolera ausência', () => {
  assert.deepEqual(runSyntheticSubstitutions({ params: { synthetic_substitutions: [['4', '13']] } }), [['4', '13']]);
  assert.deepEqual(runSyntheticSubstitutions({ params_json: '{}' }), []);
  assert.deepEqual(runSyntheticSubstitutions({ params_json: 'não é json' }), []);
  assert.deepEqual(runSyntheticSubstitutions(null), []);
});
