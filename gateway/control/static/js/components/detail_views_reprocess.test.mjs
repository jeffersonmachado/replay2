// Testes do card de reprocessamento por falha (detalhe da run).
import test from 'node:test';
import assert from 'node:assert/strict';

import { reprocessFailureCard } from './detail_views.js';

const FAILURE = {
  id: 321,
  failure_type: 'screen_divergence',
  session_id: '67b6c3b1-7876-4a5e-8e87-66d4e8558285',
  seq_global: 519,
};

test('reprocessFailureCard rotula os dois escopos de forma clara', () => {
  const html = reprocessFailureCard(FAILURE);
  assert.match(html, /A partir desta falha/);
  assert.match(html, /Só esta sessão/);
  // escopos enviados ao backend não mudam
  assert.match(html, /data-scope="from-failure"/);
  assert.match(html, /data-scope="session-from-failure"/);
  assert.match(html, /data-reprocess="321"/);
});

test('reprocessFailureCard explica o que cada botão faz (title)', () => {
  const html = reprocessFailureCard(FAILURE);
  assert.match(html, /title="[^"]*a partir do ponto desta falha \(todas as sessões\)/);
  assert.match(html, /title="[^"]*apenas a sessão desta falha/);
  // ambos deixam claro que a run original é preservada e a nova entra na fila
  const titles = html.match(/title="[^"]*preservada[^"]*fila[^"]*"/g) || [];
  assert.equal(titles.length, 2);
});

test('reprocessFailureCard mostra sessão e seq da falha', () => {
  const html = reprocessFailureCard(FAILURE);
  assert.match(html, /sessão 67b6c3b1-7876-4a5e-8e87-66d4e8558285 • seq 519/);
  assert.match(html, /screen_divergence/);
});

test('reprocessFailureCard escapa conteúdo vindo do banco', () => {
  const html = reprocessFailureCard({ ...FAILURE, failure_type: '<img src=x onerror=alert(1)>', session_id: '<b>' });
  assert.ok(!html.includes('<img src=x'));
  assert.match(html, /&lt;img src=x/);
  assert.match(html, /sessão &lt;b&gt;/);
});
