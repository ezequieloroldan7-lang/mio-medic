# CLAUDE.md — MIO Medic

Guía rápida para agentes de Claude Code que trabajan en este repo.

## Qué es

Sistema web de gestión de turnos, pacientes y profesionales para una clínica de medicina integral. Interfaz en español. Deploy en Render, un solo servicio FastAPI que sirve API + frontend estático desde el mismo origen.

## Stack

- **Backend**: FastAPI + SQLAlchemy + Pydantic. Scheduler con APScheduler (recordatorios WhatsApp cada hora, backup diario 03:00). Auth por sesión (cookie httpOnly), roles `admin` / `turnos` / `medico`, 2FA TOTP opcional. Audit trail, security headers (CSP script-src `'self'` — nada inline), rate limit, migraciones idempotentes en `_migrate_db()` al startup.
- **DB**: SQLite en dev (`backend/miomedic.db`), Postgres en prod (via `DATABASE_URL`). Toda query debe funcionar en ambos. Idioma SQL: `SUBSTR(col, n+1)` 1-indexed para compatibilidad.
- **Frontend**: HTML + CSS + JS vanilla. **Sin build step, sin framework.** Todo bajo `frontend/`, servido en `/static/*`. CSP strict: **nada de `onclick=` inline ni `<script>` inline** — toda interacción va por delegación `data-action="..."` + `data-id="..."` enrutada en el listener global de `app.js`. Los estilos admiten `style=` inline todavía.
- **PWA**: instalable. `frontend/manifest.webmanifest` + `frontend/service-worker.js`. Datos (`/turnos`, `/pacientes`, `/medicos`, `/auth`, `/bloqueos`, `/horarios`, `/especialidades`, `/resumen`, `/health`, `/2fa`, `/audit`, `/me`) **siempre por red**; el shell se cachea para abrir offline. Para forzar refresh tras deploy: bumpear `CACHE` en `service-worker.js`.

## Archivos clave

- `backend/main.py` — FastAPI app, lifespan, middlewares, mount `/static`, rutas root (`/`, `/login`, `/manifest.webmanifest`, `/service-worker.js`), `_migrate_db()`.
- `backend/routers/{turnos,pacientes,medicos,auth_router}.py` — endpoints REST.
- `backend/models.py`, `backend/schemas.py` — ORM + pydantic.
- `backend/security_headers.py` — CSP y cabeceras.
- `frontend/index.html` — app principal (dashboard, agenda, turnos, pacientes, profesionales).
- `frontend/login.html` — login separado.
- `frontend/js/app.js` — toda la lógica de la SPA. Delegación global en listener `document.click` con `switch(action)`.
- `frontend/js/login.js`, `frontend/js/theme.js`.
- `frontend/css/styles.css` — tokens (light/dark) en `:root` y `[data-theme="dark"]`; clases `.dash-turno-card--{estado}`, `.chip-{estado}`, `.pac-table`, `.turno-chip`, etc.

## Convenciones

- **Textos UX en español** (toast, labels, placeholders, commits). Nombres de variables y comentarios técnicos pueden ir en inglés o español, pero comentarios explicativos importantes están en español.
- **Orden de nombre**: siempre **"Nombre Apellido"** en toda la UI (nunca "Apellido, Nombre"). Primero se tipea el nombre.
- **Mayúsculas** en datos personales (nombre, apellido, deriva, financiador, plan) — inputs usan `style="text-transform:uppercase"` y se normalizan con `.toUpperCase()` antes de POST.
- **Toasts**: `toast(msg, "success"|"error"|"warn")`. Confirmación destructiva: `confirm(msg)`.
- **Lock de submit**: `_withSubmitLock(modalId, asyncFn)` para evitar doble envío.
- **HC auto**: `GET /pacientes/next-hc` devuelve el próximo número; se usa en `abrirNuevoPaciente` y al auto-crear paciente desde "Nuevo Turno".
- **Duplicados**: `_confirmarSiDuplicado({dni, nro_hc, excludeId})` avisa antes de crear si ya existe un paciente con ese DNI o HC.
- **Re-render tras mutación**: después de delete/cancel/move, re-renderizar solo la vista activa — patrón:
  ```js
  renderDashboard();
  if ($("view-turnos")?.classList.contains("active")) renderTurnos();
  if ($("view-agenda")?.classList.contains("active")) renderAgenda();
  ```
