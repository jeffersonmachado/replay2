/**
 * infinite_scroll_list.js — lista com scroll infinito (renderização em lotes).
 * Usado por: Replay (capture_session_replay.html)
 *
 * Renderiza os itens em lotes conforme o usuário rola a página: um sentinela
 * no fim do container dispara o próximo lote via IntersectionObserver. Sem
 * suporte a IntersectionObserver, cai para um botão "Carregar mais".
 * A lógica de fatiamento fica em LazyListController (testável sem DOM).
 */

export const DEFAULT_BATCH_SIZE = 50;

/**
 * Controlador puro de lotes: mantém itens e cursor, sem dependência de DOM.
 */
export class LazyListController {
  constructor({ batchSize = DEFAULT_BATCH_SIZE } = {}) {
    const size = Number(batchSize);
    this.batchSize = Number.isFinite(size) && size >= 1 ? Math.floor(size) : DEFAULT_BATCH_SIZE;
    this.items = [];
    this.cursor = 0;
  }

  setItems(items) {
    this.items = Array.isArray(items) ? items : [];
    this.cursor = 0;
  }

  appendItems(items) {
    // Estende a lista preservando o cursor (paginação incremental — X6).
    const novos = Array.isArray(items) ? items : [];
    this.items.push(...novos);
    return novos.length;
  }

  get totalCount() {
    return this.items.length;
  }

  get renderedCount() {
    return Math.min(this.cursor, this.items.length);
  }

  hasMore() {
    return this.cursor < this.items.length;
  }

  nextBatch() {
    if (!this.hasMore()) return [];
    const end = Math.min(this.items.length, this.cursor + this.batchSize);
    const batch = this.items.slice(this.cursor, end);
    this.cursor = end;
    return batch;
  }
}

/**
 * Vincula um LazyListController a um container DOM com scroll infinito.
 * Retorna { controller, setItems, renderNext, destroy }.
 */
export function attachInfiniteScroll({
  container,
  batchSize = DEFAULT_BATCH_SIZE,
  renderItem,
  loadMoreLabel = "Carregar mais eventos",
  loadingMoreLabel = "Carregando mais eventos do servidor…",
  rootMargin = "400px",
  onExhausted = null,
} = {}) {
  if (!container || typeof renderItem !== "function") {
    throw new Error("attachInfiniteScroll: container e renderItem são obrigatórios");
  }
  const controller = new LazyListController({ batchSize });
  const useObserver = typeof IntersectionObserver === "function";
  let sentinel = null;
  let observer = null;
  // Paginação server-side (X6): quando os itens locais acabam, onExhausted
  // pode buscar mais (appendItems no controller) e retornar true.
  let exhaustedFired = false;
  let exhaustedDone = false;

  function disconnectObserver() {
    if (observer) {
      observer.disconnect();
      observer = null;
    }
  }

  function removeSentinel() {
    disconnectObserver();
    if (sentinel) {
      sentinel.remove();
      sentinel = null;
    }
  }

  function ensureSentinel() {
    if (sentinel) return;
    sentinel = document.createElement("div");
    sentinel.className = "text-center text-xs text-stone-500 py-3";
    container.appendChild(sentinel);
  }

  function renderNext() {
    const batch = controller.nextBatch();
    if (batch.length) {
      ensureSentinel();
      sentinel.insertAdjacentHTML("beforebegin", batch.map(renderItem).join(""));
    }
    updateSentinel();
  }

  function updateSentinel() {
    if (!controller.hasMore()) {
      if (typeof onExhausted === "function" && !exhaustedDone) {
        if (!exhaustedFired) {
          exhaustedFired = true;
          ensureSentinel();
          sentinel.textContent = loadingMoreLabel;
          Promise.resolve()
            .then(() => onExhausted())
            .then((more) => {
              exhaustedFired = false;
              if (more) {
                renderNext();
              } else {
                exhaustedDone = true;
                removeSentinel();
              }
            })
            .catch(() => {
              exhaustedFired = false;
              exhaustedDone = true;
              removeSentinel();
            });
        }
        return;
      }
      removeSentinel();
      return;
    }
    ensureSentinel();
    const progress = `${controller.renderedCount} de ${controller.totalCount} eventos`;
    if (useObserver) {
      sentinel.textContent = `${progress} — role para carregar mais`;
      if (!observer) {
        observer = new IntersectionObserver(
          (entries) => {
            if (entries.some((entry) => entry.isIntersecting)) renderNext();
          },
          { rootMargin },
        );
        observer.observe(sentinel);
      }
    } else {
      sentinel.innerHTML = "";
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "r2ctl-btn-soft text-xs";
      btn.textContent = `${loadMoreLabel} (${progress})`;
      btn.addEventListener("click", renderNext);
      sentinel.appendChild(btn);
    }
  }

  return {
    controller,
    setItems(items) {
      removeSentinel();
      container.innerHTML = "";
      exhaustedFired = false;
      exhaustedDone = false;
      controller.setItems(items);
      if (controller.totalCount > 0) renderNext();
    },
    renderNext,
    destroy() {
      removeSentinel();
      container.innerHTML = "";
    },
  };
}
