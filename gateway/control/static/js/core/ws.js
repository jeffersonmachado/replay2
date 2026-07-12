/**
 * WebSocket reutilizável com reconexão automática.
 *
 * Uso:
 *   import { connectWs } from "../core/ws.js";
 *   const ws = connectWs("/ws/gateway-status", {
 *     onMessage: (data) => console.log(data),
 *     onOpen: () => console.log("conectado"),
 *     onClose: () => console.log("desconectado"),
 *   });
 *   // depois: ws.close();
 */

let _wsReconnectTimer = null;

/**
 * Cria uma conexão WebSocket com reconexão automática.
 * Retorna { close() } para encerramento limpo.
 */
export function connectWs(path, { onMessage, onOpen, onClose, reconnectMs = 3000 } = {}) {
  let ws = null;
  let closed = false;
  let reconnectTimer = null;

  function scheduleReconnect() {
    if (closed || reconnectTimer) return;
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      if (!closed) doConnect();
    }, reconnectMs);
  }

  function doConnect() {
    if (closed) return;
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${proto}//${location.host}${path}`;
    try {
      ws = new WebSocket(url);
    } catch (_) {
      scheduleReconnect();
      return;
    }
    ws.onopen = () => {
      if (onOpen) onOpen();
    };
    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (onMessage) onMessage(data);
      } catch (_) { /* ignora mensagens malformadas */ }
    };
    ws.onclose = () => {
      if (onClose) onClose();
      ws = null;
      scheduleReconnect();
    };
    ws.onerror = () => {
      ws?.close();
    };
  }

  doConnect();

  return {
    close() {
      closed = true;
      if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
      if (ws) { ws.close(); ws = null; }
    },
  };
}