- **Agenda**: chips absolutos sobre grilla de slots de `SLOT_MIN = 30` min, de 09:00 a 19:30. Drag vertical (pointer events) mueve el turno dentro del mismo consultorio; backend valida solapamiento y bloqueos. En error revierte con `renderAgenda()`.
- **Dashboard**: el botón "Editar" del card abre un modal compacto `#modal-estado` que **solo cambia el estado** del turno (pendiente/confirmado/ausente/cancelado/realizado). La edición completa sigue solo en la pestaña Turnos.
- **Estados con color**: los cards (`.dash-turno-card--{estado}`) y chips (`.chip-{estado}`) comparten paleta: bg `--{color}-lt`, borde `--{color}`, borde-izquierdo acentuado. Dark mode fuerza `color: var(--text)` en chips para contraste.
- **Ordenamiento**: los selects `filtro-orden-turnos` y `filtro-orden-pacientes` aceptan `{key}_asc|desc`. En turnos, los órdenes distintos de fecha usan `cmpFechaDesc` como tiebreaker. HC en pacientes usa `nro_hc` como key (no `hc`).

## Reglas durables del usuario

- **Siempre mergear directo a `main`** vía squash (preferencia explícita: "margea directo siempre"). No dejar PRs abiertos.
- **Después de squash-merge, la rama queda con SHAs distintos**; para el próximo cambio hacer siempre:
  ```bash
  git fetch origin main
  git reset --hard origin/main
  git cherry-pick <nuevo-sha>
  git push -u origin <branch> --force-with-lease
  ```
  Si se omite, GitHub marca `mergeable_state: dirty`.
- **Branch de trabajo**: `claude/block-slots-cleanup-names-HH20W` (la indicada en el prompt del sistema).
- **No crear PR salvo que se pida**, pero el flujo establecido en este repo es: commit → PR → squash merge → re-sync. Si el usuario pide un cambio concreto, aplica → commit → PR → merge sin preguntar.
- **Usar GitHub MCP tools** (`mcp__github__*`) para PRs/merges. No hay `gh` CLI disponible.
- **No mencionar los reminders** de TodoWrite al usuario.

## Testing / validación

- No hay suite automatizada. Antes de commitear JS: `node -c frontend/js/app.js`.
- Validar JSON: `python3 -c "import json; json.load(open('...'))"`.
- Validar HTML: leer con Read y verificar que no haya tags rotos.
- Cambios UI: describir verificación manual en el PR. Render tarda ~1-2 min en redeployar; hard refresh (Ctrl+Shift+R) necesario.

## Cosas que NO hacer

- No usar `onclick=` inline, `<script>` inline, `eval`, `Function()` — rompe CSP.
- No meter campos nuevos sin agregarlos al modelo + schema + migración idempotente en `_migrate_db()`.
- No asumir Postgres-only ni SQLite-only: usar SQL portable.
- No cachear datos dinámicos en el service worker.
- No remover el flujo explícito de "+ Agregar paciente" sin pensarlo — el usuario lo quiere disponible aunque el auto-create al guardar turno esté implementado.
- No cambiar el orden de Nombre/Apellido a "Apellido, Nombre" — el usuario explícitamente lo revirtió.

## Pendiente

- **Push notifications** (Web Push + VAPID) — el usuario lo difirió.
- **style-src sin 'unsafe-inline'** — queda `style=` inline en varios lugares.
