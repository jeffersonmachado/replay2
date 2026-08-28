import test from 'node:test';
import assert from 'node:assert/strict';

import {
  calcDelay,
  shouldBatch,
  TICK_BUDGET_MS,
  MIN_DELAY_MS,
  MAX_DELAY_MS,
  MAX_EVENTS_PER_TICK,
} from './playback_pacing.js';

const ev = (ts_ms) => ({ ts_ms });

test('calcDelay: 1x preserva o tempo real entre eventos', () => {
  assert.equal(calcDelay(ev(1000), ev(3000), 1), 2000);
});

test('calcDelay: velocidade divide o delay (2x = metade)', () => {
  assert.equal(calcDelay(ev(1000), ev(3000), 2), 1000);
});

test('calcDelay: piso acompanha a velocidade — 4x NÃO fica preso em 50ms', () => {
  // Regressão: piso fixo de 50ms limitava o player a ~20 eventos/s em
  // qualquer velocidade; o slider parecia não funcionar.
  const d = calcDelay(ev(1000), ev(1057), 4);
  assert.ok(d < MIN_DELAY_MS, `delay ${d}ms deveria ser < ${MIN_DELAY_MS}ms em 4x`);
  assert.equal(d, 57 / 4);
});

test('calcDelay: piso em 1x continua 50ms para eventos colados', () => {
  assert.equal(calcDelay(ev(1000), ev(1000), 1), MIN_DELAY_MS);
  assert.equal(calcDelay(ev(1000), ev(1010), 1), MIN_DELAY_MS);
});

test('calcDelay: teto de 5s para pausas longas', () => {
  assert.equal(calcDelay(ev(0), ev(60000), 1), MAX_DELAY_MS);
});

test('calcDelay: sem timestamps usa o piso como base', () => {
  assert.equal(calcDelay({}, {}, 1), MIN_DELAY_MS);
});

test('shouldBatch: delays menores que o frame entram no mesmo tick', () => {
  assert.equal(shouldBatch(TICK_BUDGET_MS - 1), true);
  assert.equal(shouldBatch(TICK_BUDGET_MS), false);
  assert.equal(shouldBatch(250), false);
});

test('playback em lote: captura de rajada deixa de levar minutos a 4x', () => {
  // Simula o modelo do player: aplica em lote os eventos com delay < frame e
  // agenda timeout só nos limites de lote. 6426 eventos a cada ~57ms.
  const events = [];
  let ts = 0;
  for (let i = 0; i < 6426; i++) { events.push(ev(ts)); ts += 57; }
  for (const speed of [1, 4]) {
    let i = 0;
    let totalMs = 0;
    while (i < events.length - 1) {
      let applied = 0;
      let delay = 0;
      do {
        delay = calcDelay(events[i], events[i + 1], speed);
        i++;
        applied++;
      } while (i < events.length - 1 && shouldBatch(delay) && applied < MAX_EVENTS_PER_TICK);
      totalMs += delay;
    }
    // Sem o lote, 6426 eventos custavam >= 321s (6426 x 50ms) em qualquer
    // velocidade. Com o lote, a rajada flui em poucos segundos.
    assert.ok(totalMs < 30000, `playback a ${speed}x levou ${totalMs}ms`);
  }
});
