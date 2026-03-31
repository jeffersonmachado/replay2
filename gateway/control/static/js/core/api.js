export async function api(path, opts = {}) {
  const resp = await fetch(path, { credentials: "include", ...opts });
  if (resp.status === 401) {
    window.location = "/login";
    return null;
  }
  return resp;
}

export async function apiJson(path, opts = {}) {
  const resp = await api(path, opts);
  if (!resp) {
    return null;
  }
  const text = await resp.text();
  let data = {};
  if (text) {
    try {
      data = JSON.parse(text);
    } catch (_err) {
      data = { raw: text };
    }
  }
  return { ok: resp.ok, status: resp.status, data };
}

export function jsonRequest(method, body) {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  };
}
