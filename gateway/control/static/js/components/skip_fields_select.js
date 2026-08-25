/**
 * skip_fields_select.js — multi-select de campos "Manter originais (replay)".
 *
 * Dropdown com checkboxes agrupados por tela (entidade/operação) da trilha da
 * captura. Campos-chave (chave de consulta detectada na knowledge base) vêm
 * marcados e desabilitados — já são mantidos automaticamente pelo backend.
 *
 * As funções puras (buildSkipFieldsModel, summarizeSelection, parseStoredSelection)
 * são testáveis com node:test; a parte DOM fica em render/init.
 */

/** Modelo de view: grupos por tela com flags checked/disabled por campo. */
export function buildSkipFieldsModel(screens, keyFields, selected) {
  const keys = new Set((keyFields || []).map((f) => String(f).toLowerCase()));
  const sel = new Set((selected || []).map((f) => String(f).toLowerCase()));
  const groups = [];
  for (const screen of screens || []) {
    const fields = [];
    for (const f of screen.fields || []) {
      const name = String(f.field || "").trim();
      if (!name) continue;
      const isKey = keys.has(name.toLowerCase()) || f.key === true;
      fields.push({
        field: name,
        original: String(f.original || ""),
        key: isKey,
        checked: isKey || sel.has(name.toLowerCase()),
      });
    }
    if (fields.length) {
      const entity = String(screen.entity || "").trim();
      const operation = String(screen.operation || "").trim();
      const title = String(screen.screen_title || "").trim();
      groups.push({
        label: title || (entity ? `${entity}${operation ? " · " + operation : ""}` : "tela"),
        entity,
        operation,
        fields,
      });
    }
  }
  return groups;
}

/** Texto do botão: resume a seleção de exceções (chaves não contam). */
export function summarizeSelection(model) {
  let total = 0;
  let extras = 0;
  let keys = 0;
  for (const g of model || []) {
    for (const f of g.fields || []) {
      total += 1;
      if (f.key) keys += 1;
      else if (f.checked) extras += 1;
    }
  }
  if (!total) return "Nenhum campo mapeado na trilha";
  const parts = [];
  if (extras) parts.push(`${extras} campo(s) mantido(s)`);
  if (keys) parts.push(`${keys} chave(s) automática(s)`);
  if (!parts.length) return "Nenhum campo mantido";
  return parts.join(" · ");
}

/** Lê a seleção persistida (JSON array) tolerando lixo no storage. */
export function parseStoredSelection(raw) {
  try {
    const parsed = JSON.parse(String(raw || "[]"));
    if (!Array.isArray(parsed)) return [];
    return parsed.map((f) => String(f).trim()).filter(Boolean);
  } catch (_) {
    return [];
  }
}

/** Renderiza os grupos de checkboxes dentro do container do painel. */
export function renderSkipFieldsGroups(container, model, { onChange } = {}) {
  container.innerHTML = "";
  if (!model.length) {
    const empty = document.createElement("div");
    empty.className = "px-2 py-3 text-xs text-stone-500";
    empty.textContent = "Nenhum campo mapeado nesta trilha.";
    container.appendChild(empty);
    return;
  }
  for (const group of model) {
    const groupEl = document.createElement("div");
    groupEl.className = "px-1 py-1";

    const head = document.createElement("div");
    head.className = "mb-1 text-[10px] font-semibold uppercase tracking-[0.14em] text-stone-500";
    head.textContent = group.label;
    groupEl.appendChild(head);

    for (const f of group.fields) {
      const label = document.createElement("label");
      label.className = "flex cursor-pointer items-center gap-2 rounded px-2 py-1 text-xs text-stone-200 hover:bg-stone-800/60";
      if (f.key) label.classList.add("opacity-70", "cursor-not-allowed");

      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = f.checked;
      cb.disabled = f.key;
      cb.setAttribute("data-skip-field", f.field);
      cb.className = "accent-rose-500";
      if (onChange) cb.addEventListener("change", () => onChange());

      const name = document.createElement("span");
      name.className = "font-mono";
      name.textContent = f.field;

      label.appendChild(cb);
      label.appendChild(name);
      if (f.original) {
        const orig = document.createElement("span");
        orig.className = "truncate text-stone-500";
        orig.textContent = `= ${f.original}`;
        label.appendChild(orig);
      }
      if (f.key) {
        const badge = document.createElement("span");
        badge.className = "ml-auto rounded border border-amber-700/50 bg-amber-950/40 px-1.5 py-0.5 text-[10px] text-amber-300";
        badge.textContent = "chave (automática)";
        label.appendChild(badge);
      }
      groupEl.appendChild(label);
    }
    container.appendChild(groupEl);
  }
}

