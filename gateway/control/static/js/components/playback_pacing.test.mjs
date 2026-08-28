import test from 'node:test';
import assert from 'node:assert/strict';

import {
  calcDelay,
  planPlaybackTick,
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

test('planPlaybackTick: gap longo isolado aplica 1 evento e espera o gap', () => {
  const events = [ev(0), ev(6200), ev(6210)];
  const plan = planPlaybackTick(events, 0, 1);
  assert.equal(plan.applied, 1);
  assert.equal(plan.nextIndex, 1);
  assert.equal(plan.waitMs, 5000); // teto
  assert.equal(plan.firstEvent, events[0]);
  assert.equal(plan.nextEvent, events[1]);
});

test('planPlaybackTick: rajada sem delta de tempo entra num tick só', () => {
  const events = [ev(100), ev(100), ev(100), ev(100)];
  const plan = planPlaybackTick(events, 0, 1);
  assert.equal(plan.applied, 4);
  assert.equal(plan.nextIndex, 4);
  assert.equal(plan.nextEvent, null);
  assert.equal(plan.waitMs, MIN_DELAY_MS); // span 0 → piso uma vez só
});

test('planPlaybackTick: janela de 1 evento aplica o evento (regressão do stall)', () => {
  // Streaming: a primeira janela pode chegar com 1 só evento. O tick não
  // pode voltar com applied=0 — senão o player gira em busy-loop de 50ms
  // sem avançar (stall observado na captura 59 a 1x).
  const plan = planPlaybackTick([ev(1000)], 0, 1);
  assert.equal(plan.applied, 1);
  assert.equal(plan.nextIndex, 1);
  assert.equal(plan.nextEvent, null);
  assert.equal(plan.waitMs, MIN_DELAY_MS);
});

test('planPlaybackTick: último evento da lista é aplicado e encerra', () => {
  const events = [ev(0), ev(6200), ev(6210)];
  // Consome o primeiro, depois o tick no índice 1 aplica os 2 restantes.
  let plan = planPlaybackTick(events, 0, 1);
  assert.equal(plan.nextIndex, 1);
  plan = planPlaybackTick(events, plan.nextIndex, 1);
  assert.equal(plan.applied, 2);
  assert.equal(plan.nextIndex, 3);
  assert.equal(plan.nextEvent, null);
});

test('planPlaybackTick: respeita o teto de eventos por tick', () => {
  const events = Array.from({ length: MAX_EVENTS_PER_TICK + 50 }, () => ev(0));
  const plan = planPlaybackTick(events, 0, 1);
  assert.ok(plan.applied <= MAX_EVENTS_PER_TICK);
});

test('playback em lote: preserva o tempo real em 1x e acelera de verdade em 4x', () => {
  // 6426 eventos a cada ~57ms — rajada como a da captura 59.
  const events = [];
  let ts = 0;
  for (let i = 0; i < 6426; i++) { events.push(ev(ts)); ts += 57; }
  const span = events[events.length - 1].ts_ms - events[0].ts_ms;

  function totalTime(speed) {
    let i = 0;
    let totalMs = 0;
    while (i < events.length - 1) {
      const plan = planPlaybackTick(events, i, speed);
      totalMs += plan.waitMs;
      i = plan.nextIndex;
    }
    return totalMs;
  }

  // 1x continua tempo real (±2% pelos pisos); 4x é 4x mais rápido de verdade.
  const t1 = totalTime(1);
  const t4 = totalTime(4);
  assert.ok(Math.abs(t1 - span) / span < 0.02, `1x=${t1}ms deveria ≈ ${span}ms (tempo real)`);
  assert.ok(Math.abs(t4 - span / 4) / (span / 4) < 0.02, `4x=${t4}ms deveria ≈ ${span / 4}ms`);

  // Modelo antigo (piso fixo de 50ms, 1 evento por tick): 4x mal acelerava.
  const oldMs = (events.length - 1) * MIN_DELAY_MS; // 4x caía no mesmo piso
  assert.ok(t4 * 3 < oldMs, `4x=${t4}ms deveria ser muito menor que o piso antigo (${oldMs}ms)`);
});
