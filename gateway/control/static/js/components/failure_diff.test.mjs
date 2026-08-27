// Testes do diff de telas das falhas de replay (player inline da run).
import test from 'node:test';
import assert from 'node:assert/strict';

import {
  diffScreenLines,
  failureHasScreenDiff,
  renderFailureInlinePlayerHtml,
  renderScreenDiffHtml,
  screenDiffStats,
  substitutionEchoLineIndices,
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

const FAILURE = {
  failure_type: 'screen_divergence',
  severity: 'medium',
  session_id: 'abc',
  seq_global: 519,
  ts_ms: 1787850000000,
  message: 'checkpoint não estabilizou',
  evidence: { expected_screen: 'tela A', observed_screen: 'tela B' },
};

test('renderFailureInlinePlayerHtml mostra telas lado a lado com navegação', () => {
  const html = renderFailureInlinePlayerHtml(FAILURE, 3, 226);
  assert.match(html, /id="fp_prev"/);
  assert.match(html, /id="fp_play"/);
  assert.match(html, /id="fp_next"/);
  assert.match(html, /falha 3 de 226 · seq 519/);
  assert.match(html, /Tela esperada \(captura\)/);
  assert.match(html, /Tela observada \(run\)/);
  assert.match(html, /tela A/);
  assert.match(html, /tela B/);
  assert.match(html, /checkpoint não estabilizou/);
});

test('renderFailureInlinePlayerHtml desabilita navegação nas pontas', () => {
  const first = renderFailureInlinePlayerHtml(FAILURE, 1, 226);
  assert.match(first, /id="fp_prev"[^>]*disabled/);
  assert.ok(!/id="fp_next"[^>]*disabled/.test(first));
  const last = renderFailureInlinePlayerHtml(FAILURE, 226, 226);
  assert.match(last, /id="fp_next"[^>]*disabled/);
  assert.ok(!/id="fp_prev"[^>]*disabled/.test(last));
});

test('renderFailureInlinePlayerHtml cai para sigs em runs antigas sem telas', () => {
  const failure = {
    failure_type: 'screen_divergence',
    session_id: 'abc',
    seq_global: 519,
    message: 'timeout',
    expected_value: 'sha256:aaaa1111bbbb2222cccc3333dddd',
    observed_value: 'sha256:eeee4444ffff5555gggg6666hhhh',
    evidence: {},
  };
  const html = renderFailureInlinePlayerHtml(failure, 2, 5);
  assert.match(html, /não gravou o conteúdo das telas/);
  assert.match(html, /sha256:aaaa1…3333dddd/);
  assert.match(html, /falha 2 de 5/);
});

test('renderFailureInlinePlayerHtml escapa conteúdo das telas', () => {
  const failure = {
    failure_type: 'screen_divergence',
    session_id: 'abc',
    seq_global: 1,
    message: 'x',
    evidence: { expected_screen: '<script>alert(1)</script>', observed_screen: 'ok' },
  };
  const html = renderFailureInlinePlayerHtml(failure, 1, 1);
  assert.ok(!html.includes('<script>alert(1)</script>'));
  assert.match(html, /&lt;script&gt;/);
});

test('substitutionEchoLineIndices detecta eco de pares longos', () => {
  const subs = [['00109829069', '77912345601'], ['104529,05', '77,10']];
  const exp = 'PEDIDO 00109829069\nfrete 104529,05\nok';
  const obs = 'PEDIDO 77912345601\nfrete 77,10\nok';
  assert.deepEqual(substitutionEchoLineIndices(exp, obs, subs), [0, 1]);
});

test('substitutionEchoLineIndices detecta eco de par curto via diff de caracteres', () => {
  const subs = [['1', '2']];
  const exp = 'situacao 1 fim';
  const obs = 'situacao 2 fim';
  assert.deepEqual(substitutionEchoLineIndices(exp, obs, subs), [0]);
});

test('substitutionEchoLineIndices sem eco retorna vazio', () => {
  const subs = [['00109829069', '77912345601']];
  const exp = 'PEDIDO 00109829069\nSEDEX';
  const obs = 'PEDIDO 00109829069\nPAC';
  assert.deepEqual(substitutionEchoLineIndices(exp, obs, subs), []);
  assert.deepEqual(substitutionEchoLineIndices(exp, obs, []), []);
  assert.deepEqual(substitutionEchoLineIndices(exp, obs, null), []);
});

test('renderScreenDiffHtml marca linhas de troca em âmbar e mostra a legenda', () => {
  const subs = [['104529,05', '77,10']];
  const html = renderScreenDiffHtml('frete 104529,05\nSEDEX', 'frete 77,10\nPAC', subs);
  assert.match(html, /bg-amber-950\/60/);
  assert.match(html, /bg-rose-950\/60/);
  assert.match(html, /âmbar/);
});
