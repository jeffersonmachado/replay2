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

// Rótulos leigos (pt-BR) da taxonomia de falhas — o usuário final não conhece
// os códigos técnicos. O código original deve permanecer acessível na
// renderização (title/parênteses) para suporte e paridade com a API.
export function failureTypeLabel(type) {
  const value = String(type || "").toLowerCase();
  const labels = {
    functional: "falha funcional",
    timeout: "tempo esgotado",
    screen_divergence: "tela diferente do esperado",
    synthetic_data_swap: "troca de dados esperada",
    technical_error: "erro técnico",
    navigation_error: "erro de navegação",
    concurrency_error: "erro de concorrência",
    checkpoint_mismatch: "ponto de verificação divergente",
    integrity_error: "erro de integridade",
    cancelled: "cancelada",
  };
  if (!value) return "—";
  return labels[value] || String(type);
}

export function severityLabel(severity) {
  const value = String(severity || "").toLowerCase();
  const labels = {
    low: "baixa",
    medium: "média",
    high: "alta",
    critical: "crítica",
    info: "informativa",
  };
  return labels[value] || String(severity || "-");
}

// Conformidade da sessão com a política de acesso (entrou pelo gateway
// auditável ou direto) — rótulos leigos, código técnico fica no title.
export function complianceLabel(status) {
  const value = String(status || "").toLowerCase();
  const labels = {
    compliant: "conforme",
    warning: "atenção",
    non_compliant: "não conforme",
    rejected: "rejeitada",
    not_applicable: "não se aplica",
  };
  if (!value) return "—";
  return labels[value] || String(status);
}

export function entryModeLabel(mode) {
  const value = String(mode || "").toLowerCase();
  const labels = {
    gateway_ssh: "via gateway",
    direct: "direto",
  };
  if (!value) return "—";
  return labels[value] || String(mode);
}

export function statusToneClass(status) {
  const value = String(status || "").toLowerCase();
  if (["running", "resuming"].includes(value)) return "r2ctl-status r2ctl-status-running";
  if (["queued", "pending", "paused", "warning"].includes(value)) return "r2ctl-status r2ctl-status-warn";
  if (["failed", "cancelled", "canceled", "rejected", "breached", "danger"].includes(value)) return "r2ctl-status r2ctl-status-danger";
  if (["completed", "done", "success", "compliant", "ok"].includes(value)) return "r2ctl-status r2ctl-status-brand";
  return "r2ctl-status r2ctl-status-neutral";
}
