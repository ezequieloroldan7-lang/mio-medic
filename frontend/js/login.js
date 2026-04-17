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
