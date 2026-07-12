/**
 * Combobox autocomplete — Vanilla JS + Tailwind CSS.
 *
 * Uso:
 *   import { initCombobox } from "../components/combobox.js";
 *   initCombobox(wrapperElement);
 *
 * O wrapper deve ter:
 *   [data-combobox]        — elemento wrapper
 *   [data-combobox-input]  — input de busca
 *   [data-combobox-dropdown] — container do dropdown
 *   [data-combobox-output] — <select> oculto com <option>s
 *
 * Atributos no wrapper (opcionais):
 *   data-combobox='{"placeholder":"Buscar...","minSearchLength":0}'
 */

export function initCombobox(wrapper) {
  // Evita reinicialização duplicada
  if (wrapper._comboboxInitialized) return;
  wrapper._comboboxInitialized = true;

  const input = wrapper.querySelector("[data-combobox-input]");
  const dropdown = wrapper.querySelector("[data-combobox-dropdown]");
  const output = wrapper.querySelector("[data-combobox-output]");
  if (!input || !dropdown || !output) return;

  let config = { minSearchLength: 0, placeholder: "" };
  try { config = { ...config, ...JSON.parse(wrapper.getAttribute("data-combobox") || "{}") }; } catch (_) {}

  let open = false;
  let activeIdx = -1;
  let selectedValue = output.value || "";

  function getOptions() {
    return Array.from(output.options).filter(o => o.value !== "");
  }

  function getVisibleOptions() {
    const query = input.value.toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "");
    if (query.length < config.minSearchLength) return [];
    return getOptions().filter(o =>
      o.textContent.toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "").includes(query)
    );
  }

  function render() {
    const visible = getVisibleOptions();
    dropdown.innerHTML = "";
    activeIdx = -1;

    if (visible.length === 0) {
      if (input.value.length >= config.minSearchLength) {
        const empty = document.createElement("div");
        empty.className = "px-4 py-3 text-sm text-stone-500";
        empty.textContent = "Nenhum resultado encontrado";
        dropdown.appendChild(empty);
      }
    } else {
      visible.forEach((opt, i) => {
        const el = document.createElement("div");
        el.setAttribute("role", "option");
        el.setAttribute("aria-selected", "false");
        el.className = "px-4 py-2.5 text-sm text-stone-200 cursor-pointer hover:bg-stone-700/60 transition-colors";
        el.textContent = opt.textContent;
        el.addEventListener("mousedown", (e) => {
          e.preventDefault();
          selectOption(opt);
        });
        el.addEventListener("mouseenter", () => setActive(i));
        dropdown.appendChild(el);
      });
    }

    // Highlight active
    if (activeIdx >= 0 && activeIdx < dropdown.children.length) {
      dropdown.children[activeIdx].classList.add("bg-stone-700/60", "text-white");
      dropdown.children[activeIdx].setAttribute("aria-selected", "true");
    }
  }

  function setActive(idx) {
    const children = dropdown.children;
    if (activeIdx >= 0 && activeIdx < children.length) {
      children[activeIdx].classList.remove("bg-stone-700/60", "text-white");
      children[activeIdx].setAttribute("aria-selected", "false");
    }
    activeIdx = idx;
    if (idx >= 0 && idx < children.length) {
      children[idx].classList.add("bg-stone-700/60", "text-white");
      children[idx].setAttribute("aria-selected", "true");
      children[idx].scrollIntoView({ block: "nearest" });
    }
  }

  function selectOption(opt) {
    input.value = opt.textContent;
    output.value = opt.value;
    selectedValue = opt.value;
    close();
    output.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function openDropdown() {
    if (open) return;
    open = true;
    input.value = selectedValue ? (getOptions().find(o => o.value === selectedValue)?.textContent || "") : "";
    render();
    dropdown.classList.remove("hidden");
    wrapper.setAttribute("data-combobox-open", "");
    input.setAttribute("aria-expanded", "true");
  }

  function close() {
    open = false;
    dropdown.classList.add("hidden");
    wrapper.removeAttribute("data-combobox-open");
    input.setAttribute("aria-expanded", "false");
    activeIdx = -1;
    // Restore display text from selected value
    if (selectedValue) {
      const opt = getOptions().find(o => o.value === selectedValue);
      if (opt) input.value = opt.textContent;
    }
  }

  function resetSelection() {
    selectedValue = "";
    output.value = "";
    input.value = "";
    output.dispatchEvent(new Event("change", { bubbles: true }));
  }

  // ── Event listeners ──

  input.addEventListener("focus", () => {
    if (config.minSearchLength === 0 && getOptions().length > 0) openDropdown();
  });

  input.addEventListener("input", () => {
    // Se o usuário digitar algo diferente do texto da opção selecionada, limpa a seleção
    const currentOpt = getOptions().find(o => o.value === selectedValue);
    if (selectedValue && (!currentOpt || input.value !== currentOpt.textContent)) {
      resetSelection();
    }
    if (!open) {
      openDropdown();
    } else {
      render();
    }
  });

  input.addEventListener("keydown", (e) => {
    if (!open) {
      if (e.key === "ArrowDown" || (e.key === "Enter" && getOptions().length > 0)) {
        openDropdown();
        e.preventDefault();
      }
      return;
    }

    const items = dropdown.children;
    const visibleCount = items.length;

    switch (e.key) {
      case "ArrowDown":
        e.preventDefault();
        setActive(activeIdx + 1 >= visibleCount ? 0 : activeIdx + 1);
        break;
      case "ArrowUp":
        e.preventDefault();
        setActive(activeIdx - 1 < 0 ? visibleCount - 1 : activeIdx - 1);
        break;
      case "Enter":
        e.preventDefault();
        if (activeIdx >= 0 && activeIdx < visibleCount) {
          const opt = getVisibleOptions()[activeIdx];
          if (opt) selectOption(opt);
        }
        break;
      case "Escape":
        e.preventDefault();
        close();
        break;
    }
  });

  input.addEventListener("blur", () => {
    // Delay to allow click on dropdown item
    setTimeout(() => {
      if (!wrapper.contains(document.activeElement)) close();
    }, 150);
  });

  // Click outside
  document.addEventListener("click", (e) => {
    if (!wrapper.contains(e.target)) close();
  });

  // ARIA attributes
  input.setAttribute("role", "combobox");
  input.setAttribute("aria-expanded", "false");
  input.setAttribute("aria-autocomplete", "list");
  input.setAttribute("aria-controls", dropdown.id || "");
  input.setAttribute("autocomplete", "off");
  dropdown.setAttribute("role", "listbox");
  if (!dropdown.id) dropdown.id = `combobox-dropdown-${Math.random().toString(36).slice(2, 8)}`;
  input.setAttribute("aria-controls", dropdown.id);

  // Store instance for external access
  wrapper._combobox = { open: openDropdown, close, selectOption, resetSelection, getValue: () => output.value };
}
