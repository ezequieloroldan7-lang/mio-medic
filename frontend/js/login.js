// Si ya hay token, redirigir al inicio
if (localStorage.getItem("token")) {
  window.location.href = "/";
}

// Banner de modo demo: consulta /demo-info; si la instancia es una demo,
// muestra credenciales clickeables que prellenan el formulario.
(async function initDemoBanner() {
  const banner = document.getElementById("demo-banner");
  if (!banner) return;
  try {
    const res = await fetch("/demo-info", { headers: { Accept: "application/json" } });
    if (!res.ok) return;
    const data = await res.json();
    if (!data || !data.demo) return;
    const creds = Array.isArray(data.credenciales) ? data.credenciales : [];
    const escHtml = (s) => String(s).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    }[c]));
    const itemsHtml = creds.map((c) => `
      <li>
        <span>
          <span class="demo-creds-rol">${escHtml(c.rol)}</span>
          &nbsp;<span class="demo-creds-usuario">${escHtml(c.usuario)}</span>
        </span>
        <button type="button" class="demo-cred-btn"
                data-demo-user="${escHtml(c.usuario)}"
                data-demo-pass="${escHtml(c.password)}">
          Usar
        </button>
      </li>
    `).join("");
    banner.innerHTML = `
      <div class="demo-banner-title">🎮 Modo demo</div>
      <div class="demo-banner-sub">${escHtml(data.mensaje || "Datos ficticios.")}</div>
      <ul class="demo-creds">${itemsHtml}</ul>
    `;
    banner.hidden = false;

    // Delegación: cualquier click en un botón con data-demo-user completa el form.
    banner.addEventListener("click", (ev) => {
      const btn = ev.target.closest("[data-demo-user]");
      if (!btn) return;
      const u = btn.getAttribute("data-demo-user") || "";
      const p = btn.getAttribute("data-demo-pass") || "";
      const inpU = document.getElementById("username");
      const inpP = document.getElementById("password");
      if (inpU) inpU.value = u;
      if (inpP) inpP.value = p;
      if (inpP) inpP.focus();
    });
  } catch (_) {
    // Red caída o endpoint ausente → banner queda oculto, sin ruido.
  }
})();

document.getElementById("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const errEl = document.getElementById("error-msg");
  errEl.style.display = "none";

  const username = document.getElementById("username").value.trim();
  const password = document.getElementById("password").value;
  const totpInput = document.getElementById("totp_code");
  const totp_code = totpInput ? totpInput.value.trim() : "";

  if (!username || !password) {
    errEl.textContent = "Completa usuario y contrasena.";
    errEl.style.display = "block";
    return;
  }

  try {
    const body = { username, password };
    if (totp_code) body.totp_code = totp_code;
    const res = await fetch("/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (res.status === 429) {
      throw new Error("Demasiados intentos. Esperá unos minutos y reintentá.");
    }
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || "Error al iniciar sesion");
    }
    const data = await res.json();
    if (data.totp_required) {
      // La contraseña era correcta; falta el código TOTP.
      document.getElementById("totp-group").style.display = "block";
      totpInput.focus();
      errEl.textContent = "Ingresá el código de tu app de autenticación.";
      errEl.style.display = "block";
      return;
    }
    localStorage.setItem("token", data.access_token);
    if (data.refresh_token) localStorage.setItem("refresh_token", data.refresh_token);
    localStorage.setItem("user", JSON.stringify(data.user));
    window.location.href = "/";
  } catch (err) {
    errEl.textContent = err.message;
    errEl.style.display = "block";
  }
});

// Registro del service worker (PWA) también desde el login, para que usuarios
// que nunca llegan al dashboard (primer acceso) igual puedan "instalar" la app.
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker
      .register("/service-worker.js", { scope: "/" })
      .catch((err) => console.warn("SW register fallo:", err));
  });
}
