// Ritmo do playback da sessão capturada.
//
// O player aplica eventos em lote por tick: cabem no lote os eventos cuja
// soma dos deltas de tempo ajustados (raw/speed) fica dentro de um frame
// (TICK_BUDGET_MS). A espera do tick é o tempo total do lote (primeiro
// evento aplicado → próximo não aplicado), então o relógio do playback é
// preservado — 1x continua tempo real e 4x é 4x mais rápido DE VERDADE.
//
// Sem o lote, o piso fixo de 50ms por evento limitava o player a
// ~20 eventos/s em qualquer velocidade: em capturas com milhares de chunks
// de saída (ex.: captura 59, 6426 eventos) o slider parecia não funcionar
// (7min de playback a 4x) e a tela re-renderizava a cada evento.

export const TICK_BUDGET_MS = 100;
export const MIN_DELAY_MS = 50;
export const MAX_DELAY_MS = 5000;
// Teto de segurança de eventos por tick — evita travar a UI numa rajada
// gigante de eventos sem delta de tempo.
export const MAX_EVENTS_PER_TICK = 300;
// Modo "pular pausas": think-time do usuário (segundos entre teclas) vira
// uma pausa curta fixa. Sem ele, uma captura com ~1h de digitação real
// (ex.: captura 59, ~6s entre teclas) leva ~17min de playback mesmo a 4x.
export const SKIP_PAUSES_DELAY_MS = 150;

export function calcDelay(currentEvent, nextEvent, speed, maxDelayMs = MAX_DELAY_MS) {
  const currentTs = currentEvent && currentEvent.ts_ms ? currentEvent.ts_ms : 0;
  const nextTs = nextEvent && nextEvent.ts_ms ? nextEvent.ts_ms : 0;
  const s = speed || 1;
  let rawDelay = nextTs - currentTs;
  // Eventos sem timestamp ou colados recebem o piso como delay base
  if (!rawDelay || rawDelay <= 0) rawDelay = MIN_DELAY_MS;
  // speed=1 → tempo real; speed=2 → metade do tempo
  const adjusted = rawDelay / s;
  // O piso acompanha a velocidade: em 4x cai para ~12ms
  return Math.max(MIN_DELAY_MS / s, Math.min(adjusted, maxDelayMs));
}

// Planeja um tick de playback a partir de startIdx: aplica em lote os
// eventos que cabem num frame e calcula a espera até o próximo tick.
// Retorna { startIndex, nextIndex, applied, waitMs, firstEvent, nextEvent },
// onde nextIndex é o primeiro evento NÃO aplicado e nextEvent ele mesmo
// (null no fim da lista). maxDelayMs sobrescreve o teto de pausa (modo
// "pular pausas" passa SKIP_PAUSES_DELAY_MS para comprimir think-time).
export function planPlaybackTick(events, startIdx, speed, maxDelayMs = MAX_DELAY_MS) {
  const s = speed || 1;
  const firstEvent = events[startIdx] || null;
  let i = startIdx;
  let acc = 0;
  // Aplica SEMPRE ao menos um evento por tick. Sem isso, o último evento
  // da lista (ou uma janela de streaming com 1 só evento) nunca era
  // aplicado: o tick voltava com applied=0 e o player girava num busy-loop
  // de 50ms sem avançar — o "stall" observado ao iniciar o playback da
  // captura 59 a 1x (janela inicial do streaming chega com poucos eventos).
  while (i < events.length) {
    if (i > startIdx) {
      const raw = Math.max(0, (events[i].ts_ms || 0) - (events[i - 1].ts_ms || 0)) / s;
      if (acc + raw > TICK_BUDGET_MS) break;
      acc += raw;
    }
    i++;
    if (i - startIdx >= MAX_EVENTS_PER_TICK) break;
  }
  const nextEvent = i < events.length ? events[i] : null;
  const waitMs = nextEvent && firstEvent ? calcDelay(firstEvent, nextEvent, s, maxDelayMs) : MIN_DELAY_MS;
  return {
    startIndex: startIdx,
    nextIndex: i,
    applied: i - startIdx,
    waitMs,
    firstEvent,
    nextEvent,
  };
}
