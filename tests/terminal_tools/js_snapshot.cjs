#!/usr/bin/env node
'use strict';

const fs = require('node:fs');
const path = require('node:path');
const vt = require('../../gateway/control/static/js/virtual_terminal.cjs');

function normalizeSnapshot(snapshot) {
  return {
    version: snapshot.version,
    engine_version: snapshot.engine_version,
    rows: snapshot.rows,
    cols: snapshot.cols,
    term: snapshot.term,
    encoding: snapshot.encoding,
    cursor: snapshot.cursor,
    saved_cursor: snapshot.saved_cursor,
    attributes: snapshot.attributes,
    g0_charset: snapshot.g0_charset,
    g1_charset: snapshot.g1_charset,
    active_charset: snapshot.active_charset,
    scroll_region: snapshot.scroll_region,
    cells: snapshot.cells,
    text_sig: snapshot.text_sig,
    visual_sig: snapshot.visual_sig,
  };
}

const file = process.argv[2];
if (!file) {
  console.error('usage: js_snapshot.cjs vector.json');
  process.exit(2);
}

const vector = JSON.parse(fs.readFileSync(file, 'utf8'));
const term = vt.createVirtualTerminal(vector.rows || 25, vector.cols || 80);
term.term = vector.term || 'xterm';
term.encoding = vector.encoding || 'utf-8';
for (const chunk of vector.chunks_b64 || []) {
  vt.feedBase64(term, chunk, vector.encoding || 'utf-8');
}
process.stdout.write(JSON.stringify(normalizeSnapshot(vt.renderSnapshot(term))));

