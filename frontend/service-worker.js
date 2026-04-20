/* MIO Medic — Service Worker
 * Shell cache para funcionar "instalable" y abrir rápido incluso sin red.
 * Estrategia:
 *   - Install: pre-cache del shell estático (HTML base, CSS, JS, logo, manifest).
 *   - Activate: limpia caches viejas.
 *   - Fetch:
 *       · API/datos dinámicos → red directa (sin cache), así nunca mostramos
 *         turnos/pacientes desactualizados.
 *       · Navegaciones HTML → network-first con fallback al shell cacheado
 *         (permite abrir la app si el dispositivo está offline).
 *       · /static/* y /manifest.webmanifest → cache-first (bundles versionados
 *         por querystring, así se renuevan al cambiar).
 *
 * Para forzar refresh tras un deploy: bumpear CACHE.
 */
const CACHE = "mio-medic-v4-demo";
const SHELL = [
  "/",
  "/login",
  "/static/css/styles.css",
  "/static/js/app.js",
  "/static/js/theme.js",
  "/static/img/logo.png",
  "/manifest.webmanifest",
];

self.addEventListener("install", (e) => {
  e.waitUntil(
    caches
      .open(CACHE)
      .then((c) => c.addAll(SHELL))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    (async () => {
      const keys = await caches.keys();
      await Promise.all(
        keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))
      );
      await self.clients.claim();
    })()
  );
});

// Rutas que NUNCA se cachean (siempre red, datos sensibles o dinámicos).
const DYNAMIC_RE = /^\/(pacientes|turnos|medicos|auth|bloqueos|horarios|especialidades|resumen|health|healthz|2fa|audit|me)\b/;

self.addEventListener("fetch", (e) => {
  const req = e.request;
  const url = new URL(req.url);

  // Solo interceptamos GET mismo origen. Métodos mutantes y terceros → red pura.
  if (req.method !== "GET" || url.origin !== self.location.origin) return;

  // API / datos dinámicos → red directa, sin cache.
  if (DYNAMIC_RE.test(url.pathname)) return;

  // Navegación HTML → network-first con fallback al shell cacheado.
  if (req.mode === "navigate") {
    e.respondWith(
      (async () => {
        try {
          return await fetch(req);
        } catch (_) {
          const cache = await caches.open(CACHE);
          return (
            (await cache.match(req)) ||
            (await cache.match("/")) ||
            Response.error()
          );
        }
      })()
    );
    return;
  }

  // Estáticos → cache-first con actualización diferida.
  if (
    url.pathname.startsWith("/static/") ||
    url.pathname === "/manifest.webmanifest"
  ) {
    e.respondWith(
      (async () => {
        const cache = await caches.open(CACHE);
        const hit = await cache.match(req);
        if (hit) return hit;
        try {
          const resp = await fetch(req);
          if (resp.ok) cache.put(req, resp.clone());
          return resp;
        } catch (_) {
          return hit || Response.error();
        }
      })()
    );
  }
});
