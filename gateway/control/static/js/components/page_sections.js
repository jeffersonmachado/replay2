// Maps a URL section name to the content section actually rendered.
// Allows sidebar aliases (flows, signatures, automation) to display
// existing sections while highlighting the correct tab in the nav.
const SECTION_DISPLAY_MAP = {
  observability: {
    flows: "trends",
    signatures: "reprocess",
    automation: "reprocess",
  },
};

export function activatePageSections(group, defaultSection) {
  const root = document.querySelector(`[data-page-group="${group}"]`);
  if (!root) return;

  const state = window.__R2CTL_PAGE_STATE__ || {};
  const section = String(state.section || defaultSection || "").trim();
  const sections = Array.from(root.querySelectorAll("[data-section]"));
  if (!section || !sections.length) return;

  const displaySection = (SECTION_DISPLAY_MAP[group] || {})[section] || section;

  sections.forEach((el) => {
    el.classList.toggle("hidden", (el.dataset.section || "") !== displaySection);
  });

  root.querySelectorAll("[data-tab-link]").forEach((link) => {
    link.classList.toggle("r2ctl-tab-active", (link.dataset.tabLink || "") === section);
  });
}
