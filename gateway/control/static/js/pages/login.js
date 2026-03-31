function showMessage(message) {
  const el = document.getElementById("msg");
  if (!el) return;
  if (!message) {
    el.classList.add("hidden");
    el.textContent = "";
    return;
  }
  el.classList.remove("hidden");
  el.textContent = message;
}

async function submitLogin() {
  const username = (document.getElementById("u")?.value || "").trim();
  const password = document.getElementById("p")?.value || "";
  const submit = document.getElementById("loginSubmit");

  if (!username || !password) {
    showMessage("Preencha todos os campos");
    return;
  }

  if (submit) submit.disabled = true;
  showMessage("");
  const response = await fetch("/api/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
    credentials: "include",
  });

  if (submit) submit.disabled = false;

  if (response.status === 200) {
    window.location = "/";
    return;
  }
  showMessage("Usuário ou senha inválidos");
}

window.addEventListener("DOMContentLoaded", () => {
  document.getElementById("loginForm")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    await submitLogin();
  });
});
