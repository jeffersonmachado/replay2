import { escapeHtml } from "../core/dom.js";

// Render do de→para dos dados sintéticos (detalhe da captura e detalhe da
// run — payload de GET /api/captures/{id}/synthetic-substitutions ou o
// "depara" do POST /api/captures/{id}/synthesize).
// Mostra, por tela, o valor original da captura → valor usado na trilha
// sintética, marcando os campos mantidos (chave de consulta / iguais) e os
// dados digitados preservados sem substituição (opção de menu, campo fora
// da KB, texto sem match) — todo dado digitado fica contabilizado.

function deparaScreenTitle(screen) {
  const name = screen.display_name || screen.entity || "-";
  const showEntity = screen.entity && screen.display_name &&
    screen.display_name !== screen.entity;
  return `Tela ${escapeHtml(name)}${screen.operation ? ` (${escapeHtml(screen.operation)})` : ""}` +
    (showEntity ? ` <span class="font-normal text-stone-500">· entidade ${escapeHtml(screen.entity)}</span>` : "");
}

function renderDeparaFieldRow(field) {
  const keptBadge = field.kept
    ? `<span class="inline-flex items-center rounded-full border border-amber-900/60 bg-amber-950/50 px-2 py-0.5 text-xs text-amber-200">mantido${field.note === "chave de consulta" ? " (chave)" : ""}</span>`
    : "";
  const synthClass = field.kept ? "text-stone-400" : "text-emerald-300";
  return `<tr class="border-b border-stone-800/60">
    <td class="px-3 py-2 font-mono text-stone-200">${escapeHtml(field.field || "-")}</td>
    <td class="px-3 py-2 font-mono text-stone-300 break-all">${escapeHtml(field.original || "")}</td>
    <td class="px-1 py-2 text-stone-500">→</td>
    <td class="px-3 py-2 font-mono ${synthClass} break-all">${escapeHtml(field.synthetic || "")}</td>
    <td class="px-3 py-2">${keptBadge}</td>
  </tr>`;
}

function renderDeparaPreservedRow(preserved) {
  const note = preserved.note ? ` — ${escapeHtml(preserved.note)}` : "";
  return `<tr class="border-b border-stone-800/60">
    <td class="px-3 py-2 font-mono text-stone-400">${escapeHtml(preserved.field || "-")}</td>
    <td class="px-3 py-2 font-mono text-stone-400 break-all">${escapeHtml(preserved.original || "")}</td>
    <td class="px-1 py-2 text-stone-600">=</td>
    <td class="px-3 py-2 font-mono text-stone-500 break-all">${escapeHtml(preserved.original || "")}</td>
    <td class="px-3 py-2"><span class="inline-flex items-center rounded-full border border-stone-700 bg-stone-900/60 px-2 py-0.5 text-xs text-stone-400">mantido${note}</span></td>
  </tr>`;
}

export function renderDeparaScreenHtml(screen) {
  const rows = (screen.fields || []).map(renderDeparaFieldRow).join("") +
    (screen.preserved || []).map(renderDeparaPreservedRow).join("");
  return `<div>
    <h4 class="mb-2 text-sm font-semibold text-stone-200">${deparaScreenTitle(screen)}</h4>
    <table class="w-full text-left text-xs">
      <thead><tr class="border-b border-stone-700/60 text-stone-400">
        <th class="px-3 py-1">campo</th><th class="px-3 py-1">original</th><th class="px-1 py-1"></th><th class="px-3 py-1">sintético</th><th class="px-3 py-1"></th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>
  </div>`;
}

export function countDeparaFields(screens) {
  const list = Array.isArray(screens) ? screens : [];
  const campos = list.reduce((acc, sc) => acc + (sc.fields || []).length, 0);
  const substituidos = list.reduce(
    (acc, sc) => acc + (sc.fields || []).filter((f) => !f.kept).length, 0);
  const preservados = list.reduce(
    (acc, sc) => acc + (sc.preserved || []).length, 0);
  return { campos, substituidos, preservados };
}

export function renderRunDeparaHtml(payload) {
  const data = payload || {};
  const screens = Array.isArray(data.screens) ? data.screens : [];
  if (!screens.length) {
    return '<div class="text-sm text-stone-400">Nenhum campo foi substituído — esta run usou os dados originais da captura.</div>';
  }
  const keyFields = (Array.isArray(data.key_fields) ? data.key_fields : []).filter(Boolean);
  const notas = [
    "Valor original da captura → valor usado nesta run, por tela.",
    keyFields.length
      ? `Campos mantidos (chave de consulta): ${escapeHtml(keyFields.join(", "))} — conservam o valor original para a consulta encontrar o registro.`
      : "",
    data.journey_id ? `Jornada: ${escapeHtml(data.journey_id)}.` : "",
  ].filter(Boolean);
  const { campos, substituidos, preservados } = countDeparaFields(screens);
  const resumo = `${substituidos} de ${campos} campo(s) substituídos` +
    (preservados ? ` · ${preservados} dado(s) digitado(s) mantido(s) com o valor original` : "") +
    ".";
  return `
    <div class="mb-1 text-xs text-stone-400">${notas.map((n) => `<div>${n}</div>`).join("")}</div>
    <div class="mb-3 text-xs text-stone-400">${escapeHtml(resumo)}</div>
    <div class="space-y-4">${screens.map(renderDeparaScreenHtml).join("")}</div>`;
}
