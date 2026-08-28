import { escapeHtml } from "../core/dom.js";

// Comparação linha a linha das telas gravadas numa falha de replay
// (expected_screen × observed_screen do evidence). As telas vêm de um
// terminal de geometria fixa, então o alinhamento por índice de linha basta.

export function diffScreenLines(expected, observed) {
  const expLines = String(expected ?? "").split("\n");
  const obsLines = String(observed ?? "").split("\n");
  const total = Math.max(expLines.length, obsLines.length);
  const rows = [];
  for (let i = 0; i < total; i++) {
    const exp = i < expLines.length ? expLines[i] : null;
    const obs = i < obsLines.length ? obsLines[i] : null;
    rows.push({ index: i, expected: exp, observed: obs, changed: exp !== obs });
  }
  return rows;
}

export function screenDiffStats(rows) {
  const list = Array.isArray(rows) ? rows : [];
  const changed = list.filter((row) => row.changed).length;
  return { total: list.length, changed };
}

export function failureHasScreenDiff(failure) {
  const evidence = (failure && failure.evidence) || {};
  return Boolean(evidence.expected_screen || evidence.observed_screen);
}

function truncateSig(value) {
  const raw = String(value ?? "");
  if (raw.length <= 24) return raw;
  return `${raw.slice(0, 12)}…${raw.slice(-8)}`;
}

// Marcação de eco do de→para sintético (linhas em âmbar no player). Espelha
// dakota_gateway/replay_compare.py: pares longos (≥3 chars) são substituídos
// diretamente pelo placeholder; pares curtos são conferidos nos trechos
// divergentes da linha via diff de caracteres (LCS).
export const SUBSTITUTION_PLACEHOLDER = "<troca>";

function normalizePairs(substitutions) {
  const pairs = [];
  for (const item of substitutions || []) {
    if (!Array.isArray(item) || item.length < 2) continue;
    const orig = String(item[0] ?? "");
    const synth = String(item[1] ?? "");
    if (orig && synth && orig !== synth) pairs.push([orig, synth]);
  }
  return pairs;
}

// Diff de caracteres por LCS — retorna trechos divergentes [chunkA, chunkB].
function charDiffChunks(a, b) {
  const n = a.length;
  const m = b.length;
  const dp = Array.from({ length: n + 1 }, () => new Uint16Array(m + 1));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  const chunks = [];
  let i = 0;
  let j = 0;
  let ca = "";
  let cb = "";
  const flush = () => {
    if (ca || cb) chunks.push([ca, cb]);
    ca = "";
    cb = "";
  };
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      flush();
      i++;
      j++;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      ca += a[i];
      i++;
    } else {
      cb += b[j];
      j++;
    }
  }
  ca += a.slice(i);
  cb += b.slice(j);
  flush();
  return chunks;
}

// Identificador gerado pela aplicação (ex.: número do pedido D00011073) —
// não é dado digitado, mas muda a cada run sintética como consequência da
// troca. Mesmo shape na mesma posição = eco da troca.
const GENERATED_ID_RE = /[A-Za-z]?\d{5,}/g;

// Normaliza número ignorando zeros à esquerda ('01' e '1' são o mesmo valor).
function numericIdentity(value) {
  return /^\d+$/.test(value) ? String(parseInt(value, 10)) : value;
}

function chunksHaveShortPairEcho(lineExp, lineObs, shortPairs) {
  const shortNumeric = new Set(shortPairs.map(([o, s]) => `${numericIdentity(o)}->${numericIdentity(s)}`));
  for (const [ca, cb] of charDiffChunks(lineExp, lineObs)) {
    const ea = ca.trim();
    const eb = cb.trim();
    if (!ea && !eb) continue;
    if (shortPairs.some(([o, s]) => o === ea && s === eb)) return true;
    if (shortNumeric.has(`${numericIdentity(ea)}->${numericIdentity(eb)}`)) return true;
  }
  return false;
}

// True quando TODOS os tokens numéricos divergentes casam pares curtos.
// Cobre o caso em que o diff de caracteres funde o número com o texto ao
// lado (o decode muda junto: '01 PAC' → ' 2 SEDEX'). Conservador: se algum
// token divergente não casa um par curto, a linha não é eco.
function numericTokensEcho(lineExp, lineObs, shortPairs) {
  const numsExp = lineExp.match(/\d+/g) || [];
  const numsObs = lineObs.match(/\d+/g) || [];
  if (!numsExp.length || numsExp.length !== numsObs.length) return false;
  if (numsExp.every((token, i) => token === numsObs[i])) return false;
  const shortNumeric = new Set(shortPairs.map(([o, s]) => `${numericIdentity(o)}->${numericIdentity(s)}`));
  let sawDiff = false;
  for (let i = 0; i < numsExp.length; i++) {
    if (numsExp[i] === numsObs[i]) continue;
    sawDiff = true;
    if (!shortNumeric.has(`${numericIdentity(numsExp[i])}->${numericIdentity(numsObs[i])}`)) return false;
  }
  return sawDiff;
}

