import { apiJson } from "./api.js";
import { escapeHtml, text } from "./dom.js";

export async function logout() {
  await fetch("/api/logout", { method: "POST", credentials: "include" });
  window.location = "/login";
}

export async function loadSessionChrome() {
  const me = await apiJson("/api/me");
  if (me?.data) {
    text("#current_user_chip", `usuário=${me.data.username} perfil=${me.data.role}`);
  }
}

export function bindGlobalChrome() {
  const button = document.getElementById("global_logout_btn");
  if (button) {
    button.addEventListener("click", logout);
  }
}
