/**
 * production_no_terminal_parser.test.mjs — garante ausência de VT em produção
 * Run: node --test gateway/control/static/js/components/production_no_terminal_parser.test.mjs
 *
 * Aplica de fato os FORBIDDEN_PATTERNS em todos os diretórios de produção
 * (templates/, components/, pages/, core/). renderHtml/visualSig NÃO são
 * padrões proibidos: são API legítima de replay_snapshot_state.js (renderização
 * de snapshot canônico, não parser de terminal).
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.resolve(__dirname, '../../../../..');

const FORBIDDEN_PATTERNS = [
  { re: /require\(['"][^'"]*virtual_terminal\.cjs['"]\)/, label: "require('virtual_terminal.cjs')" },
  { re: /import\(['"][^'"]*virtual_terminal\.cjs['"]\)/, label: "import('virtual_terminal.cjs')" },
  { re: /\bwindow\.DakVT\b/, label: 'window.DakVT' },
  { re: /\bcreateVirtualTerminal\b/, label: 'createVirtualTerminal' },
  { re: /\bfeedBase64\b/, label: 'feedBase64' },
];

const PRODUCTION_DIRS = [
  'gateway/control/templates',
  'gateway/control/static/js/components',
  'gateway/control/static/js/pages',
  'gateway/control/static/js/core',
];

// Marcadores de comentário/documentação que mencionam que NÃO usam o VT
const ALLOWED_LINE_MARKERS = [
  'NÃO usa mais DakVT',
  'NÃO usando fallback DakVT',
];

function isAllowedLine(line) {
  const trimmed = line.trim();
  if (trimmed.startsWith('//') || trimmed.startsWith('*') || trimmed.startsWith('/*')) return true;
  return ALLOWED_LINE_MARKERS.some((marker) => trimmed.includes(marker));
}

function scanDir(dirPath) {
  const results = [];
  try {
    const entries = readdirSync(dirPath);
    for (const entry of entries) {
      const fullPath = path.join(dirPath, entry);
      const stat = statSync(fullPath);
      if (stat.isDirectory()) {
        results.push(...scanDir(fullPath));
      } else if (entry.endsWith('.html') || entry.endsWith('.js') || entry.endsWith('.cjs') || entry.endsWith('.mjs')) {
        // Skip test files
        if (entry.includes('.test.') || entry.includes('test.mjs')) continue;
        results.push(fullPath);
      }
    }
  } catch (_) {
    // directory might not exist
  }
  return results;
}

test('production files do not use terminal parser patterns', () => {
  const violations = [];
  for (const dir of PRODUCTION_DIRS) {
    const absDir = path.resolve(projectRoot, dir);
    for (const file of scanDir(absDir)) {
      const lines = readFileSync(file, 'utf8').split('\n');
      for (const { re, label } of FORBIDDEN_PATTERNS) {
        for (const line of lines) {
          if (re.test(line) && !isAllowedLine(line)) {
            violations.push(`${file}: forbidden ${label}: ${line.trim().substring(0, 80)}`);
          }
        }
      }
    }
  }
  assert.deepEqual(violations, [], violations.join('\n'));
});

test('production scan covers pages/ and core/ directories', () => {
  // Garante que os diretórios adicionados existem e têm arquivos escaneáveis
  for (const dir of ['gateway/control/static/js/pages', 'gateway/control/static/js/core']) {
    const files = scanDir(path.resolve(projectRoot, dir));
    assert.ok(files.length > 0, `${dir} deve conter arquivos de produção escaneáveis`);
  }
});

test('virtual_terminal.cjs does not exist in production static/js', () => {
  const prodPath = path.resolve(projectRoot, 'gateway/control/static/js/virtual_terminal.cjs');
  let exists = false;
  try {
    readFileSync(prodPath);
    exists = true;
  } catch (_) {
    // expected
  }
  assert.equal(exists, false, 'virtual_terminal.cjs must not exist in production');
});

test('virtual_terminal.cjs exists only in tests/oracles', () => {
  const oraclePath = path.resolve(projectRoot, 'tests/oracles/virtual_terminal.cjs');
  let exists = false;
  try {
    readFileSync(oraclePath);
    exists = true;
  } catch (_) {
    // unexpected
  }
  assert.equal(exists, true, 'oracle must exist in tests/oracles/');
});
