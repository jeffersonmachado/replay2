/**
 * host_metrics_panel.test.mjs — helpers puros do painel de recursos do host
 * Run: node --test gateway/control/static/js/components/host_metrics_panel.test.mjs
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { rebaseSamples, computeStats, buildPolylinePoints, EXPORT_FORMAT } from './host_metrics_panel.js';

test('EXPORT_FORMAT é o contrato do arquivo de comparação', () => {
  assert.equal(EXPORT_FORMAT, 'dakota-host-metrics/v1');
});

test('rebaseSamples converte ts_ms para segundos relativos ao primeiro ponto', () => {
  const out = rebaseSamples([
    { ts_ms: 10_000, cpu_pct: 10 },
    { ts_ms: 12_500, cpu_pct: 20 },
    { ts_ms: 15_000, cpu_pct: 30 },
  ]);
  assert.deepEqual(out.map((s) => s.rel_s), [0, 2.5, 5]);
  assert.deepEqual(rebaseSamples([]), []);
});

test('computeStats calcula média/máx ignorando null', () => {
  const stats = computeStats(
    [{ cpu_pct: 10, load1: null }, { cpu_pct: 30, load1: null }, { cpu_pct: 20, load1: null }],
    ['cpu_pct', 'load1'],
  );
  assert.deepEqual(stats.cpu_pct, { avg: 20, max: 30 });
  assert.deepEqual(stats.load1, { avg: null, max: null });
});

test('buildPolylinePoints escala x pelo tempo e y pelo vmax', () => {
  const samples = rebaseSamples([
    { ts_ms: 0, cpu_pct: 0 },
    { ts_ms: 5000, cpu_pct: 50 },
    { ts_ms: 10_000, cpu_pct: 100 },
  ]);
  const points = buildPolylinePoints(samples, 'cpu_pct', 100, 600, 160, 8)
    .split(' ')
    .map((p) => p.split(',').map(Number));
  assert.equal(points.length, 3);
  // primeiro ponto: x no pad, y no fundo (0%)
  assert.deepEqual(points[0], [8, 160 - 8]);
  // último ponto: x no limite direito, y no topo (100%)
  assert.deepEqual(points[2], [600 - 8, 8]);
  // ponto médio: x central, y central
  assert.ok(Math.abs(points[1][0] - 300) < 1);
  assert.ok(Math.abs(points[1][1] - 80) < 1);
});

test('buildPolylinePoints ignora amostras sem o campo e tolera vmax 0', () => {
  const samples = rebaseSamples([
    { ts_ms: 0, cpu_pct: null },
    { ts_ms: 1000, cpu_pct: 42 },
  ]);
  const points = buildPolylinePoints(samples, 'cpu_pct', 0);
  assert.equal(points.split(' ').length, 1);
  assert.equal(buildPolylinePoints([], 'cpu_pct', 100), '');
});
