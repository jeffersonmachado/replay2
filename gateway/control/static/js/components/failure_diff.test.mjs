// Testes do diff de telas das falhas de replay (modal "o que diverge").
import test from 'node:test';
import assert from 'node:assert/strict';

import {
  diffScreenLines,
  failureHasScreenDiff,
  renderFailureDivergenceHtml,
  renderScreenDiffHtml,
  screenDiffStats,
} from './failure_diff.js';

test('diffScreenLines alinha por índice e marca linhas divergentes', () => {
  const rows = diffScreenLines('aaa\nbbb\nccc', 'aaa\nxxx\nccc');
  assert.equal(rows.length, 3);
  assert.equal(rows[0].changed, false);
  assert.equal(rows[1].changed, true);
  assert.equal(rows[1].expected, 'bbb');
  assert.equal(rows[1].observed, 'xxx');
  assert.equal(rows[2].changed, false);
});

test('diffScreenLines trata telas com tamanhos diferentes', () => {
  const rows = diffScreenLines('aaa\nbbb', 'aaa');
  assert.equal(rows.length, 2);
  assert.equal(rows[1].observed, null);
  assert.equal(rows[1].changed, true);
});

test('diffScreenLines aceita entradas vazias/nulas', () => {
  const rows = diffScreenLines('', '');
  assert.equal(rows.length, 1);
  assert.equal(rows[0].changed, false);
  assert.equal(diffScreenLines(null, undefined)[0].changed, false);
});

test('screenDiffStats conta divergências', () => {
  const stats = screenDiffStats(diffScreenLines('a\nb\nc', 'a\nx\ny'));
  assert.deepEqual(stats, { total: 3, changed: 2 });
});

test('failureHasScreenDiff exige alguma tela gravada no evidence', () => {
  assert.equal(failureHasScreenDiff(null), false);
  assert.equal(failureHasScreenDiff({}), false);
  assert.equal(failureHasScreenDiff({ evidence: {} }), false);
  assert.equal(failureHasScreenDiff({ evidence: { expected_screen: 'x' } }), true);
  assert.equal(failureHasScreenDiff({ evidence: { observed_screen: 'y' } }), true);
});

test('renderScreenDiffHtml destaca linhas divergentes e informa o resumo', () => {
  const html = renderScreenDiffHtml('PEDIDO 00109829069\nfrete 1', 'PEDIDO 00109829069\nfrete 104529,05');
  assert.match(html, /1 de 2 linha\(s\) divergem/);
  assert.match(html, /Tela esperada \(captura\)/);
  assert.match(html, /Tela observada \(run\)/);
  assert.match(html, /bg-rose-950\/60/);
  assert.match(html, /frete 104529,05/);
});

test('renderScreenDiffHtml escapa conteúdo das telas', () => {
  const html = renderScreenDiffHtml('<b>esperada</b>', '<i>observada</i>');
  assert.ok(!html.includes('<b>esperada</b>'));
  assert.match(html, /&lt;b&gt;esperada&lt;\/b&gt;/);
});

test('renderFailureDivergenceHtml usa telas quando gravadas', () => {
  const failure = {
    failure_type: 'screen_divergence',
    session_id: 'abc',
    seq_global: 519,
    message: 'checkpoint não estabilizou',
    evidence: { expected_screen: 'tela A', observed_screen: 'tela B' },
  };
  const html = renderFailureDivergenceHtml(failure);
  assert.match(html, /tela A/);
  assert.match(html, /tela B/);
  assert.match(html, /checkpoint não estabilizou/);
  assert.ok(!html.includes('não gravou o conteúdo'));
});

test('renderFailureDivergenceHtml cai para sigs em runs antigas', () => {
  const failure = {
    failure_type: 'screen_divergence',
    session_id: 'abc',
    seq_global: 519,
    message: 'timeout',
    expected_value: 'sha256:aaaa1111bbbb2222cccc3333dddd',
    observed_value: 'sha256:eeee4444ffff5555gggg6666hhhh',
    evidence: {},
  };
  const html = renderFailureDivergenceHtml(failure);
  assert.match(html, /não gravou o conteúdo das telas/);
  assert.match(html, /sha256:aaaa1…3333dddd/);
});
