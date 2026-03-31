export const qs = (selector) => document.querySelector(selector);
export const qsa = (selector) => Array.from(document.querySelectorAll(selector));

export function text(selector, value) {
  const el = typeof selector === "string" ? qs(selector) : selector;
  if (el) {
    el.textContent = String(value ?? "");
  }
}

export function html(selector, value) {
  const el = typeof selector === "string" ? qs(selector) : selector;
  if (el) {
    el.innerHTML = String(value ?? "");
  }
}

export function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

export function formatCount(value) {
  return new Intl.NumberFormat("pt-BR").format(Number(value || 0));
}

export function formatAgo(tsMs) {
  if (!tsMs) return "-";
  const diff = Math.max(0, Date.now() - Number(tsMs));
  const sec = Math.floor(diff / 1000);
  if (sec < 5) return "agora";
  if (sec < 60) return `${sec}s atrás`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m atrás`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h atrás`;
  return `${Math.floor(hr / 24)}d atrás`;
}

export function formatDate(tsMs) {
  if (!tsMs) return "-";
  return new Date(Number(tsMs)).toLocaleString("pt-BR");
}

export function statusLabel(status) {
  const value = String(status || "").toLowerCase();
  const labels = {
    queued: "na fila",
    running: "em execução",
    paused: "pausada",
    failed: "falhou",
    success: "sucesso",
    cancelled: "cancelada",
    canceled: "cancelada",
    pending: "pendente",
    resuming: "retomando",
    completed: "concluída",
    done: "concluída",
  };
  return labels[value] || String(status || "-");
}

export function modeLabel(mode) {
  const value = String(mode || "").toLowerCase();
  if (value === "strict-global") return "sequencial estrito";
  if (value === "parallel-sessions") return "paralelo por sessão";
  return String(mode || "-");
}

export function statusToneClass(status) {
  const value = String(status || "").toLowerCase();
  if (["running", "resuming"].includes(value)) return "r2ctl-status r2ctl-status-running";
  if (["queued", "pending", "paused", "warning"].includes(value)) return "r2ctl-status r2ctl-status-warn";
  if (["failed", "cancelled", "canceled", "rejected", "breached", "danger"].includes(value)) return "r2ctl-status r2ctl-status-danger";
  if (["completed", "done", "success", "compliant", "ok"].includes(value)) return "r2ctl-status r2ctl-status-brand";
  return "r2ctl-status r2ctl-status-neutral";
}
