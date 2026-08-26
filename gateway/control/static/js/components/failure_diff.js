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

function renderScreenColumn(title, rows, side) {
  const lines = rows
    .map((row) => {
      const value = side === "expected" ? row.expected : row.observed;
      const cls = row.changed ? "bg-rose-950/60 text-rose-100" : "text-stone-300";
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

export function renderScreenDiffHtml(expected, observed) {
  const rows = diffScreenLines(expected, observed);
  const stats = screenDiffStats(rows);
  return `
    <p class="mb-3 text-xs text-stone-400">${stats.changed} de ${stats.total} linha(s) divergem — linhas destacadas em rosa.</p>
    <div class="flex flex-col gap-3 lg:flex-row">
      ${renderScreenColumn("Tela esperada (captura)", rows, "expected")}
      ${renderScreenColumn("Tela observada (run)", rows, "observed")}
    </div>`;
}

// Corpo do modal de divergência de uma falha: telas lado a lado quando a run
// gravou o conteúdo; caso contrário (runs antigas) mostra mensagem + sigs.
export function renderFailureDivergenceHtml(failure) {
  const item = failure || {};
  const evidence = item.evidence || {};
  const meta = `
    <div class="mb-3 grid gap-2 text-xs text-stone-400">
      <div><span class="text-stone-500">Tipo:</span> <span class="text-stone-200">${escapeHtml(item.failure_type || "—")}</span>
        <span class="text-stone-500">· seq:</span> <span class="text-stone-200">${escapeHtml(String(item.seq_global ?? "—"))}</span>
        <span class="text-stone-500">· sessão:</span> <span class="font-mono text-stone-200">${escapeHtml(String(item.session_id || "—"))}</span></div>
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
  return meta + renderScreenDiffHtml(evidence.expected_screen || "", evidence.observed_screen || "");
}
