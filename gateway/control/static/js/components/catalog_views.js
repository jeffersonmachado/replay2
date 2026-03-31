import { escapeHtml, formatCount } from "../core/dom.js";

export function targetCard(item) {
  return `
    <div class="r2ctl-obs-run">
      <div class="flex items-center justify-between gap-3">
        <div>
          <div class="font-mono text-sm text-stone-100">${escapeHtml(item.name || item.env_id || "-")}</div>
          <div class="mt-1 text-xs text-stone-400">${escapeHtml(item.host || "-")} • ${escapeHtml(item.platform || "-")} • ${escapeHtml(item.transport_hint || "-")}</div>
        </div>
        <span class="r2ctl-status-pill r2ctl-status ${item.gateway_required ? "r2ctl-status-danger" : "r2ctl-status-brand"}">${item.gateway_required ? "gateway-only" : "acesso flexivel"}</span>
      </div>
    </div>
  `;
}

export function policyCard(item) {
  return `
    <div class="r2ctl-detail-surface rounded-2xl p-4">
      <div class="font-mono text-sm text-stone-100">${escapeHtml(item.name || item.env_id || "-")}</div>
      <div class="mt-2 text-xs text-stone-400">${escapeHtml(item.target_policy_reason || "-")}</div>
    </div>
  `;
}

export function profileCard(item) {
  return `
    <div class="r2ctl-obs-run">
      <div class="font-mono text-sm text-stone-100">${escapeHtml(item.name || item.profile_id || "-")}</div>
      <div class="mt-1 text-xs text-stone-400">${escapeHtml(item.transport || "ssh")} • ${escapeHtml(item.username || "-")} • ${escapeHtml(item.port || "-")}</div>
    </div>
  `;
}

export function operationalScenarioCard(item, index) {
  return `
    <div class="r2ctl-obs-run">
      <div class="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <div class="font-mono text-sm text-stone-100">${escapeHtml(item.name || "-")}</div>
          <div class="mt-1 text-xs text-stone-400">${escapeHtml(item.scenario_type || "replay")} • ${escapeHtml(item.target_user || "-")}@${escapeHtml(item.target_host || "-")} • runs=${formatCount((item.usage_summary || {}).total_runs || 0)}</div>
          <div class="mt-1 text-xs text-stone-500">${escapeHtml(item.description || "sem descricao")}</div>
        </div>
        <div class="flex flex-wrap gap-2">
          <button class="r2ctl-btn-soft" data-edit-scenario="${escapeHtml(index)}">Editar</button>
          <button class="r2ctl-btn-soft" data-instantiate-scenario="${escapeHtml(item.id)}">Criar run</button>
        </div>
      </div>
    </div>
  `;
}