function chunksHaveGeneratedIdEcho(lineExp, lineObs) {
  const idsExp = lineExp.match(GENERATED_ID_RE) || [];
  const idsObs = lineObs.match(GENERATED_ID_RE) || [];
  if (!idsExp.length || idsExp.length !== idsObs.length) return false;
  if (idsExp.every((token, i) => token === idsObs[i])) return false;
  const shape = (token) => token.replace(/\d/g, "#");
  return idsExp.every((token, i) => shape(token) === shape(idsObs[i]));
}

function lineHasEcho(lineExp, lineObs, shortPairs) {
  if (lineExp.includes(SUBSTITUTION_PLACEHOLDER) || lineObs.includes(SUBSTITUTION_PLACEHOLDER)) return true;
  if (chunksHaveShortPairEcho(lineExp, lineObs, shortPairs)) return true;
  if (numericTokensEcho(lineExp, lineObs, shortPairs)) return true;
  return chunksHaveGeneratedIdEcho(lineExp, lineObs);
}

// Índices das linhas divergentes que contêm eco do de→para. Linha que só
// diverge pela troca (idêntica após a máscara dos pares longos) é troca pura
// e também conta como eco.
export function substitutionEchoLineIndices(expected, observed, substitutions) {
  const pairs = normalizePairs(substitutions);
  if (!pairs.length) return [];
  const longPairs = pairs.filter(([o, s]) => o.length >= 3 && s.length >= 3);
  const shortPairs = pairs.filter(([o, s]) => !(o.length >= 3 && s.length >= 3));
  const expRawLines = String(expected ?? "").split("\n");
  const obsRawLines = String(observed ?? "").split("\n");
  let exp = String(expected ?? "");
  let obs = String(observed ?? "");
  for (const [orig, synth] of longPairs) {
    exp = exp.split(orig).join(SUBSTITUTION_PLACEHOLDER);
    obs = obs.split(synth).join(SUBSTITUTION_PLACEHOLDER);
  }
  const expLines = exp.split("\n");
  const obsLines = obs.split("\n");
  const total = Math.max(expLines.length, obsLines.length);
  const indices = [];
  for (let i = 0; i < total; i++) {
    const re = i < expRawLines.length ? expRawLines[i] : "";
    const ro = i < obsRawLines.length ? obsRawLines[i] : "";
    if (re === ro) continue;
    const le = i < expLines.length ? expLines[i] : "";
    const lo = i < obsLines.length ? obsLines[i] : "";
    if (le === lo || lineHasEcho(le, lo, shortPairs)) indices.push(i);
  }
  return indices;
}

function renderScreenColumn(title, rows, side) {
  const lines = rows
    .map((row) => {
      const value = side === "expected" ? row.expected : row.observed;
      const cls = row.changed
        ? (row.echo ? "bg-amber-950/60 text-amber-100" : "bg-rose-950/60 text-rose-100")
        : "text-stone-300";
      const content = value === null ? "" : escapeHtml(value);
      return `<div class="${cls} whitespace-pre px-2 leading-5">${content || " "}</div>`;
    })
    .join("");
  return `
    <div class="min-w-0 flex-1">
      <div class="mb-1 text-xs uppercase tracking-[0.14em] text-stone-400">${escapeHtml(title)}</div>
      <div class="max-h-[46vh] overflow-auto rounded-2xl border border-stone-800 bg-stone-950/60 py-1 font-mono text-xs">${lines}</div>
    </div>`;
}

export function renderScreenDiffHtml(expected, observed, substitutions) {
  const rows = diffScreenLines(expected, observed);
  const echoSet = new Set(substitutionEchoLineIndices(expected, observed, substitutions));
  for (const row of rows) row.echo = echoSet.has(row.index);
  const stats = screenDiffStats(rows);
  const legend = echoSet.size
    ? `${stats.changed} de ${stats.total} linha(s) divergem — em <span class="text-amber-200">âmbar</span> o eco da troca de dados sintéticos; em <span class="text-rose-200">rosa</span> as demais divergências.`
    : `${stats.changed} de ${stats.total} linha(s) divergem — linhas destacadas em rosa.`;
  return `
    <p class="mb-3 text-xs text-stone-400">${legend}</p>
    <div class="flex flex-col gap-3 lg:flex-row">
      ${renderScreenColumn("Tela esperada (captura)", rows, "expected")}
      ${renderScreenColumn("Tela observada (run)", rows, "observed")}
    </div>`;
}

