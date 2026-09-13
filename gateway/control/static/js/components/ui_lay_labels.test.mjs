/**
 * ui_lay_labels.test.mjs — textos leigos nos templates de captura/replay.
 * Regressão da 2ª leva de simplificação: o usuário leigo não deve precisar
 * conhecer "determinístico", "trilha bruta" ou "batching" para operar.
 * Run: node --test gateway/control/static/js/components/ui_lay_labels.test.mjs
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const TEMPLATES = join(here, '..', '..', '..', 'templates');
const captures = readFileSync(join(TEMPLATES, 'captures.html'), 'utf8');
const sessionReplay = readFileSync(join(TEMPLATES, 'capture_session_replay.html'), 'utf8');
const runs = readFileSync(join(TEMPLATES, 'runs.html'), 'utf8');

test('captures.html: painel de dados sintéticos explica em linguagem leiga', () => {
  assert.match(captures, /refazer a jornada no sistema novo/);
  // O campo Nº de sessões alimenta o "Gerar"; o 1-clique executa 1 sessão —
  // o hint precisa dizer isso para não induzir expectativa errada.
  assert.match(captures, /Replay sintético executa 1 sessão/);
});

test('captures.html: hint de execução sem jargão de implementação', () => {
  assert.doesNotMatch(captures, /batching conservador de digitação contígua/);
  assert.match(captures, /acelera trechos seguros de digitação/);
  assert.match(captures, /sem pular nenhuma verificação/);
});

test('session replay: banner sintético em linguagem leiga', () => {
  assert.match(sessionReplay, /refeita com dados de teste a partir da captura original/);
});

test('session replay: botão De→para tem tooltip explicando o que é', () => {
  assert.match(sessionReplay, /id="depara-open-btn"[^>]*title="/);
  assert.match(sessionReplay, /campo a campo, o valor original/);
});

test('session replay: playback explicado como vídeo, sem "trilha bruta"', () => {
  assert.doesNotMatch(sessionReplay, /revisar a trilha bruta capturada/);
  assert.match(sessionReplay, /como um vídeo/);
  assert.match(sessionReplay, /não executa nada no sistema/);
});

test('session replay: filtro "Determinístico" vira "Teclas verificadas"', () => {
  assert.match(sessionReplay, /id="events-filter-det"[^>]*>[^<]*Teclas verificadas/);
  assert.match(sessionReplay, /id="events-filter-det"[^>]*title="[^"]*conferiu a tela/);
});

test('session replay: badge DET carrega explicação no title', () => {
  assert.match(sessionReplay, /title="Tecla com verificação de tela/);
});

test('runs.html: filtro de gravidade mostra rótulos leigos, valores da API preservados', () => {
  assert.match(runs, /<option value="critical">crítica<\/option>/);
  assert.match(runs, /<option value="high">alta<\/option>/);
  assert.match(runs, /<option value="medium">média<\/option>/);
  assert.match(runs, /<option value="low">baixa<\/option>/);
  assert.match(runs, /<option value="info">informativa<\/option>/);
});
