/**
 * template_syntax.test.mjs — validates all templates have valid JavaScript
 * Run: node --test gateway/control/static/js/components/template_syntax.test.mjs
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, writeFileSync, unlinkSync, mkdtempSync, readdirSync, rmSync } from 'node:fs';
import { execSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { tmpdir } from 'node:os';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.resolve(__dirname, '../../../../..');

function extractScriptBlocks(filePath) {
  const content = readFileSync(filePath, 'utf8');
  const scripts = [];
  const regex = /<script[^>]*>(.*?)<\/script>/gs;
  let match;
  while ((match = regex.exec(content)) !== null) {
    const code = match[1].trim();
    if (!code || code.startsWith('import(') || code.startsWith('import ')) continue;
    scripts.push(code);
  }
  return scripts;
}

test('capture_session_replay.html has valid JavaScript syntax', () => {
  const templatePath = path.resolve(projectRoot, 'gateway/control/templates/capture_session_replay.html');
  const scripts = extractScriptBlocks(templatePath);
  assert.ok(scripts.length > 0, 'template must have script blocks');

  const tmpDir = mkdtempSync(path.join(tmpdir(), 'template-syntax-'));
  try {
    for (let i = 0; i < scripts.length; i++) {
      const tmpFile = path.join(tmpDir, `script_${i}.js`);
      writeFileSync(tmpFile, scripts[i], 'utf8');
      try {
        execSync(`node --check "${tmpFile}"`, { stdio: 'pipe' });
      } catch (err) {
        assert.fail(`Script ${i} has syntax error: ${err.stderr?.toString() || err.message}`);
      }
    }
  } finally {
    // cleanup
    for (let i = 0; i < scripts.length; i++) {
      try { unlinkSync(path.join(tmpDir, `script_${i}.js`)); } catch (_) {}
    }
    try { rmSync(tmpDir, { recursive: true }); } catch (_) {}
  }
});

test('all HTML templates in gateway have valid JavaScript', () => {
  const templatesDir = path.resolve(projectRoot, 'gateway/control/templates');
  let foundAny = false;

  try {
    const files = readdirSync(templatesDir);
    for (const file of files) {
      if (!file.endsWith('.html')) continue;
      const filePath = path.join(templatesDir, file);
      const scripts = extractScriptBlocks(filePath);
      if (scripts.length === 0) continue;
      foundAny = true;

      const tmpDir = mkdtempSync(path.join(tmpdir(), 'tmpl-syntax-'));
      try {
        for (let i = 0; i < scripts.length; i++) {
          const tmpFile = path.join(tmpDir, `s_${i}.js`);
          writeFileSync(tmpFile, scripts[i], 'utf8');
          try {
            execSync(`node --check "${tmpFile}"`, { stdio: 'pipe' });
          } catch (err) {
            assert.fail(`${file} script ${i}: syntax error`);
          }
        }
      } finally {
        for (let i = 0; i < scripts.length; i++) {
          try { unlinkSync(path.join(tmpDir, `s_${i}.js`)); } catch (_) {}
        }
        try { rmSync(tmpDir, { recursive: true }); } catch (_) {}
      }
    }
  } catch (err) {
    if (err.code === 'ENOENT') {
      return;
    }
    throw err;
  }

  if (!foundAny) {
    return;
  }
});
