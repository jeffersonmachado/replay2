/**
 * infinite_scroll_list.test.mjs — lazy loading em lotes da timeline de replay
 * Run: node --test gateway/control/static/js/components/infinite_scroll_list.test.mjs
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { LazyListController, attachInfiniteScroll, DEFAULT_BATCH_SIZE } from './infinite_scroll_list.js';

test('DEFAULT_BATCH_SIZE é 50', () => {
  assert.equal(DEFAULT_BATCH_SIZE, 50);
});

test('primeiro lote respeita batchSize e indica hasMore', () => {
  const ctl = new LazyListController({ batchSize: 10 });
  ctl.setItems(Array.from({ length: 25 }, (_, i) => i));
  const batch = ctl.nextBatch();
  assert.equal(batch.length, 10);
  assert.deepEqual(batch, [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]);
  assert.equal(ctl.renderedCount, 10);
  assert.equal(ctl.totalCount, 25);
  assert.ok(ctl.hasMore());
});

test('lotes avançam até esgotar; último lote é parcial', () => {
  const ctl = new LazyListController({ batchSize: 10 });
  ctl.setItems(Array.from({ length: 25 }, (_, i) => i));
  ctl.nextBatch();
  ctl.nextBatch();
  const last = ctl.nextBatch();
  assert.equal(last.length, 5);
  assert.equal(ctl.renderedCount, 25);
  assert.ok(!ctl.hasMore());
  assert.deepEqual(ctl.nextBatch(), []);
});

test('setItems reinicia o cursor', () => {
  const ctl = new LazyListController({ batchSize: 5 });
  ctl.setItems([1, 2, 3, 4, 5, 6, 7]);
  ctl.nextBatch();
  assert.equal(ctl.renderedCount, 5);
  ctl.setItems(['a', 'b']);
  assert.equal(ctl.renderedCount, 0);
  assert.deepEqual(ctl.nextBatch(), ['a', 'b']);
  assert.ok(!ctl.hasMore());
});

test('entrada não-array é tratada como lista vazia', () => {
  const ctl = new LazyListController({});
  ctl.setItems(null);
  assert.equal(ctl.totalCount, 0);
  assert.ok(!ctl.hasMore());
  assert.deepEqual(ctl.nextBatch(), []);
});

test('batchSize inválido cai para o default; fracionário é truncado', () => {
  assert.equal(new LazyListController({ batchSize: 0 }).batchSize, DEFAULT_BATCH_SIZE);
  assert.equal(new LazyListController({ batchSize: NaN }).batchSize, DEFAULT_BATCH_SIZE);
  assert.equal(new LazyListController({ batchSize: 7.9 }).batchSize, 7);
});

// ── attachInfiniteScroll com DOM fake (sem IntersectionObserver → botão) ──

function fakeElement() {
  return {
    children: [],
    html: '',
    adjacentHtml: '',
    textContent: '',
    className: '',
    type: '',
    removed: false,
    _listeners: {},
    appendChild(child) { this.children.push(child); },
    // No DOM real, "beforebegin" insere irmãos — não faz parte do innerHTML do sentinela
    insertAdjacentHTML(_pos, htmlStr) { this.adjacentHtml += htmlStr; },
    remove() { this.removed = true; },
    addEventListener(ev, fn) { this._listeners[ev] = fn; },
    click() { this._listeners.click && this._listeners.click(); },
    set innerHTML(value) { this.html = value; if (value === '') this.children = []; },
    get innerHTML() { return this.html; },
  };
}

test('attachInfiniteScroll renderiza em lotes via botão "Carregar mais"', () => {
  const container = fakeElement();
  globalThis.document = { createElement: () => fakeElement() };
  try {
    const lazy = attachInfiniteScroll({
      container,
      batchSize: 50,
      renderItem: (n) => `<div>${n}</div>`,
    });
    lazy.setItems(Array.from({ length: 120 }, (_, i) => i));

    // 1º lote: 50 itens antes do sentinela + botão "Carregar mais (50 de 120 eventos)"
    const sentinel = container.children[0];
    assert.ok(sentinel, 'sentinela deve existir');
    assert.equal((sentinel.adjacentHtml.match(/<div>/g) || []).length, 50);
    let btn = sentinel.children[0];
    assert.ok(btn, 'fallback sem IntersectionObserver deve criar botão');
    assert.match(btn.textContent, /Carregar mais eventos \(50 de 120 eventos\)/);

    btn.click();
    assert.equal((sentinel.adjacentHtml.match(/<div>/g) || []).length, 100);
    btn = sentinel.children[0];
    assert.match(btn.textContent, /100 de 120/);

    btn.click();
    assert.equal((sentinel.adjacentHtml.match(/<div>/g) || []).length, 120);
    assert.ok(sentinel.removed, 'sentinela é removida ao esgotar os itens');
  } finally {
    delete globalThis.document;
  }
});

test('attachInfiniteScroll.setItems limpa o container antes de renderizar', () => {
  const container = fakeElement();
  container.innerHTML = '<div>Carregando eventos...</div>';
  globalThis.document = { createElement: () => fakeElement() };
  try {
    const lazy = attachInfiniteScroll({ container, batchSize: 2, renderItem: (n) => `<i>${n}</i>` });
    lazy.setItems([1, 2, 3]);
    assert.equal(container.html, '');
    lazy.destroy();
  } finally {
    delete globalThis.document;
  }
});

test('attachInfiniteScroll exige container e renderItem', () => {
  assert.throws(() => attachInfiniteScroll({}), /obrigatórios/);
  assert.throws(() => attachInfiniteScroll({ container: {}, renderItem: null }), /obrigatórios/);
});
