/**
 * skip_fields_select.test.mjs — helpers puros do multi-select "Manter originais"
 * Run: node --test gateway/control/static/js/components/skip_fields_select.test.mjs
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  buildSkipFieldsModel,
  summarizeSelection,
  parseStoredSelection,
  resolveInitialSelection,
} from './skip_fields_select.js';

const SCREENS = [
  {
    entity: 'arq',
    operation: 'seek',
    screen_title: 'CADASTRO DE CLIENTES',
    fields: [
      { field: 'cpf', original: '03392117906', method: 'by_cursor_position', key: true },
      { field: 'nome', original: 'JOSE MARIA', method: 'by_cursor_position' },
    ],
  },
  {
    entity: 'ped',
    operation: 'insert',
    screen_title: '',
    fields: [
      { field: 'frete', original: '1', method: 'by_cursor_position' },
      { field: 'situacao', original: 'P', method: 'by_cursor_position' },
    ],
  },
];

test('buildSkipFieldsModel agrupa por tela e marca chaves como checked', () => {
  const model = buildSkipFieldsModel(SCREENS, ['cpf'], []);
  assert.equal(model.length, 2);
  assert.equal(model[0].label, 'CADASTRO DE CLIENTES');
  const cpf = model[0].fields.find((f) => f.field === 'cpf');
  assert.equal(cpf.key, true);
  assert.equal(cpf.checked, true);
  const nome = model[0].fields.find((f) => f.field === 'nome');
  assert.equal(nome.key, false);
  assert.equal(nome.checked, false);
});

test('buildSkipFieldsModel usa flag key do payload e fallback de label', () => {
  const screens = [{ entity: 'arq', operation: 'seek', fields: [{ field: 'cpf', original: '1', key: true }] }];
  const model = buildSkipFieldsModel(screens, [], []);
  assert.equal(model[0].label, 'arq · seek');
  assert.equal(model[0].fields[0].key, true);
});

test('buildSkipFieldsModel aplica seleção persistida e case-insensitive', () => {
  const model = buildSkipFieldsModel(SCREENS, ['cpf'], ['FRETE']);
  const frete = model[1].fields.find((f) => f.field === 'frete');
  assert.equal(frete.checked, true);
  const situacao = model[1].fields.find((f) => f.field === 'situacao');
  assert.equal(situacao.checked, false);
});

test('buildSkipFieldsModel ignora telas sem campos e campos sem nome', () => {
  const model = buildSkipFieldsModel([
    { entity: 'x', fields: [] },
    { entity: 'y', fields: [{ field: '', original: '?' }] },
  ], [], []);
  assert.deepEqual(model, []);
});

test('summarizeSelection resume exceções e chaves automáticas', () => {
  const model = buildSkipFieldsModel(SCREENS, ['cpf'], ['frete']);
  assert.equal(summarizeSelection(model), '1 campo(s) mantido(s) · 1 chave(s) automática(s)');
});

test('summarizeSelection sem exceções e sem campos', () => {
  const model = buildSkipFieldsModel(SCREENS, ['cpf'], []);
  assert.equal(summarizeSelection(model), '1 chave(s) automática(s)');
  assert.equal(summarizeSelection([]), 'Nenhum campo mapeado na trilha');
});

test('buildSkipFieldsModel colapsa título multilinha (amostra crua da tela)', () => {
  const raw = ' DAKOTA S/A                    ESTOQUE\n  REDE DE LOJAS  | 3.6.1 PEDIDO E-COMMERCE\n*Pedido.....:D00011073  E-c: 0  Emissao..:27/07/26 Incluir Modificar Excluir';
  const model = buildSkipFieldsModel([
    { entity: 'arq', operation: 'read', screen_title: raw, fields: [{ field: 'cpf', original: '1' }] },
  ], [], []);
  assert.ok(!model[0].label.includes('\n'));
  assert.ok(model[0].label.length <= 60);
  assert.ok(model[0].label.startsWith('DAKOTA S/A ESTOQUE'));
});

test('parseStoredSelection tolera lixo e filtra vazios', () => {
  assert.deepEqual(parseStoredSelection('["cpf", " frete ", ""]'), ['cpf', 'frete']);
  assert.deepEqual(parseStoredSelection('não-json'), []);
  assert.deepEqual(parseStoredSelection('{"a":1}'), []);
  assert.deepEqual(parseStoredSelection(''), []);
});

test('resolveInitialSelection prefere a seleção do servidor', () => {
  // Servidor manda a lista (mesmo vazia) → ela vence o localStorage.
  assert.deepEqual(resolveInitialSelection(['cpf'], ['frete']), ['cpf']);
  assert.deepEqual(resolveInitialSelection([], ['frete']), []);
  // Payload antigo/sem a chave → fallback para o localStorage.
  assert.deepEqual(resolveInitialSelection(undefined, ['frete']), ['frete']);
  assert.deepEqual(resolveInitialSelection(null, ['frete']), ['frete']);
  assert.deepEqual(resolveInitialSelection(undefined, []), []);
});
