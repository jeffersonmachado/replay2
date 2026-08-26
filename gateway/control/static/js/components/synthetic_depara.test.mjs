// Testes do render do de→para dos dados sintéticos de uma run.
import test from 'node:test';
import assert from 'node:assert/strict';

import { renderRunDeparaHtml } from './synthetic_depara.js';

const PAYLOAD = {
  ok: true,
  source: 'manifest',
  journey_id: 'capture-13-replay',
  key_fields: ['cpf'],
  screens: [
    {
      entity: 'arq',
      operation: 'read',
      fields: [
        { field: 'cpf', original: '00109829069', synthetic: '00109829069', kept: true, note: 'chave de consulta' },
      ],
    },
    {
      entity: 'arq',
      operation: 'update',
      fields: [
        { field: 'frete', original: '1', synthetic: '104529,05', kept: false, note: '' },
        { field: 'situacao', original: '4', synthetic: '13', kept: false, note: '' },
      ],
    },
  ],
};

test('renderRunDeparaHtml lista telas, campos e resumo de substituições', () => {
  const html = renderRunDeparaHtml(PAYLOAD);
  assert.match(html, /Tela arq \(read\)/);
  assert.match(html, /Tela arq \(update\)/);
  assert.match(html, /frete/);
  assert.match(html, /104529,05/);
  assert.match(html, /2 de 3 campo\(s\) substituídos/);
});

test('renderRunDeparaHtml marca campo mantido como chave de consulta', () => {
  const html = renderRunDeparaHtml(PAYLOAD);
  assert.match(html, /mantido \(chave\)/);
  assert.match(html, /Campos mantidos \(chave de consulta\): cpf/);
});

test('renderRunDeparaHtml sem telas informa que usou dados originais', () => {
  const html = renderRunDeparaHtml({ ok: true, screens: [] });
  assert.match(html, /dados originais da captura/);
  assert.equal(renderRunDeparaHtml(null).includes('table'), false);
});

test('renderRunDeparaHtml mostra a jornada quando presente', () => {
  const html = renderRunDeparaHtml(PAYLOAD);
  assert.match(html, /Jornada: capture-13-replay/);
});

test('renderRunDeparaHtml escapa valores vindos do manifest', () => {
  const payload = {
    screens: [{
      entity: '<img src=x>',
      operation: 'read',
      fields: [{ field: 'f', original: '<b>1</b>', synthetic: '<i>2</i>', kept: false }],
    }],
  };
  const html = renderRunDeparaHtml(payload);
  assert.ok(!html.includes('<img src=x>'));
  assert.match(html, /&lt;b&gt;1&lt;\/b&gt;/);
  assert.match(html, /&lt;i&gt;2&lt;\/i&gt;/);
});
