// Ritmo do playback da sessão capturada.
//
// O delay entre dois eventos vem do delta de ts_ms dividido pela velocidade,
// com piso e teto sensíveis à velocidade. O piso NÃO pode ser fixo: com 50ms
// por evento o player fica limitado a ~20 eventos/s em qualquer velocidade —
// em capturas com milhares de chunks de saída o slider parecia não funcionar
// (ex.: 6426 eventos levavam 7min mesmo a 4x).
//
// Os eventos cuja espera ajustada cabe num frame (< TICK_BUDGET_MS) são
// aplicados em lote no mesmo tick pelo chamador (shouldBatch), que renderiza
// a tela uma única vez por tick.

export const TICK_BUDGET_MS = 100;
export const MIN_DELAY_MS = 50;
export const MAX_DELAY_MS = 5000;
// Teto de segurança de eventos por tick — evita travar a UI numa rajada
// gigante de eventos sem delta de tempo.
export const MAX_EVENTS_PER_TICK = 300;

export function calcDelay(currentEvent, nextEvent, speed) {
  const currentTs = currentEvent && currentEvent.ts_ms ? currentEvent.ts_ms : 0;
  const nextTs = nextEvent && nextEvent.ts_ms ? nextEvent.ts_ms : 0;
  const s = speed || 1;
  let rawDelay = nextTs - currentTs;
  // Eventos sem timestamp ou colados recebem o piso como delay base
  if (!rawDelay || rawDelay <= 0) rawDelay = MIN_DELAY_MS;
  // speed=1 → tempo real; speed=2 → metade do tempo
  const adjusted = rawDelay / s;
  // O piso acompanha a velocidade: em 4x cai para ~12ms
  return Math.max(MIN_DELAY_MS / s, Math.min(adjusted, MAX_DELAY_MS));
}

// true = o próximo evento entra no mesmo tick (rajada); false = o chamador
// agenda setTimeout com o delay retornado por calcDelay.
export function shouldBatch(delayMs) {
  return delayMs < TICK_BUDGET_MS;
}
