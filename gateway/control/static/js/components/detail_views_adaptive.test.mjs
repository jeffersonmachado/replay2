/**
 * detail_views_adaptive.test.mjs — cartão de telemetria do motor adaptativo
 * no detalhe da run (metrics_json["adaptive"]: política, overhead do Replay2,
 * batching, shadow mode). Run:
 * node --test gateway/control/static/js/components/detail_views_adaptive.test.mjs
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { adaptiveMetricsCard, runAdaptiveMetrics } from './detail_views.js';

const METRICS = JSON.stringify({
  adaptive: {
    execution_policy: 'adaptive',
    sessions: 3,
    journey_total_ms: 4000,
    erp_response_ms: 2500,
    replay_overhead_ms: 1500,
    replay_overhead_ratio: 0.375,
    pacing_ms: 0,
    sync_wait_ms: 120,
    explicit_wait_ms: 50,
    checkpoint_wait_ms: 2500,
    batch_count: 12,
    batched_action_count: 216,
    barrier_count: 9,
    conservative_fallback_count: 2,
    batching_saved_ms: 980,
  },
});

test('runAdaptiveMetrics lê o bloco adaptive do metrics_json', () => {
  const adaptive = runAdaptiveMetrics({ metrics_json: METRICS });
  assert.equal(adaptive.execution_policy, 'adaptive');
  assert.equal(adaptive.batch_count, 12);
});

test('runAdaptiveMetrics aceita metrics já parseado (objeto)', () => {
  const adaptive = runAdaptiveMetrics({ metrics: { adaptive: { execution_policy: 'conservative' } } });
  assert.equal(adaptive.execution_policy, 'conservative');
});

test('runAdaptiveMetrics tolera metrics_json ausente/inválido/sem adaptive', () => {
  assert.equal(runAdaptiveMetrics({}), null);
  assert.equal(runAdaptiveMetrics({ metrics_json: '' }), null);
  assert.equal(runAdaptiveMetrics({ metrics_json: 'não é json' }), null);
  assert.equal(runAdaptiveMetrics({ metrics_json: '{"checkpoints_ok": 3}' }), null);
  assert.equal(runAdaptiveMetrics(null), null);
});

test('adaptiveMetricsCard renderiza política, overhead e batching', () => {
  const html = adaptiveMetricsCard({ metrics_json: METRICS });
  assert.match(html, /adaptativa/i);
  assert.match(html, /overhead Replay2/i);
  assert.match(html, /37,5%/);
  assert.match(html, /batches/);
  assert.match(html, /980/);          // batching_saved_ms
  assert.match(html, /fallbacks conservadores/i);
});

test('adaptiveMetricsCard destaca bloco shadow quando presente', () => {
  const metrics = JSON.parse(METRICS);
  metrics.adaptive.execution_policy = 'adaptive_shadow';
  metrics.adaptive.shadow = {
    total_actions: 50, batch_candidates: 7, safe_candidates: 5,
    false_safe_decisions: 0, potential_saved_ms: 320,
  };
  const html = adaptiveMetricsCard({ metrics_json: JSON.stringify(metrics) });
  assert.match(html, /shadow/i);
  assert.match(html, /false[- ]safe/i);
  assert.match(html, /320/);
});

test('adaptiveMetricsCard não renderiza nada sem bloco adaptive', () => {
  assert.equal(adaptiveMetricsCard({ metrics_json: '{}' }), '');
  assert.equal(adaptiveMetricsCard({}), '');
});
