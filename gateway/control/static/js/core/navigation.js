import { bindGlobalChrome, loadSessionChrome } from "./session.js";

function initSidebarToggle() {
  const sidebar = document.querySelector(".r2ctl-sidebar");
  const overlay = document.getElementById("sidebar_overlay");
  const openBtn = document.getElementById("sidebar_toggle");
  const closeBtn = document.getElementById("sidebar_close");
  if (!sidebar) return;

  function openSidebar() {
    sidebar.classList.add("is-open");
    overlay?.classList.add("is-open");
    document.body.style.overflow = "hidden";
  }

  function closeSidebar() {
    sidebar.classList.remove("is-open");
    overlay?.classList.remove("is-open");
    document.body.style.overflow = "";
  }

  openBtn?.addEventListener("click", openSidebar);
  closeBtn?.addEventListener("click", closeSidebar);
  overlay?.addEventListener("click", closeSidebar);
}

function initNavGroupToggle() {
  document.querySelectorAll(".r2ctl-sidebar-group").forEach((group) => {
    const link = group.querySelector(".r2ctl-sidebar-link");
    link?.addEventListener("click", () => {
      const isOpen = group.classList.contains("is-open");
      document.querySelectorAll(".r2ctl-sidebar-group").forEach((g) => g.classList.remove("is-open"));
      if (!isOpen) group.classList.add("is-open");
    });
  });
}

window.addEventListener("DOMContentLoaded", () => {
  bindGlobalChrome();
  loadSessionChrome();
  initSidebarToggle();
  initNavGroupToggle();
});