// Player inline da seção "Eventos e falhas": as duas telas lado a lado e a
// navegação ⏮ primeira / ◀ anterior / ▶ reproduzir / próxima ▶ / última ⏭ +
// salto direto para uma falha (campo "ir para"), entre as falhas em ordem
// cronológica (seq crescente), para acompanhar onde a divergência começou e
// como evoluiu. O JS da página controla a troca de falha (ids fp_*).
// Com runId, mostra o link "⇄ Comparar sessões" para /runs/{id}/compare
// (sessão capturada × sessão observada no ponto da falha — v0.8.66).
export function renderFailureInlinePlayerHtml(failure, position, total, substitutions, runId) {
  const item = failure || {};
  const evidence = item.evidence || {};
  const ts = item.ts_ms ? new Date(item.ts_ms).toLocaleString("pt-BR") : "—";
  const compareHref = runId
    ? `/runs/${encodeURIComponent(String(runId))}/compare?session_id=${encodeURIComponent(String(item.session_id || ""))}&failure=${Math.max(0, Number(position) - 1)}`
    : "";
  const compareBtn = compareHref
    ? `<a id="fp_compare" href="${compareHref}" class="r2ctl-btn-soft text-xs">⇄ Comparar sessões</a>`
    : "";
  const meta = `
    <div class="mb-3 flex flex-wrap items-center gap-2">
      <button id="fp_first" type="button" class="r2ctl-btn-soft text-xs"${position <= 1 ? " disabled" : ""}>⏮ Primeira</button>
      <button id="fp_prev" type="button" class="r2ctl-btn-soft text-xs"${position <= 1 ? " disabled" : ""}>◀ Anterior</button>
      <button id="fp_play" type="button" class="r2ctl-btn-soft text-xs">▶ Reproduzir</button>
      <button id="fp_next" type="button" class="r2ctl-btn-soft text-xs"${position >= total ? " disabled" : ""}>Próxima ▶</button>
      <button id="fp_last" type="button" class="r2ctl-btn-soft text-xs"${position >= total ? " disabled" : ""}>Última ⏭</button>
      ${compareBtn}
      <span class="flex items-center gap-1 text-xs text-stone-400">
        <label for="fp_goto">ir para</label>
        <input id="fp_goto" type="number" min="1" max="${total}" placeholder="${position}"
          class="w-20 rounded-lg border border-stone-700 bg-stone-900 px-2 py-1 text-xs text-stone-200" />
        <span>/ ${total}</span>
      </span>
      <span id="fp_position" class="rounded-full bg-stone-800 px-3 py-1 text-xs text-stone-200">falha ${position} de ${total} · seq ${escapeHtml(String(item.seq_global ?? "—"))}</span>
    </div>
    <div class="mb-3 grid gap-2 text-xs text-stone-400">
      <div><span class="text-stone-500">Tipo:</span> <span class="text-stone-200">${escapeHtml(item.failure_type || "—")}</span>
        <span class="text-stone-500">· gravidade:</span> <span class="text-stone-200">${escapeHtml(item.severity || "—")}</span>
        <span class="text-stone-500">· sessão:</span> <span class="font-mono text-stone-200">${escapeHtml(String(item.session_id || "—"))}</span>
        <span class="text-stone-500">·</span> <span class="text-stone-200">${escapeHtml(ts)}</span></div>
      <div><span class="text-stone-500">Mensagem:</span> <span class="text-stone-300">${escapeHtml(item.message || "—")}</span></div>
    </div>`;
  if (!failureHasScreenDiff(item)) {
    return `${meta}
      <div class="rounded-2xl border border-amber-800/50 bg-amber-950/20 px-4 py-3 text-xs text-amber-100">
        Esta run não gravou o conteúdo das telas (gravado a partir da v0.8.40) — apenas as assinaturas abaixo.
      </div>
      <div class="mt-3 grid gap-2 font-mono text-xs text-stone-300">
        <div><span class="text-stone-500">esperada:</span> ${escapeHtml(truncateSig(item.expected_value))}</div>
        <div><span class="text-stone-500">observada:</span> ${escapeHtml(truncateSig(item.observed_value))}</div>
      </div>`;
  }
  return meta + renderScreenDiffHtml(evidence.expected_screen || "", evidence.observed_screen || "", substitutions);
}
