import { apiJson } from "./api.js";
import { text } from "./dom.js";

const GATEWAY_TONE_CLASSES = ["r2ctl-status-neutral", "r2ctl-status-ok", "r2ctl-status-warn", "r2ctl-status-danger"];

let gatewayStatusTimer = null;

export async function logout() {
  await fetch("/api/logout", { method: "POST", credentials: "include" });
  window.location = "/login";
}

export async function loadSessionChrome() {
  const me = await apiJson("/api/me");
  if (me?.data?.username) {
    text("#current_user_chip", `usuario=${me.data.username} perfil=${me.data.role}`);
  }
  await loadGatewayStatusChrome();
  startGatewayStatusPolling();
}

export function bindGlobalChrome() {
  const button = document.getElementById("global_logout_btn");
  if (button) {
    button.addEventListener("click", logout);
  }
}

function setGatewayTone(toneClass) {
  const indicator = document.getElementById("global_gateway_indicator");
  if (!indicator) return;
  indicator.classList.remove(...GATEWAY_TONE_CLASSES);
  indicator.classList.add(toneClass);
}

async function loadGatewayStatusChrome() {
  const chip = document.getElementById("global_gateway_chip");
  if (!chip) return;

  const result = await apiJson("/api/gateway/status");
  if (!result?.data) {
    setGatewayTone("r2ctl-status-danger");
    text("#global_gateway_text", "status indisponivel");
    return;
  }

  const running = Boolean(result.data.running);
  const reason = String(result.data.reason || "").trim();
  const pid = Number(result.data.pid || 0);

  if (running) {
    setGatewayTone("r2ctl-status-ok");
    text("#global_gateway_text", pid > 0 ? `ativo (pid ${pid})` : "ativo");
    return;
  }

  if (reason) {
    setGatewayTone("r2ctl-status-warn");
    text("#global_gateway_text", `inativo (${reason})`);
    return;
  }

  setGatewayTone("r2ctl-status-neutral");
  text("#global_gateway_text", "inativo");
}

function startGatewayStatusPolling() {
  if (gatewayStatusTimer) return;
  if (!document.getElementById("global_gateway_chip")) return;
  gatewayStatusTimer = window.setInterval(() => {
    loadGatewayStatusChrome();
  }, 15000);
}