/** Nomes dos campos marcados pelo usuário (chaves desabilitadas não entram). */
export function collectSelectedFields(container) {
  const out = [];
  for (const cb of container.querySelectorAll("input[data-skip-field]")) {
    if (cb.checked && !cb.disabled) out.push(cb.getAttribute("data-skip-field"));
  }
  return out;
}

/**
 * Liga o dropdown: toggle abre/fecha; na primeira abertura chama `load()`
 * (async → payload da API) e renderiza. `storageKey` persiste a seleção.
 */
export function initSkipFieldsSelect(wrapper, { load, storageKey } = {}) {
  if (!wrapper || wrapper._skipFieldsInitialized) return;
  wrapper._skipFieldsInitialized = true;

  const toggle = wrapper.querySelector("[data-skip-toggle]");
  const panel = wrapper.querySelector("[data-skip-panel]");
  const body = wrapper.querySelector("[data-skip-body]");
  const summary = wrapper.querySelector("[data-skip-summary]");
  if (!toggle || !panel || !body) return;

  let loaded = false;
  let loading = false;
  let model = [];

  function persist() {
    if (!storageKey) return;
    try {
      localStorage.setItem(storageKey, JSON.stringify(collectSelectedFields(body)));
    } catch (_) {}
  }

  function refreshSummary() {
    if (summary) summary.textContent = summarizeSelection(model);
  }

  async function ensureLoaded() {
    if (loaded || loading || typeof load !== "function") return;
    loading = true;
    body.innerHTML = '<div class="px-2 py-3 text-xs text-stone-400">Mapeando campos da trilha…</div>';
    try {
      const data = await load();
      const stored = storageKey ? parseStoredSelection(localStorage.getItem(storageKey)) : [];
      model = buildSkipFieldsModel(data?.screens, data?.key_fields, stored);
      renderSkipFieldsGroups(body, model, { onChange: () => { persist(); refreshSummary(); } });
      loaded = true;
    } catch (err) {
      body.innerHTML = "";
      const fail = document.createElement("div");
      fail.className = "px-2 py-3 text-xs text-rose-300";
      fail.textContent = `Falha ao mapear campos: ${err?.message || err || "erro desconhecido"}`;
      body.appendChild(fail);
    } finally {
      loading = false;
      refreshSummary();
    }
  }

  function isOpen() {
    return !panel.classList.contains("hidden");
  }

  function open() {
    panel.classList.remove("hidden");
    toggle.setAttribute("aria-expanded", "true");
    ensureLoaded();
  }

  function close() {
    panel.classList.add("hidden");
    toggle.setAttribute("aria-expanded", "false");
  }

  toggle.addEventListener("click", (e) => {
    e.preventDefault();
    if (isOpen()) close(); else open();
  });
  document.addEventListener("click", (e) => {
    if (!wrapper.contains(e.target)) close();
  });

  toggle.setAttribute("aria-expanded", "false");
  refreshSummary();

  wrapper._skipFields = {
    open,
    close,
    reload: () => { loaded = false; ensureLoaded(); },
    getSelected: () => {
      // Nunca abriu o dropdown? Usa a seleção persistida da última vez.
      if (!loaded && storageKey) return parseStoredSelection(localStorage.getItem(storageKey));
      return collectSelectedFields(body);
    },
  };
}
