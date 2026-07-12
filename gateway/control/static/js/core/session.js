import { apiJson } from "./api.js";
import { text } from "./dom.js";
import { connectWs } from "./ws.js";

const GATEWAY_TONE_CLASSES = ["r2ctl-status-neutral", "r2ctl-status-ok", "r2ctl-status-warn", "r2ctl-status-danger"];

let _gwWs = null;

export async function logout() {
  await fetch("/api/logout", { method: "POST", credentials: "include" });
  window.location = "/login";
}

export async function loadSessionChrome() {
  const me = await apiJson("/api/me");
  if (me?.data?.username) {
    text("#current_user_chip", `usuario=${me.data.username} perfil=${me.data.role}`);
  }
  try {
    const health = await apiJson("/health");
    if (health?.data?.version) {
      text("#current_version_chip", `v${health.data.version}`);
    }
  } catch (_) { /* ok */ }
  // Carga inicial via REST, depois WebSocket atualiza tudo em tempo real
  await loadGatewayStatusChrome();
  startGatewayStatusWs();
}

export function bindGlobalChrome() {
  const button = document.getElementById("global_logout_btn");
  if (button) {
    button.addEventListener("click", logout);
  }

  // UX: badge do gateway só é link quando fora das páginas /gateway/*
  const chip = document.getElementById("global_gateway_chip");
  if (chip && window.location.pathname.startsWith("/gateway")) {
    chip.removeAttribute("href");
    chip.style.cursor = "default";
    chip.title = "Você já está na página de status do gateway";
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

  const logicalActive = Boolean(result.data.logical_active);
  const running = Boolean(result.data.running);
  const reason = String(result.data.reason || result.data.policy?.reason || "").trim();

  // Gateway ativo logicamente (ativado pelo usuário)
  if (logicalActive) {
    setGatewayTone("r2ctl-status-ok");
    const pid = Number(result.data.pid || 0);
    text("#global_gateway_text", pid > 0 ? `ativo (pid ${pid})` : "ativo");
    return;
  }

  // Gateway inativo mas serviço rodando (ex: máquina local sem ativação)
  if (running) {
    setGatewayTone("r2ctl-status-neutral");
    text("#global_gateway_text", "inativo");
    return;
  }

  // Serviço parado
  if (reason) {
    setGatewayTone("r2ctl-status-danger");
    text("#global_gateway_text", `parado (${reason})`);
    return;
  }

  setGatewayTone("r2ctl-status-danger");
  text("#global_gateway_text", "serviço parado");
}

function startGatewayStatusWs() {
  if (_gwWs) return;
  if (!document.getElementById("global_gateway_chip")) return;
  _gwWs = connectWs("/ws/gateway-status", {
    onMessage: (data) => {
      if (!data || typeof data !== "object") return;
      // Estado lógico do gateway (do banco)
      if (data.logical_active) {
        setGatewayTone("r2ctl-status-ok");
        text("#global_gateway_text", "ativo");
      } else if (!data.running) {
        setGatewayTone("r2ctl-status-danger");
        text("#global_gateway_text", "serviço parado");
      } else {
        setGatewayTone("r2ctl-status-neutral");
        text("#global_gateway_text", "inativo");
      }
    },
    onClose: () => {
      setGatewayTone("r2ctl-status-danger");
      text("#global_gateway_text", "serviço indisponível");
    },
  });
}
