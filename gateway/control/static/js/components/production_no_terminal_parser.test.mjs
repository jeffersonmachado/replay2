/**
 * production_no_terminal_parser.test.mjs — garante ausência de VT em produção
 * Run: node --test gateway/control/static/js/components/production_no_terminal_parser.test.mjs
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.resolve(__dirname, '../../../../..');

const FORBIDDEN_PATTERNS = [
  /require\(['"].*virtual_terminal\.cjs['"]\)/,
  /window\.DakVT\b/,
  /createVirtualTerminal\b/,
  /feedBase64\b/,
  /\.feed\(/,
  /renderHtml\b/,
  /screenSig\b/,
  /visualSig\b/,
];

const PRODUCTION_DIRS = [
  'gateway/control/templates',
  'gateway/control/static/js/components',
];

const ALLOWED_IN_PRODUCTION = [
  // Comentários/documentação que mencionam que NÃO usam
  'NÃO usa mais DakVT',
  'NÃO usando fallback DakVT',
];

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

test('production files do not reference virtual_terminal.cjs', () => {
  for (const dir of PRODUCTION_DIRS) {
    const absDir = path.resolve(projectRoot, dir);
    const files = scanDir(absDir);
    for (const file of files) {
      const content = readFileSync(file, 'utf8');
      // Check for require('virtual_terminal.cjs')
      const hasRequire = /require\(['"].*virtual_terminal\.cjs['"]\)/.test(content);
      if (hasRequire) {
        // Check if it's only in allowed comments
        const lines = content.split('\n');
        for (const line of lines) {
          if (/require\(['"].*virtual_terminal\.cjs['"]\)/.test(line)) {
            const isComment = line.trim().startsWith('//') || line.trim().startsWith('*');
            if (!isComment) {
              assert.fail(`${file}: forbidden require('virtual_terminal.cjs') in production`);
            }
          }
        }
      }
    }
  }
});

test('production files do not reference window.DakVT', () => {
  for (const dir of PRODUCTION_DIRS) {
    const absDir = path.resolve(projectRoot, dir);
    const files = scanDir(absDir);
    for (const file of files) {
      const content = readFileSync(file, 'utf8');
      if (/\bwindow\.DakVT\b/.test(content)) {
        // Check if it's only in allowed comments
        const lines = content.split('\n');
        for (const line of lines) {
          if (/\bwindow\.DakVT\b/.test(line)) {
            const trimmed = line.trim();
            const isCommentOrError = trimmed.startsWith('//') || trimmed.startsWith('*') || trimmed.includes('NÃO usa') || trimmed.includes('NÃO usando');
            if (!isCommentOrError) {
              assert.fail(`${file}: forbidden window.DakVT reference in production`);
            }
          }
        }
      }
    }
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
