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

test('renderRunDeparaHtml prefere display_name e mantém entidade como contexto', () => {
  const payload = {
    screens: [{
      entity: 'arq',
      display_name: '3.6.1 Pedido E-Commerce',
      operation: 'read',
      fields: [{ field: 'frete', original: '1', synthetic: '2', kept: false }],
    }],
  };
  const html = renderRunDeparaHtml(payload);
  assert.match(html, /Tela 3\.6\.1 Pedido E-Commerce \(read\)/);
  assert.match(html, /entidade arq/);
});

test('renderRunDeparaHtml lista dados digitados mantidos sem substituição', () => {
  const payload = {
    screens: [{
      entity: 'arq',
      display_name: '3.6.1 Pedido E-Commerce',
      operation: 'read',
      fields: [{ field: 'frete', original: '1', synthetic: '2', kept: false }],
      preserved: [
        { field: 'ecommerc', original: '4', note: 'campo fora da KB — original mantido', method: 'kept_layout_field' },
        { field: '', original: 'i', note: 'sem match confiável — original mantido', method: 'unmapped' },
      ],
    }],
  };
  const html = renderRunDeparaHtml(payload);
  assert.match(html, /ecommerc/);
  assert.match(html, /mantido — campo fora da KB/);
  assert.match(html, /1 de 1 campo\(s\) substituídos · 2 dado\(s\) digitado\(s\) mantido\(s\)/);
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

test('renderRunDeparaHtml marca origem formulário × grade com tabela', () => {
  const payload = {
    screens: [{
      entity: 'arq',
      display_name: '3.6.1 Pedido E-Commerce',
      operation: 'read',
      fields: [
        { field: 'frete', original: '1', synthetic: '2', kept: false, origin: 'formulario', grid_source: '' },
        { field: 'modelo', original: 'g2511', synthetic: 'c6182', kept: false, origin: 'grade', grid_source: 'est361' },
      ],
      preserved: [
        { field: 'parcelas', original: '2', note: 'campo fora da KB — original mantido', method: 'kept_layout_field', origin: 'grade', grid_source: 'est366' },
      ],
    }],
  };
  const html = renderRunDeparaHtml(payload);
  assert.match(html, />formulário</);
  assert.match(html, />grade · est361</);
  assert.match(html, />grade · est366</);
});

test('renderRunDeparaHtml grade sem tabela identificada mostra só grade', () => {
  const payload = {
    screens: [{
      entity: 'arq',
      operation: 'read',
      fields: [
        { field: 'qtd', original: '1', synthetic: '3', kept: false, origin: 'grade', grid_source: '' },
      ],
    }],
  };
  const html = renderRunDeparaHtml(payload);
  assert.match(html, />grade</);
  assert.ok(!html.includes('grade ·'));
});
