// Si ya hay token, redirigir al inicio
if (localStorage.getItem("token")) {
  window.location.href = "/";
}

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
