import { apiJson, jsonRequest } from "../core/api.js";
import { escapeHtml, formatDate, html, text } from "../core/dom.js";

async function loadSessionCard() {
  const [me, gateway] = await Promise.all([apiJson("/api/me"), apiJson("/api/gateway/status")]);
  if (me?.data) {
    html("#admin_session_card", `
      <div class="r2ctl-detail-surface rounded-2xl p-4">
        <div class="font-mono text-sm text-stone-100">${escapeHtml(me.data.username || "-")}</div>
        <div class="mt-1 text-xs text-stone-400">role=${escapeHtml(me.data.role || "-")}</div>
      </div>
      <div class="r2ctl-detail-surface rounded-2xl p-4">
        <div class="font-mono text-sm text-stone-100">gateway ${gateway?.data?.running ? "ativo" : "inativo"}</div>
        <div class="mt-1 text-xs text-stone-400">${escapeHtml(gateway?.data?.service || "-")} • ${escapeHtml(gateway?.data?.platform || "-")}</div>
      </div>
    `);
  }
  html("#admin_settings_card", `
    <div class="r2ctl-detail-surface rounded-2xl p-4">
      <div class="font-mono text-sm text-stone-100">rotas HTML reorganizadas</div>
      <div class="mt-1 text-xs text-stone-400">Dashboard, Execuções, Gateway, Catálogo, Observabilidade e Administração</div>
    </div>
    <div class="r2ctl-detail-surface rounded-2xl p-4">
      <div class="font-mono text-sm text-stone-100">assets JS modulares</div>
      <div class="mt-1 text-xs text-stone-400">scripts por página servidos em /assets/js</div>
    </div>
  `);
}

async function loadUsers() {
  const result = await apiJson("/api/users");
  if (!result) return;
  if (!result.ok) {
    text("#admin_users_status", "lista disponível apenas para admin");
    html("#admin_users_list", '<div class="text-sm text-stone-400">Seu perfil não pode listar usuários.</div>');
    return;
  }
  const users = result.data.users || [];
  text("#admin_users_status", `${users.length} usuários carregados`);
  html("#admin_users_list", users.map((user) => `
    <div class="r2ctl-obs-run">
      <div class="font-mono text-sm text-stone-100">${escapeHtml(user.username || "-")}</div>
      <div class="mt-1 text-xs text-stone-400">${escapeHtml(user.role || "-")} • criado em ${formatDate(user.created_at_ms)}</div>
    </div>
  `).join("") || '<div class="text-sm text-stone-400">Nenhum usuário encontrado.</div>');
}

async function createUser() {
  const payload = {
    username: document.getElementById("new_user_username")?.value || "",
    password: document.getElementById("new_user_password")?.value || "",
    role: document.getElementById("new_user_role")?.value || "viewer",
  };
  const result = await apiJson("/api/users", jsonRequest("POST", payload));
  if (result?.ok) {
    document.getElementById("new_user_username").value = "";
    document.getElementById("new_user_password").value = "";
    document.getElementById("new_user_role").value = "viewer";
    loadUsers();
  }
}

window.addEventListener("DOMContentLoaded", () => {
  loadSessionCard();
  loadUsers();
  document.getElementById("load_users_btn")?.addEventListener("click", loadUsers);
  document.getElementById("create_user_btn")?.addEventListener("click", createUser);
});
