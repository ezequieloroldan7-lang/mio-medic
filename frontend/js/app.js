/* app.js — MIO MEDIC v9 */
const API = "";
const DIAS = ["Lunes","Martes","Miércoles","Jueves","Viernes"];

let medicos = [], especialidades = [], pacientes = [];
let turnoEditing = null, pacienteEditing = null, medicoEditing = null, horarioParaMedicoId = null;

/* Estado de ordenamiento de la tabla de pacientes */
let pacSort = { key: "apellido", dir: "asc" };

/* ── Auth ──────────────────────────────────────────────── */
function _readStoredUser() {
  const raw = localStorage.getItem("user");
  if (!raw) return null;
  try { return JSON.parse(raw); }
  catch (e) {
    console.warn("user en localStorage corrupto, limpiando:", e);
    localStorage.removeItem("user");
    return null;
  }
}
const currentUser = _readStoredUser();
if (!localStorage.getItem("token")) { window.location.href = "/login"; }

const $ = id => document.getElementById(id);

/* ── Escape HTML (anti-XSS) ─────────────────────────────── */
function esc(s) {
  if (s === null || s === undefined) return "";
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/* ── Reloj ──────────────────────────────────────────────── */
function actualizarReloj() {
  const now = new Date();
  $("header-date").textContent = now.toLocaleDateString("es-AR",{weekday:"long",day:"2-digit",month:"long",year:"numeric"});
  $("header-time").textContent = now.toLocaleTimeString("es-AR",{hour:"2-digit",minute:"2-digit",second:"2-digit",hour12:false});
}
actualizarReloj();
setInterval(actualizarReloj, 1000);

/* ── Helpers ────────────────────────────────────────────── */
function fmtHora(dt) { const d=new Date(dt); return String(d.getHours()).padStart(2,"0")+":"+String(d.getMinutes()).padStart(2,"0"); }
function fmtHoraDisplay(dt) { return new Date(dt).toLocaleTimeString("es-AR",{hour:"2-digit",minute:"2-digit",hour12:false}); }
function fmtFecha(dt)       { return new Date(dt).toLocaleDateString("es-AR",{weekday:"long",day:"2-digit",month:"long",year:"numeric"}); }
function fmtFechaCorta(dt)  { return new Date(dt).toLocaleDateString("es-AR"); }

function toast(msg, type="info") {
  const icons = { success: "✓", error: "✕", warning: "⚠", info: "ℹ" };
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  // role=alert para errores (interrumpe), role=status para el resto (polite)
  el.setAttribute("role", type === "error" ? "alert" : "status");
  el.innerHTML = `
    <span class="toast-icon" aria-hidden="true">${icons[type] || icons.info}</span>
    <span class="toast-msg"></span>
    <button class="toast-close" aria-label="Cerrar notificación">×</button>`;
  el.querySelector(".toast-msg").textContent = msg;
  const dismiss = () => {
    if (el._hiding) return; el._hiding = true;
    el.classList.add("hiding");
    setTimeout(() => el.remove(), 200);
  };
  el.querySelector(".toast-close").addEventListener("click", dismiss);
  // Auto-dismiss: más tiempo para errores; pausa al pasar el mouse
  const delay = (type === "error" || type === "warning") ? 6000 : 3500;
  let timer = setTimeout(dismiss, delay);
  el.addEventListener("mouseenter", () => clearTimeout(timer));
  el.addEventListener("mouseleave", () => { timer = setTimeout(dismiss, 2000); });
  $("toast-container").appendChild(el);
}
function logout() {
  localStorage.removeItem("token");
  localStorage.removeItem("refresh_token");
  localStorage.removeItem("user");
  sessionStorage.clear();
  window.location.href="/login";
}

/* ── Breadcrumb contextual en modales ────────────────────── */
const _VIEW_LABELS = {
  "view-dashboard": "Dashboard",
  "view-agenda": "Agenda",
  "view-turnos": "Turnos",
  "view-pacientes": "Pacientes",
  "view-profesionales": "Profesionales",
};
function _activeSection() {
  const active = document.querySelector(".view.active");
  return _VIEW_LABELS[active?.id] || "";
}
function setModalTitle(id, label, crumbOverride) {
  const el = document.getElementById(id);
  if (!el) return;
  const crumb = crumbOverride !== undefined ? crumbOverride : _activeSection();
  el.innerHTML = crumb
    ? `<span class="modal-crumb">${esc(crumb)}</span><span class="modal-crumb-sep">›</span>${esc(label)}`
    : esc(label);
}

/* ── Validación inline de formularios ───────────────────── */
function markFieldError(id, msg) {
  const el = document.getElementById(id); if (!el) return;
  el.classList.add("has-error");
  const group = el.closest(".form-group") || el.parentElement;
  if (!group) return;
  let err = group.querySelector(":scope > .field-error");
  if (!err) {
    err = document.createElement("div");
    err.className = "field-error";
    group.appendChild(err);
  }
  err.textContent = msg;
}
function clearFieldError(el) {
  if (!el) return;
  el.classList.remove("has-error");
  const group = el.closest(".form-group") || el.parentElement;
  const err = group && group.querySelector(":scope > .field-error");
  if (err) err.remove();
}
function clearFormErrors(modalId) {
  const root = document.getElementById(modalId) || document;
  root.querySelectorAll(".has-error").forEach(el => el.classList.remove("has-error"));
  root.querySelectorAll(".field-error").forEach(el => el.remove());
}
function validateRequired(fields) {
  // fields: [{id, msg}]. Returns true si todos OK.
  let firstError = null;
  let ok = true;
  for (const f of fields) {
    const el = document.getElementById(f.id);
    const val = el ? String(el.value || "").trim() : "";
    if (!val) {
      markFieldError(f.id, f.msg || "Este campo es obligatorio");
      if (!firstError) firstError = el;
      ok = false;
    }
  }
  if (firstError) firstError.focus();
  return ok;
}
// Limpia el error del campo al escribir/cambiar
document.addEventListener("input",  e => { if (e.target.classList && e.target.classList.contains("has-error")) clearFieldError(e.target); }, true);
document.addEventListener("change", e => { if (e.target.classList && e.target.classList.contains("has-error")) clearFieldError(e.target); }, true);

// Refresh coalescido: si hay varias requests en vuelo y todas dan 401, solo
// llamamos a /auth/refresh una vez y el resto espera la misma promise.
let _refreshInFlight = null;
async function _tryRefresh() {
  if (_refreshInFlight) return _refreshInFlight;
  const rt = localStorage.getItem("refresh_token");
  if (!rt) return null;
  _refreshInFlight = (async () => {
    try {
      const res = await fetch(API + "/auth/refresh", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: rt }),
      });
      if (!res.ok) return null;
      const data = await res.json();
      if (data.access_token) localStorage.setItem("token", data.access_token);
      if (data.refresh_token) localStorage.setItem("refresh_token", data.refresh_token);
      return data.access_token || null;
    } catch { return null; }
    finally { setTimeout(() => { _refreshInFlight = null; }, 0); }
  })();
  return _refreshInFlight;
}

async function api(path, opts={}) {
  const url = API + path;
  const _doFetch = (tok) => {
    const headers = {"Content-Type":"application/json"};
    if (tok) headers["Authorization"] = "Bearer " + tok;
    return fetch(url, { headers, cache: "no-store", ...opts });
  };
  let token = localStorage.getItem("token");
  let res = await _doFetch(token);
  if (res.status === 401) {
    // Intentamos refrescar el access_token una sola vez y reintentamos.
    const fresh = await _tryRefresh();
    if (fresh) {
      res = await _doFetch(fresh);
    }
    if (res.status === 401) { logout(); return; }
  }
  if(!res.ok){const e=await res.json().catch(()=>({}));throw new Error(e.detail||"Error en el servidor");}
  if(res.status===204)return null; return res.json();
}

/* ── Autocomplete de pacientes ──────────────────────────── */
function initPacienteAutocomplete(inputId, hiddenId) {
  const input  = $(inputId);
  const hidden = $(hiddenId);
  if (!input) return;

  const drop = document.createElement("div");
  drop.id = inputId + "-drop";
  drop.style.cssText = `
    position:absolute; z-index:500; background:#fff; border:1.5px solid var(--primary-lt);
    border-radius:var(--radius); box-shadow:var(--shadow-lg); max-height:260px; overflow-y:auto;
    width:100%; display:none; font-family:Raleway,sans-serif;
  `;
  input.parentElement.style.position = "relative";
  input.parentElement.appendChild(drop);

  function _onSelectPaciente(p) {
    input.value = `${p.nombre} ${p.apellido}`;
    hidden.value = p.id;
    drop.style.display = "none";
    if ($("turno-financiador")) $("turno-financiador").value = p.financiador || "";
    if ($("turno-plan"))        $("turno-plan").value        = p.plan || "";
    if ($("turno-new-nombre"))   $("turno-new-nombre").value   = p.nombre   || "";
    if ($("turno-new-apellido")) $("turno-new-apellido").value = p.apellido || "";
    if ($("turno-new-tel"))      $("turno-new-tel").value      = p.telefono || "";
    if ($("turno-new-email"))    $("turno-new-email").value    = p.email    || "";
    if ($("turno-new-dni"))      $("turno-new-dni").value      = p.dni      || "";
    if ($("turno-new-hc"))       $("turno-new-hc").value       = p.nro_hc   || "";
    if ($("turno-new-deriva"))   $("turno-new-deriva").value   = p.deriva   || "";
    _resetBotonAgregarPaciente();
    if ($("turno-pac-info")) $("turno-pac-info").style.display = "none";
  }

  function renderDrop(lista) {
    if (!lista.length) { drop.style.display="none"; return; }
    drop.innerHTML = lista.map(p => {
      const hc = p.nro_hc ? ` · HC ${esc(p.nro_hc)}` : "";
      const label = `${esc(p.nombre)} ${esc(p.apellido)}`;
      return `<div class="pac-ac-item" data-id="${p.id}" data-label="${label}">
        <span class="pac-ac-nombre">${esc(p.nombre)} ${esc(p.apellido)}</span>
        <span class="pac-ac-hc">${hc}</span>
      </div>`;
    }).join("");
    drop.style.display = "block";
    drop.querySelectorAll(".pac-ac-item").forEach(el => {
      el.addEventListener("mousedown", e => {
        e.preventDefault();
        const p = pacientes.find(x => x.id === parseInt(el.dataset.id));
        if (p) _onSelectPaciente(p);
      });
    });
  }

  // Devuelve el paciente seleccionado actualmente (si hidden.value tiene un id válido)
  function _currentSelection() {
    const id = parseInt(hidden.value, 10);
    if (!id) return null;
    return pacientes.find(x => x.id === id) || null;
  }
  // "¿El texto del input coincide con el paciente seleccionado?"
  function _textMatchesSelection(text) {
    const p = _currentSelection();
    return !!p && `${p.nombre} ${p.apellido}`.trim() === text.trim();
  }

  input.addEventListener("input", function() {
    const raw = this.value.trim();
    // Si el texto aún coincide con la selección vigente, no invalidar: sería
    // un re-dispatch (foco, focus+blur) que pierde el paciente ya elegido y
    // muestra el botón "Agregar paciente" como si fuera uno nuevo.
    if (_textMatchesSelection(raw)) {
      drop.style.display = "none";
      if ($("btn-agregar-paciente")) $("btn-agregar-paciente").style.display = "none";
      return;
    }
    const q = raw.toLowerCase();
    const habiaSeleccion = !!hidden.value;
    hidden.value = "";
    // Si veniamos de una selección válida y el texto ya no la matchea, limpiar
    // los campos autocompletados para no mezclar datos del paciente anterior.
    if (habiaSeleccion) {
      ["turno-new-nombre","turno-new-apellido","turno-new-tel","turno-new-email",
       "turno-new-dni","turno-new-hc","turno-new-deriva"].forEach(id => {
        if ($(id)) $(id).value = "";
      });
    }
    if (!q) { drop.style.display="none"; if($("btn-agregar-paciente"))$("btn-agregar-paciente").style.display="none"; return; }
    // Multi-token: "sanchez valentina" matchea paciente cuyo "apellido nombre"
    // contiene TODOS los tokens. Evita falsos negativos cuando el input
    // muestra la forma concatenada de una selección previa.
    const tokens = q.split(/\s+/).filter(Boolean);
    const filtered = pacientes.filter(p => {
      const hay = `${p.apellido || ""} ${p.nombre || ""} ${p.nro_hc || ""}`.toLowerCase();
      return tokens.every(t => hay.includes(t));
    }).slice(0, 12);
    renderDrop(filtered);
    // Mostrar boton agregar paciente si no hay resultados exactos
    if ($("btn-agregar-paciente")) {
      $("btn-agregar-paciente").style.display = filtered.length === 0 ? "inline-block" : "none";
    }
  });

  input.addEventListener("focus", function() {
    // Solo re-disparar el filtro si el texto NO corresponde a la selección
    // actual (caso: el user dejó el input a medio escribir y vuelve a enfocarlo).
    if (this.value.trim() && !_textMatchesSelection(this.value)) {
      this.dispatchEvent(new Event("input"));
    }
  });

  document.addEventListener("click", e => {
    if (!drop.contains(e.target) && e.target !== input) drop.style.display = "none";
  });
}

/* ── CSS del autocomplete ───────────────────────────────── */
const acStyle = document.createElement("style");
acStyle.textContent = `
  .pac-ac-item { display:flex; align-items:center; justify-content:space-between; padding:.55rem .85rem; cursor:pointer; font-size:.87rem; transition:background .1s; }
  .pac-ac-item:hover { background:var(--accent); }
  .pac-ac-nombre { font-weight:500; color:var(--text); }
  .pac-ac-hc     { font-size:.75rem; color:var(--primary); font-weight:400; opacity:.8; }
  .chip-nombre { font-weight:600; font-size:.8rem; }
  .chip-esp    { font-size:.68rem; opacity:.75; font-weight:300; }
  .chip-hc     { font-size:.67rem; color:var(--primary-dk); opacity:.65; font-weight:400; }
`;
document.head.appendChild(acStyle);

/* ── Navegación ─────────────────────────────────────────── */
function navTo(view) {
  document.querySelectorAll(".view").forEach(v=>v.classList.remove("active"));
  document.querySelectorAll(".nav-item").forEach(n=>{
    n.classList.remove("active");
    n.removeAttribute("aria-current");
  });
  $(view).classList.add("active");
  const navBtn = document.querySelector(`[data-view="${view}"]`);
  if (navBtn) {
    navBtn.classList.add("active");
    navBtn.setAttribute("aria-current", "page");
  }
  _setSidebarOpen(false);
  $("btn-fab").style.display = view==="view-agenda" ? "flex" : "none";
  if(view==="view-agenda")        renderAgenda();
  if(view==="view-pacientes")     renderPacientes();
  if(view==="view-turnos")        renderTurnos();
  if(view==="view-dashboard")     renderDashboard();
  if(view==="view-profesionales") renderProfesionales();
  if(view==="view-audit")         renderAudit();
}
document.querySelectorAll(".nav-item[data-view]").forEach(el=>el.addEventListener("click",()=>navTo(el.dataset.view)));
function _setSidebarOpen(open) {
  const sidebar = document.querySelector(".sidebar");
  const toggle = $("menu-toggle");
  if (!sidebar) return;
  sidebar.classList.toggle("open", open);
  if (toggle) toggle.setAttribute("aria-expanded", String(open));
}
$("menu-toggle").addEventListener("click",()=>{
  const isOpen = document.querySelector(".sidebar").classList.contains("open");
  _setSidebarOpen(!isOpen);
});

/* ── Header / sidebar user actions (CSP strict: sin onclick inline) ── */
$("btn-header-2fa")?.addEventListener("click", () => abrir2FA());
$("btn-header-password")?.addEventListener("click", () => abrirCambiarPassword());
$("btn-header-logout")?.addEventListener("click", () => logout());
$("sidebar-password")?.addEventListener("click", () => {
  abrirCambiarPassword();
  _setSidebarOpen(false);
});
$("sidebar-logout")?.addEventListener("click", () => logout());

/* ── Theme toggle (light/dark) ───────────────────────────────── */
function _updateThemeButtons() {
  if (!window.__theme) return;
  const current = window.__theme.get();
  const isDark = current === "dark";
  const label = isDark ? "Cambiar a modo claro" : "Cambiar a modo oscuro";
  document.querySelectorAll(".btn-theme-toggle").forEach(btn => {
    btn.setAttribute("aria-label", label);
    btn.setAttribute("aria-pressed", String(isDark));
  });
}
function _onThemeToggleClick() {
  if (!window.__theme) return;
  window.__theme.toggle();
}
$("btn-header-theme")?.addEventListener("click", _onThemeToggleClick);
$("sidebar-theme")?.addEventListener("click", _onThemeToggleClick);
if (window.__theme) {
  _updateThemeButtons();
  window.__theme.onChange(_updateThemeButtons);
}
// Cerrar sidebar al tocar fuera (mobile)
document.querySelector(".main").addEventListener("click",()=>_setSidebarOpen(false));

/* ── Modales: cerrar + guardar (CSP strict: sin onclick inline) ── */
document.addEventListener("click", (e) => {
  const el = e.target.closest("[data-close-modal]");
  if (el) cerrarModal(el.dataset.closeModal);
});

/* ── Delegación de acciones en listas dinámicas (CSP strict) ──
 * Cada botón/fila con data-action="..." y data-id="..." se enruta acá.
 * closest() encuentra el elemento más cercano, así que un botón dentro de
 * una tarjeta gana sobre la tarjeta contenedora — no hace falta stopPropagation.
 */
document.addEventListener("click", (e) => {
  const el = e.target.closest("[data-action]");
  if (!el) return;
  const action = el.dataset.action;
  const id = el.dataset.id ? parseInt(el.dataset.id, 10) : null;
  switch (action) {
    case "editar-turno":         abrirEditarTurno(id); break;
    case "cancelar-turno":       cancelarTurno(id); break;
    case "eliminar-turno":       eliminarTurno(id); break;
    case "nuevo-turno-paciente": abrirNuevoTurnoPaciente(id); break;
    case "editar-paciente":      abrirEditarPaciente(id); break;
    case "eliminar-paciente":    eliminarPaciente(id); break;
    case "agregar-horario":      abrirAgregarHorario(id); break;
    case "editar-medico":        abrirEditarMedico(id); break;
    case "eliminar-medico":      eliminarMedico(id); break;
    case "eliminar-horario":     eliminarHorario(id); break;
    case "bloquear-medico":      abrirNuevoBloqueo(id); break;
    case "eliminar-bloqueo":     eliminarBloqueo(id); break;
    case "eliminar-especialidad":eliminarEspecialidad(id); break;
  }
});
$("btn-save-turno")?.addEventListener("click", () => guardarTurno());
$("btn-save-paciente")?.addEventListener("click", () => guardarPaciente());
$("btn-save-medico")?.addEventListener("click", () => guardarMedico());
$("btn-save-horario")?.addEventListener("click", () => guardarHorario());
$("btn-save-bloqueo")?.addEventListener("click", () => guardarBloqueo());
$("btn-nueva-especialidad")?.addEventListener("click", () => abrirNuevaEspecialidad());
$("btn-save-especialidad")?.addEventListener("click", () => guardarEspecialidad());
$("btn-save-password")?.addEventListener("click", () => guardarPassword());
$("btn-2fa-activate")?.addEventListener("click", () => activar2FA());
$("btn-2fa-disable")?.addEventListener("click", () => desactivar2FA());
$("btn-2fa-start")?.addEventListener("click", () => iniciar2FA());
$("btn-agregar-paciente")?.addEventListener("click", () => mostrarCamposPacienteNuevo());

/* ── Filtros persistentes (sessionStorage) ──────────────── */
const _FILTROS = [
  { id: "filtro-fecha",        key: "f_turnos_fecha", evt: "change" },
  { id: "filtro-buscar-turno", key: "f_turnos_q",     evt: "input"  },
  { id: "buscar-paciente",     key: "f_pacientes_q",  evt: "input"  },
  { id: "agenda-fecha",        key: "f_agenda_fecha", evt: "change" },
];
function _restoreFiltros() {
  _FILTROS.forEach(f => {
    const el = $(f.id);
    if (!el) return;
    const v = sessionStorage.getItem(f.key);
    if (v) el.value = v;
    el.addEventListener(f.evt, () => sessionStorage.setItem(f.key, el.value));
  });
}

/* ── Init ───────────────────────────────────────────────── */
async function init() {
  _restoreFiltros();

  // Mostrar nombre de usuario
  if (currentUser) {
    if ($("user-display")) $("user-display").textContent = currentUser.display_name;
    if ($("sidebar-user-name")) $("sidebar-user-name").textContent = currentUser.display_name;
  }

  // Revalidar flag must_change_password desde backend (puede haber cambiado tras reset)
  try {
    const me = await api("/auth/me");
    if (me && currentUser) {
      currentUser.must_change_password = !!me.must_change_password;
      localStorage.setItem("user", JSON.stringify(currentUser));
    }
    if (me && me.must_change_password) {
      _forceChangePassword();
      return; // no cargamos el resto hasta que cambie la clave
    }
  } catch (e) {
    // 401 → /api interceptor hace logout; cualquier otro error no bloquea init
  }

  // Si es medico, ocultar secciones que no le corresponden (pacientes y profesionales).
  if (currentUser && currentUser.role === "medico") {
    document.querySelectorAll('[data-view="view-pacientes"],[data-view="view-profesionales"]').forEach(el=>{
      const li = el.closest("li");
      (li || el).style.display = "none";
    });
    // "+ Nueva" especialidad: el backend bloquea medicos con require_staff,
    // asi que escondemos el boton acá para no ofrecer una acción que igual fallaria.
    const btnNuevaEsp = $("btn-nueva-especialidad");
    if (btnNuevaEsp) btnNuevaEsp.style.display = "none";
  }
  // Las pestañas .admin-only solo son para role="admin". Cualquier otro rol
  // (medico, turnos, o lo que venga) las ve ocultas. El backend igual gatea
  // los endpoints con require_admin — esto es defensa en el UI.
  if (!currentUser || currentUser.role !== "admin") {
    document.querySelectorAll(".admin-only").forEach(el => {
      const li = el.closest("li");
      (li || el).style.display = "none";
    });
  }

  [medicos, especialidades, pacientes] = await Promise.all([api("/medicos"), api("/especialidades"), api("/pacientes")]);
  populateSelects();

  // Si es medico, preseleccionar su profesional en el filtro
  if (currentUser && currentUser.role === "medico" && currentUser.medico_id) {
    const selMed = $("turno-medico");
    if (selMed) selMed.value = currentUser.medico_id;
  }
  initPacienteAutocomplete("turno-paciente-input","turno-paciente-id");

  // Mensaje de bienvenida para profesionales
  if (_isMedico && !sessionStorage.getItem("welcome_shown")) {
    const m = medicos.find(x => x.id === currentUser.medico_id);
    const nombre = m ? `${m.nombre} ${m.apellido}` : currentUser.display_name;
    toast(`Bienvenido/a ${nombre} ❤️`, "success");
    sessionStorage.setItem("welcome_shown", "1");
  }

  renderDashboard();
}

function populateSelects() {
  const mOpts = medicos.map(m=>`<option value="${m.id}">${esc(m.nombre)} ${esc(m.apellido)} — ${esc(m.especialidad?.nombre||"")}</option>`).join("");
  document.querySelectorAll(".sel-medico").forEach(s=>s.innerHTML=`<option value="">Seleccioná profesional</option>`+mOpts);
  const eOpts = especialidades.map(e=>`<option value="${e.id}">${esc(e.nombre)}</option>`).join("");
  document.querySelectorAll(".sel-especialidad").forEach(s=>s.innerHTML=`<option value="">Seleccioná especialidad</option>`+eOpts);
  _populateFinanciadores();
}

/* ── Autocomplete financiador / plan ────────────────────── */
const _FINANCIADORES_COMUNES = [
  "PARTICULAR","OSDE","SWISS MEDICAL","GALENO","MEDIFE","OMINT",
  "SANCOR SALUD","ACCORD SALUD","PREVENCION SALUD","MEDICUS",
  "HOSPITAL ITALIANO","HOSPITAL ALEMAN","HOSPITAL BRITANICO",
  "PAMI","IOMA","OSPLAD","OSECAC","OSDEPYM","UNION PERSONAL",
  "FEDERADA SALUD","APROSS","WILLIAM HOPE","JERARQUICOS SALUD",
  "LUIS PASTEUR","AVALIAN","QUALITAS","DOCTHOS","SCIS","SADAIC",
];
const _PLANES_COMUNES = [
  "210","310","410","450","510","710","910",
  "SB01","SB03","SB04","SB06","SMG01","SMG02","SMG03",
  "AZUL","PLATA","ORO","BLACK","110","220","330","440",
  "CLASSIC","PLUS","PREMIUM",
  "1000","1500","2500","3500","4500",
  "A2","A3","A4",
];
function _populateFinanciadores() {
  const fin = new Set(_FINANCIADORES_COMUNES);
  const pla = new Set(_PLANES_COMUNES);
  (pacientes || []).forEach(p => {
    if (p.financiador) fin.add(p.financiador.toUpperCase().trim());
    if (p.plan)        pla.add(p.plan.toUpperCase().trim());
  });
  const dlFin = $("datalist-financiadores");
  const dlPla = $("datalist-planes");
  if (dlFin) dlFin.innerHTML = [...fin].sort().map(v => `<option value="${esc(v)}">`).join("");
  if (dlPla) dlPla.innerHTML = [...pla].sort().map(v => `<option value="${esc(v)}">`).join("");
}

/* ── Filtro por rol ─────────────────────────────────────── */
const _isMedico = currentUser && currentUser.role === "medico" && currentUser.medico_id;
function _filtrarPorRol(turnos) {
  if (!_isMedico) return turnos;
  return turnos.filter(t => t.medico_id === currentUser.medico_id);
}

/* ── Dashboard ──────────────────────────────────────────── */
async function renderDashboard() {
  try {
    const todos_raw = await api(`/turnos?fecha=${new Date().toISOString().slice(0,10)}`);
    const todos = _filtrarPorRol(todos_raw);

    const cnt = (estado) => todos.filter(t => t.estado === estado).length;
    $("dash-hoy").textContent = todos.length;
    $("dash-pendientes").textContent = cnt("pendiente");
    $("dash-confirmados").textContent = cnt("confirmado");
    $("dash-ausentes").textContent = cnt("ausente") + cnt("cancelado");
    if ($("dash-realizados")) $("dash-realizados").textContent = cnt("realizado");

    $("dash-proximos").innerHTML = todos.length===0
      ? `<div style="text-align:center;color:var(--muted);padding:2rem;font-size:.85rem">Sin turnos para hoy</div>`
      : todos.map(t => {
          const p = t.paciente;
          const obs = t.observaciones ? `<div class="dash-turno-obs">${esc(t.observaciones)}</div>` : "";
          const info = [];
          if (p?.financiador) info.push(esc(p.financiador) + (p.plan ? " — " + esc(p.plan) : ""));
          if (p?.telefono) info.push(esc(p.telefono));
          const infoHtml = info.length ? `<div class="dash-turno-info">${info.map(i=>`<span>${i}</span>`).join("")}</div>` : "";
          return `<div class="dash-turno-card" data-action="editar-turno" data-id="${t.id}">

            <span class="dash-turno-hora">${fmtHoraDisplay(t.fecha_hora_inicio)}</span>
            <span class="dash-turno-paciente">${esc(p?.nombre)} ${esc(p?.apellido)}</span>
            <span class="dash-turno-consultorio">C${t.consultorio}</span>
            <span class="dash-turno-medico">${esc(t.medico?.nombre)} ${esc(t.medico?.apellido)}</span>
            <span class="badge badge-${t.estado}">${t.estado}</span>
            <span class="dash-turno-actions"><button class="btn btn-sm btn-outline" data-action="editar-turno" data-id="${t.id}">Editar</button></span>
            ${infoHtml}
            ${obs}
          </div>`;
        }).join("");
  } catch(e){toast("Error al cargar dashboard: "+e.message,"error");}
}

/* ── Agenda ─────────────────────────────────────────────── */
async function renderAgenda() {
  const fecha=$("agenda-fecha").value||new Date().toISOString().slice(0,10);
  $("agenda-fecha").value=fecha;
  sessionStorage.setItem("f_agenda_fecha", fecha);
  $("agenda-titulo").textContent=fmtFecha(fecha+"T12:00:00");
  const [turnos_raw, bloqueos_raw] = await Promise.all([
    api(`/turnos?fecha=${fecha}`),
    api(`/bloqueos?fecha=${fecha}`).catch(() => []),
  ]);
  const turnos=_filtrarPorRol(turnos_raw);
  const activos=turnos.filter(t=>t.estado!=="cancelado");
  // La agenda mezcla turnos de todos los profesionales en la misma grilla, así
  // que un bloqueo solo debe "cubrir" slots en la vista del propio profesional
  // (rol medico). Para staff mostramos un resumen al costado — el backend
  // valida al crear el turno si el profesional elegido está bloqueado.
  const bloqueosMios = _isMedico
    ? bloqueos_raw.filter(b => b.medico_id === currentUser.medico_id)
    : [];
  renderColumna(1,activos.filter(t=>t.consultorio===1),fecha,bloqueosMios);
  renderColumna(2,activos.filter(t=>t.consultorio===2),fecha,bloqueosMios);
  _renderBanderaBloqueos(_isMedico ? [] : bloqueos_raw);
}

function _renderBanderaBloqueos(bloqueos) {
  const host = $("agenda-bloqueos-info");
  if (!host) return;
  if (!bloqueos || !bloqueos.length) { host.innerHTML = ""; host.style.display = "none"; return; }
  const porMedico = new Map();
  for (const b of bloqueos) {
    const arr = porMedico.get(b.medico_id) || [];
    arr.push(b); porMedico.set(b.medico_id, arr);
  }
  const pills = [];
  for (const [medicoId, lista] of porMedico) {
    const m = medicos.find(x => x.id === medicoId);
    if (!m) continue;
    const rangos = lista.map(b => _fmtRangoBloqueo(b)).join(", ");
    pills.push(`<span class="horario-pill bloqueo-pill" title="${esc(lista.map(b => b.motivo || 'Bloqueo').join(' · '))}">
      ${esc(m.nombre)} ${esc(m.apellido)} · ${esc(rangos)}</span>`);
  }
  host.innerHTML = pills.length
    ? `<div style="font-size:.72rem;color:var(--muted);margin-right:.5rem">Bloqueos del día:</div>${pills.join("")}`
    : "";
  host.style.display = pills.length ? "flex" : "none";
}

const SLOT_MIN = 30;  // minutos por slot

function horasDisponibles() {
  const h = [];
  for (let hr = 9; hr < 20; hr++) {
    for (let m = 0; m < 60; m += SLOT_MIN) {
      if (hr === 19 && m > 30) break;
      h.push(`${String(hr).padStart(2,"0")}:${String(m).padStart(2,"0")}`);
    }
  }
  return h;
}

function _slotIndexDeHora(hora) {
  // "09:00" → 0, "09:15" → 1, ...
  const [h, m] = hora.split(":").map(Number);
  const base = 9 * 60;
  return Math.round((h * 60 + m - base) / SLOT_MIN);
}

function _bloqueoTramoEnFecha(b, fecha) {
  // Devuelve {idxIni, spans} del rango del bloqueo que cae en `fecha` (YYYY-MM-DD),
  // clampeado a la grilla 09:00–20:00. Null si no hay intersección visible.
  const dayStart = new Date(fecha + "T00:00:00");
  const dayEnd   = new Date(fecha + "T23:59:59");
  const bIni = new Date(b.fecha_inicio);
  const bFin = new Date(b.fecha_fin);
  if (bFin <= dayStart || bIni > dayEnd) return null;
  const clampIni = bIni < dayStart ? new Date(fecha + "T09:00:00") : bIni;
  const clampFin = bFin > dayEnd   ? new Date(fecha + "T20:00:00") : bFin;
  const minutesIni = clampIni.getHours() * 60 + clampIni.getMinutes();
  const minutesFin = clampFin.getHours() * 60 + clampFin.getMinutes();
  const gridStart = 9 * 60;
  const gridEnd   = 19 * 60 + 30 + SLOT_MIN;
  const ini = Math.max(minutesIni, gridStart);
  const fin = Math.min(minutesFin, gridEnd);
  if (fin <= ini) return null;
  const idxIni = Math.floor((ini - gridStart) / SLOT_MIN);
  const spans  = Math.max(1, Math.ceil((fin - ini) / SLOT_MIN));
  return { idxIni, spans };
}

function renderColumna(consultorio, turnos, fecha, bloqueos = []) {
  const grid = $(`grid-c${consultorio}`);
  grid.innerHTML = "";
  const horas = horasDisponibles();
  const total = horas.length;

  // Calcular qué slots quedan cubiertos por cada turno
  const cubiertos = new Set();       // índices cubiertos (pero NO el inicial)
  const turnosPorSlot = new Map();   // slotIdx inicial → turno
  for (const t of turnos) {
    const hIni = fmtHora(t.fecha_hora_inicio);
    const idx = _slotIndexDeHora(hIni);
    if (idx < 0 || idx >= total) continue;
    const spans = Math.max(1, Math.ceil((t.duracion_minutos || SLOT_MIN) / SLOT_MIN));
    turnosPorSlot.set(idx, { t, spans });
    for (let i = idx + 1; i < Math.min(total, idx + spans); i++) cubiertos.add(i);
  }

  // Índice de slots bloqueados (se aplica a cualquier consultorio del profesional)
  const bloqueados = new Set();
  const bloqueosVisibles = [];
  for (const b of bloqueoSinDuplicar(bloqueos)) {
    const tramo = _bloqueoTramoEnFecha(b, fecha);
    if (!tramo) continue;
    bloqueosVisibles.push({ b, ...tramo });
    for (let i = tramo.idxIni; i < Math.min(total, tramo.idxIni + tramo.spans); i++) {
      bloqueados.add(i);
    }
  }

  // Render de los slots (siempre todos, para mantener la grilla horaria)
  horas.forEach((hora, i) => {
    const esExacta = hora.endsWith(":00");
    const slot = document.createElement("div");
    slot.className = "time-slot"
      + (esExacta ? " slot-exacta" : "")
      + (cubiertos.has(i) ? " slot-cubierto" : "")
      + (bloqueados.has(i) ? " slot-bloqueado" : "");
    slot.innerHTML = `<span class="time-label${esExacta ? " exacta" : ""}">${hora}</span><span class="time-content"></span>`;
    if (!turnosPorSlot.has(i) && !cubiertos.has(i) && !bloqueados.has(i)) {
      slot.addEventListener("click", () => abrirNuevoTurno(consultorio, `${fecha}T${hora}`));
    }
    grid.appendChild(slot);
  });

  // Chip overlay por cada bloqueo visible (debajo de los turnos en z-order)
  for (const { b, idxIni, spans } of bloqueosVisibles) {
    const chip = document.createElement("div");
    chip.className = "turno-chip chip-bloqueado";
    chip.style.top = `calc(${idxIni} * var(--slot-h) + 3px)`;
    chip.style.height = `calc(${spans} * var(--slot-h) - 6px)`;
    const motivo = b.motivo ? ` · ${esc(b.motivo)}` : "";
    chip.innerHTML = `<span class="chip-nombre">BLOQUEADO${motivo}</span>
      <span class="chip-hc">${esc(_fmtRangoBloqueo(b))}</span>`;
    chip.title = "Profesional no disponible en este rango. Click para eliminar.";
    chip.addEventListener("click", e => { e.stopPropagation(); eliminarBloqueo(b.id); });
    grid.appendChild(chip);
  }

  // Render de los chips como overlays absolutos encima de la grilla
  for (const [idx, { t, spans }] of turnosPorSlot) {
    const chip = document.createElement("div");
    chip.className = `turno-chip chip-${t.estado}`;
    chip.style.top = `calc(${idx} * var(--slot-h) + 3px)`;
    chip.style.height = `calc(${spans} * var(--slot-h) - 6px)`;
    chip.innerHTML = chipInnerHTML(t);
    chip.addEventListener("click", e => { e.stopPropagation(); abrirEditarTurno(t.id); });
    grid.appendChild(chip);
  }
}

function bloqueoSinDuplicar(arr) {
  // Un mismo bloqueo aparece idéntico en ambos consultorios; deduplicamos por id.
  const seen = new Set();
  return arr.filter(b => {
    if (seen.has(b.id)) return false;
    seen.add(b.id);
    return true;
  });
}

function chipInnerHTML(t) {
  const hc   = t.paciente?.nro_hc ? `HC ${esc(t.paciente.nro_hc)}` : "";
  const prof = esc(t.medico?.apellido || "");
  const esp  = esc(t.medico?.especialidad?.nombre || "");
  const hIni = fmtHora(t.fecha_hora_inicio);
  const hFin = (() => {
    const d = new Date(t.fecha_hora_inicio);
    d.setMinutes(d.getMinutes() + (t.duracion_minutos || 0));
    return `${String(d.getHours()).padStart(2,"0")}:${String(d.getMinutes()).padStart(2,"0")}`;
  })();
  const obs = t.observaciones
    ? `<span class="chip-obs" title="${esc(t.observaciones)}">📝 ${esc(t.observaciones)}</span>`
    : "";
  return `
    <span class="chip-nombre">${esc(t.paciente?.nombre)} ${esc(t.paciente?.apellido)}</span>
    <span class="chip-hc">${hc}${hc ? " · " : ""}${hIni}–${hFin}</span>
    <span class="chip-esp">${prof}${prof && esp ? " · " : ""}${esp}</span>
    ${obs}`;
}

$("agenda-fecha").addEventListener("change",renderAgenda);
$("btn-agenda-hoy").addEventListener("click",()=>{$("agenda-fecha").value=new Date().toISOString().slice(0,10);renderAgenda();});
$("btn-agenda-prev").addEventListener("click",()=>{const d=new Date($("agenda-fecha").value+"T12:00:00");d.setDate(d.getDate()-1);$("agenda-fecha").value=d.toISOString().slice(0,10);renderAgenda();});
$("btn-agenda-next").addEventListener("click",()=>{const d=new Date($("agenda-fecha").value+"T12:00:00");d.setDate(d.getDate()+1);$("agenda-fecha").value=d.toISOString().slice(0,10);renderAgenda();});

/* ── Swipe en agenda para cambiar de día (mobile) ─────── */
(function(){
  const el=document.querySelector(".cal-cols"); if(!el) return;
  let startX=0, startY=0;
  el.addEventListener("touchstart",e=>{startX=e.touches[0].clientX;startY=e.touches[0].clientY;},{passive:true});
  el.addEventListener("touchend",e=>{
    const dx=e.changedTouches[0].clientX-startX;
    const dy=e.changedTouches[0].clientY-startY;
    if(Math.abs(dx)<60||Math.abs(dy)>Math.abs(dx))return;
    const d=new Date($("agenda-fecha").value+"T12:00:00");
    d.setDate(d.getDate()+(dx<0?1:-1));
    $("agenda-fecha").value=d.toISOString().slice(0,10);
    renderAgenda();
  },{passive:true});
})();

/* ── Turnos ─────────────────────────────────────────────── */
async function renderTurnos(q) {
  if (q === undefined) q = $("filtro-buscar-turno")?.value || "";
  const fecha=$("filtro-fecha")?.value||"";
  const turnos_raw=await api("/turnos?"+(fecha?`fecha=${fecha}&`:""));
  const turnos=_filtrarPorRol(turnos_raw);
  const filtrados=q?turnos.filter(t=>`${t.paciente?.apellido} ${t.paciente?.nombre}`.toLowerCase().includes(q.toLowerCase())):turnos;
  const orden=$("filtro-orden-turnos")?.value||"fecha_desc";
  const cmpStr=(a,b)=>(a||"").toString().localeCompare((b||"").toString(),"es",{sensitivity:"base"});
  const cmpHc=(a,b)=>{
    const na=parseInt(a,10), nb=parseInt(b,10);
    const aNum=!isNaN(na), bNum=!isNaN(nb);
    if(aNum && bNum) return na-nb;
    if(aNum) return -1;
    if(bNum) return 1;
    return cmpStr(a,b);
  };
  // Desempate por fecha (más reciente primero) cuando el criterio primario
  // no distingue — p.ej. dos turnos del mismo paciente quedan en orden cronológico.
  const cmpFechaDesc = (a,b) => (b.fecha_hora_inicio||"").localeCompare(a.fecha_hora_inicio||"");
  const f=[...filtrados].sort((a,b)=>{
    const pa=a.paciente||{}, pb=b.paciente||{};
    let primary;
    switch(orden){
      case "fecha_asc":     return (a.fecha_hora_inicio||"").localeCompare(b.fecha_hora_inicio||"");
      case "fecha_desc":    return cmpFechaDesc(a,b);
      case "apellido_asc":  primary = cmpStr(pa.apellido, pb.apellido) || cmpStr(pa.nombre, pb.nombre); break;
      case "apellido_desc": primary = cmpStr(pb.apellido, pa.apellido) || cmpStr(pb.nombre, pa.nombre); break;
      case "nombre_asc":    primary = cmpStr(pa.nombre, pb.nombre)     || cmpStr(pa.apellido, pb.apellido); break;
      case "nombre_desc":   primary = cmpStr(pb.nombre, pa.nombre)     || cmpStr(pb.apellido, pa.apellido); break;
      case "hc_asc":        primary = cmpHc(pa.nro_hc, pb.nro_hc); break;
      case "hc_desc":       primary = cmpHc(pb.nro_hc, pa.nro_hc); break;
      default:              return cmpFechaDesc(a,b);
    }
    return primary !== 0 ? primary : cmpFechaDesc(a,b);
  });
  $("tabla-turnos").innerHTML=f.length===0
    ?`<div style="text-align:center;color:var(--muted);padding:2rem">Sin turnos</div>`
    :f.map(t=>{
      const p=t.paciente;
      const obs=t.observaciones?`<div class="dash-turno-obs">${esc(t.observaciones)}</div>`:"";
      const info=[];
      if(p?.nro_hc)info.push(`HC: ${esc(p.nro_hc)}`);
      if(p?.dni)info.push(`DNI: ${esc(p.dni)}`);
      if(p?.telefono)info.push(`WhatsApp: ${esc(p.telefono)}`);
      if(p?.financiador)info.push(esc(p.financiador)+(p.plan?" — "+esc(p.plan):""));
      if(p?.email)info.push(esc(p.email));
      const infoHtml=info.length?`<div class="dash-turno-info">${info.map(i=>`<span>${i}</span>`).join("")}</div>`:"";
      return `<div class="dash-turno-card" data-action="editar-turno" data-id="${t.id}">
        <span class="dash-turno-hora">${fmtFechaCorta(t.fecha_hora_inicio)} ${fmtHoraDisplay(t.fecha_hora_inicio)}</span>
        <span class="dash-turno-paciente">${esc(p?.nombre)} ${esc(p?.apellido)}</span>
        <span class="dash-turno-consultorio">C${t.consultorio}</span>
        <span class="dash-turno-medico">${esc(t.medico?.nombre)} ${esc(t.medico?.apellido)} — ${esc(t.medico?.especialidad?.nombre||"")}</span>
        <span class="badge badge-${t.estado}">${t.estado}</span>
        <span class="dash-turno-actions">
          <button class="btn btn-sm btn-outline" data-action="editar-turno" data-id="${t.id}">Editar</button>
          <button class="btn btn-sm btn-outline" data-action="cancelar-turno" data-id="${t.id}" style="color:var(--warning);border-color:var(--warning)">Cancelar</button>
          <button class="btn btn-sm btn-danger" data-action="eliminar-turno" data-id="${t.id}">Eliminar</button>
        </span>
        ${infoHtml}
        ${obs}
      </div>`;
    }).join("");
}
$("filtro-fecha")?.addEventListener("change",()=>renderTurnos());
$("filtro-buscar-turno")?.addEventListener("input",e=>renderTurnos(e.target.value));
$("filtro-orden-turnos")?.addEventListener("change",()=>renderTurnos());

/* ── Exportar Excel (XLSX) ──────────────────────────────── */
async function exportarTurnosXLSX() {
  const desde = prompt("Desde (YYYY-MM-DD). Dejar vacío para últimos 30 días:") || "";
  const hasta = prompt("Hasta (YYYY-MM-DD). Dejar vacío para hoy:") || "";
  const qs = [];
  if (desde) qs.push("desde=" + encodeURIComponent(desde));
  if (hasta) qs.push("hasta=" + encodeURIComponent(hasta));
  const url = "/turnos/export.xlsx" + (qs.length ? ("?" + qs.join("&")) : "");
  try {
    const token = localStorage.getItem("token");
    const res = await fetch(url, {
      headers: token ? { Authorization: "Bearer " + token } : {},
    });
    if (res.status === 401) { logout(); return; }
    if (!res.ok) throw new Error("Error " + res.status);
    const blob = await res.blob();
    const a = document.createElement("a");
    const blobUrl = URL.createObjectURL(blob);
    a.href = blobUrl;
    a.download = `turnos_${desde || "ultimos30"}_${hasta || "hoy"}.xlsx`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(blobUrl), 1000);
  } catch (e) {
    toast("No se pudo exportar Excel: " + e.message, "error");
  }
}

/* ── Pacientes ──────────────────────────────────────────── */
function _pacVal(p, key) {
  const v = p[key];
  if (v === null || v === undefined || v === "") return null;
  if (key === "nro_hc") {
    const n = parseInt(v, 10);
    return isNaN(n) ? null : n;
  }
  return v.toString().toLowerCase();
}

function _pacCmp(a, b, key, dir) {
  const av = _pacVal(a, key);
  const bv = _pacVal(b, key);
  // Vacíos siempre al final, sin importar dirección
  if (av === null && bv === null) return 0;
  if (av === null) return 1;
  if (bv === null) return -1;
  let base;
  if (typeof av === "number") base = av - bv;
  else                        base = av.localeCompare(bv, "es");
  return dir === "desc" ? -base : base;
}

function _ordenarPacientes(lista) {
  const copia = lista.slice();
  copia.sort((a, b) => {
    const base = _pacCmp(a, b, pacSort.key, pacSort.dir);
    if (base === 0 && pacSort.key !== "apellido") {
      return _pacCmp(a, b, "apellido", "asc");
    }
    return base;
  });
  return copia;
}

function _updateSortHeaders() {
  const thead = $("tabla-pacientes-tabla");
  if (!thead) return;
  thead.querySelectorAll("th.sortable").forEach(th => {
    const active = th.dataset.sort === pacSort.key;
    th.classList.toggle("sort-active", active);
    const arrow = th.querySelector(".sort-arrow");
    if (arrow) arrow.textContent = active ? (pacSort.dir === "asc" ? "↑" : "↓") : "↕";
  });
}

async function renderPacientes(q) {
  if (q === undefined) q = $("buscar-paciente")?.value || "";
  try {
    const lista = await api(q ? `/pacientes?q=${encodeURIComponent(q)}` : "/pacientes");
    const ordenados = _ordenarPacientes(lista);
    $("pacientes-count").textContent = `${lista.length} pacientes`;
    const queryRaw = q.trim();
    const queryEsc = esc(queryRaw);
    const emptyMsg = queryRaw
      ? `<div style="text-align:center;color:var(--muted);padding:2rem">
           <div style="margin-bottom:.75rem">No se encontró ningún paciente con <strong>"${queryEsc}"</strong>.</div>
           <button class="btn btn-primary btn-sm" id="btn-crear-paciente-vacio">+ Crear paciente "${queryEsc}"</button>
         </div>`
      : `<div style="text-align:center;color:var(--muted);padding:2rem">
           <div style="margin-bottom:.75rem">No hay pacientes todavía.</div>
           <button class="btn btn-primary btn-sm" id="btn-crear-paciente-vacio">+ Agregar primer paciente</button>
         </div>`;
    $("tabla-pacientes").innerHTML = ordenados.length === 0
      ? emptyMsg
      : ordenados.map(p => {
          const info = [];
          if (p.nro_hc) info.push(`HC: ${esc(p.nro_hc)}`);
          if (p.dni) info.push(`DNI: ${esc(p.dni)}`);
          if (p.telefono) info.push(esc(p.telefono));
          if (p.email) info.push(esc(p.email));
          if (p.financiador) info.push(esc(p.financiador) + (p.plan ? " — " + esc(p.plan) : ""));
          const infoStr = info.length ? `<span style="font-size:.78rem;color:var(--muted);display:inline-flex;gap:.6rem;flex-wrap:wrap">${info.map(i=>`<span>${i}</span>`).join("")}</span>` : "";
          return `<div class="dash-turno-card pac-card" style="align-items:center">
            <span class="dash-turno-paciente" style="font-weight:600">${esc(p.nombre)} ${esc(p.apellido)}</span>
            ${infoStr}
            <span class="pac-actions" style="margin-left:auto;display:flex;gap:.35rem;flex-shrink:0">
              <button class="btn btn-sm btn-primary" data-action="nuevo-turno-paciente" data-id="${p.id}">Turno</button>
              <button class="btn btn-sm btn-outline" data-action="editar-paciente" data-id="${p.id}">Editar</button>
              <button class="btn btn-sm btn-danger" data-action="eliminar-paciente" data-id="${p.id}">Eliminar</button>
            </span>
          </div>`;
        }).join("");
    if (ordenados.length === 0) {
      const btn = document.getElementById("btn-crear-paciente-vacio");
      if (btn) btn.addEventListener("click", () => abrirNuevoPaciente(queryRaw));
    }
  } catch (e) { toast("Error al cargar pacientes: " + e.message, "error"); }
}
$("buscar-paciente").addEventListener("input", e => renderPacientes(e.target.value));
$("filtro-orden-pacientes")?.addEventListener("change", e => {
  const v = e.target.value || "apellido_asc";
  const i = v.lastIndexOf("_");
  pacSort = { key: v.slice(0, i), dir: v.slice(i + 1) };
  renderPacientes();
});

/* ── Profesionales ──────────────────────────────────────── */
async function renderProfesionales() {
  try {
    const hoy = new Date().toISOString().slice(0,10);
    const [meds, turnosHoy, bloqueosAll] = await Promise.all([
      api("/medicos"),
      api(`/turnos?fecha=${hoy}`).catch(() => []),
      api(`/bloqueos?fecha=${hoy}`).catch(() => []),
    ]);
    medicos = meds;
    const turnosPorMedico = {};
    turnosHoy.filter(t => t.estado !== "cancelado").forEach(t => {
      turnosPorMedico[t.medico_id] = (turnosPorMedico[t.medico_id] || 0) + 1;
    });

    // Próximos bloqueos (hasta 30 días) por médico, en paralelo
    const limiteFin = new Date(); limiteFin.setDate(limiteFin.getDate() + 30);
    const hastaISO = limiteFin.toISOString().slice(0,10);
    const bloqueosPorMedico = {};
    await Promise.all(medicos.map(async m => {
      try {
        bloqueosPorMedico[m.id] = await api(
          `/medicos/${m.id}/bloqueos?desde=${hoy}&hasta=${hastaISO}`
        );
      } catch { bloqueosPorMedico[m.id] = []; }
    }));

    const grid = $("prof-grid");
    if (!medicos.length) {
      grid.innerHTML = `<div class="empty-state"><span class="empty-state-icon">✦</span>No hay profesionales registrados</div>`;
      return;
    }
    grid.innerHTML = medicos.map(m => {
      const iniciales = ((m.nombre[0] || "") + (m.apellido[0] || "")).toUpperCase();
      const n = turnosPorMedico[m.id] || 0;
      const badge = n > 0
        ? `<span class="prof-turnos-hoy" title="Turnos activos hoy"><span class="prof-turnos-num">${n}</span> ${n === 1 ? "turno" : "turnos"} hoy</span>`
        : `<span class="prof-turnos-hoy prof-turnos-vacio" title="Sin turnos para hoy">Sin turnos hoy</span>`;
      const infoRows = [];
      if (m.matricula)          infoRows.push(`<div class="prof-info-row"><span class="prof-info-icon">◉</span><span>Mat. ${esc(m.matricula)}</span></div>`);
      if (m.telefono)           infoRows.push(`<div class="prof-info-row"><span class="prof-info-icon">☏</span><span>${esc(m.telefono)}</span></div>`);
      if (m.email)              infoRows.push(`<div class="prof-info-row"><span class="prof-info-icon">✉</span><span>${esc(m.email)}</span></div>`);
      if (m.google_calendar_id) infoRows.push(`<div class="prof-info-row"><span class="prof-info-icon">✓</span><span style="color:var(--success);font-size:.72rem">Google Calendar sincronizado</span></div>`);

      return `
        <div class="prof-card">
          <div class="prof-head">
            <div class="prof-avatar">${esc(iniciales)}</div>
            <div class="prof-headinfo">
              <div class="prof-nombre">${esc(m.nombre)} ${esc(m.apellido)}</div>
              <div class="prof-esp">${esc(m.especialidad?.nombre || "Sin especialidad")}</div>
            </div>
            ${badge}
          </div>
          ${infoRows.length ? `<div class="prof-info">${infoRows.join("")}</div>` : ""}
          <div class="prof-section">
            <div class="prof-section-label">Horarios de atención</div>
            <div class="horario-list">${renderHorariosPills(m.horarios || [], m.id)}</div>
          </div>
          <div class="prof-section">
            <div class="prof-section-label">Bloqueos próximos</div>
            <div class="horario-list">${renderBloqueosPills(bloqueosPorMedico[m.id] || [])}</div>
          </div>
          <div class="prof-actions">
            <button class="btn btn-sm btn-outline" data-action="agregar-horario" data-id="${m.id}">+ Horario</button>
            <button class="btn btn-sm btn-outline" data-action="bloquear-medico" data-id="${m.id}">+ Bloquear</button>
            <button class="btn btn-sm btn-outline" data-action="editar-medico" data-id="${m.id}">Editar</button>
            <button class="btn btn-sm btn-danger" data-action="eliminar-medico" data-id="${m.id}">Eliminar</button>
          </div>
        </div>`;
    }).join("");
  } catch (e) { toast("Error al cargar profesionales: " + e.message, "error"); }
}

function renderBloqueosPills(bloqueos) {
  if (!bloqueos || !bloqueos.length) {
    return `<span style="font-size:.75rem;color:var(--muted);font-style:italic">Sin bloqueos próximos</span>`;
  }
  return bloqueos.map(b => {
    const motivo = b.motivo ? ` · ${esc(b.motivo)}` : "";
    return `<span class="horario-pill bloqueo-pill" title="${esc(b.motivo || "Bloqueo")}">${esc(_fmtRangoBloqueo(b))}${motivo}
      <button data-action="eliminar-bloqueo" data-id="${b.id}" title="Eliminar bloqueo" aria-label="Eliminar bloqueo">×</button></span>`;
  }).join("");
}

function renderHorariosPills(horarios, medicoId) {
  if (!horarios.length) return `<span style="font-size:.75rem;color:var(--muted);font-style:italic">Sin horarios cargados</span>`;
  return horarios.slice().sort((a, b) => a.dia_semana - b.dia_semana).map(h =>
    `<span class="horario-pill">${DIAS[h.dia_semana].slice(0,3)} · ${esc(h.hora_inicio)}–${esc(h.hora_fin)} · C${h.consultorio}
      <button data-action="eliminar-horario" data-id="${h.id}" title="Eliminar">×</button></span>`
  ).join("");
}

/* ── Link Calendario iCal ────────────────────────────────── */
async function copiarLinkCalendario(medicoId, regenerate = false) {
  let url;
  try {
    const qs = regenerate ? "?regenerate=true" : "";
    const res = await api(`/medicos/${medicoId}/calendario-url${qs}`);
    url = `${location.origin}${res.path}`;
  } catch (e) {
    toast("No se pudo generar el link: " + e.message, "error");
    return;
  }
  try {
    await navigator.clipboard.writeText(url);
    toast("Link del calendario copiado. Pegalo en Google Calendar → Otros calendarios → Desde URL. No compartas este link.", "success");
  } catch {
    prompt("Copiá este link (contiene un token privado, no lo compartas):", url);
  }
}
window.copiarLinkCalendario = copiarLinkCalendario;

/* ── Modal Médico ────────────────────────────────────────── */
function abrirNuevoMedico() {
  medicoEditing=null; setModalTitle("modal-medico-titulo","Nuevo Profesional");
  ["med-nombre","med-apellido","med-matricula","med-telefono","med-email","med-gcal"].forEach(id=>$(id).value="");
  $("med-especialidad").value=""; $("modal-medico").classList.add("open");
}
async function abrirEditarMedico(id) {
  const m=await api(`/medicos/${id}`); medicoEditing=id;
  setModalTitle("modal-medico-titulo","Editar Profesional");
  $("med-nombre").value=m.nombre; $("med-apellido").value=m.apellido;
  $("med-especialidad").value=m.especialidad_id; $("med-matricula").value=m.matricula||"";
  $("med-telefono").value=m.telefono||""; $("med-email").value=m.email||"";
  $("med-gcal").value=m.google_calendar_id||"";
  $("modal-medico").classList.add("open");
}
async function guardarMedico() {
  const body={nombre:$("med-nombre").value.trim(),apellido:$("med-apellido").value.trim(),especialidad_id:parseInt($("med-especialidad").value),matricula:$("med-matricula").value.trim()||null,telefono:$("med-telefono").value.trim()||null,email:$("med-email").value.trim()||null,google_calendar_id:$("med-gcal").value.trim()||null};
  if(!body.nombre||!body.apellido||!body.especialidad_id){toast("Nombre, apellido y especialidad son obligatorios.","error");return;}
  await _withSubmitLock("modal-medico", async () => {
    try{
      if(medicoEditing){await api(`/medicos/${medicoEditing}`,{method:"PUT",body:JSON.stringify(body)});toast("Profesional actualizado ✓","success");}
      else{await api("/medicos",{method:"POST",body:JSON.stringify(body)});toast("Profesional creado ✓","success");}
      cerrarModal("modal-medico"); medicos=await api("/medicos"); populateSelects(); renderProfesionales();
    }catch(e){toast(e.message,"error");}
  });
}

/* ── Modal Especialidades (alta + listado + borrado) ────────
 * Abierto desde el modal de Profesional y también desde la vista de
 * profesionales. Permite agregar nuevas especialidades y eliminar las que
 * no están en uso. Al agregar, deja la nueva seleccionada en #med-especialidad
 * si el modal de profesional está abierto.
 */
function _renderEspList() {
  const ul = $("esp-list"); if (!ul) return;
  if (!especialidades.length) {
    ul.innerHTML = `<li style="font-size:.8rem;color:var(--muted);font-style:italic;padding:.4rem 0">Sin especialidades</li>`;
    return;
  }
  ul.innerHTML = especialidades.slice()
    .sort((a, b) => a.nombre.localeCompare(b.nombre, "es"))
    .map(e => `<li class="esp-item" style="display:flex;justify-content:space-between;align-items:center;padding:.35rem .15rem;border-bottom:1px solid #eee">
        <span>${esc(e.nombre)}</span>
        <button class="btn btn-sm btn-danger" data-action="eliminar-especialidad" data-id="${e.id}" title="Eliminar especialidad" aria-label="Eliminar ${esc(e.nombre)}">×</button>
      </li>`).join("");
}
function abrirNuevaEspecialidad() {
  $("esp-nombre").value = "";
  _renderEspList();
  $("modal-especialidad").classList.add("open");
  setTimeout(() => $("esp-nombre").focus(), 50);
}
async function guardarEspecialidad() {
  const nombre = $("esp-nombre").value.trim();
  if (!nombre) { toast("Ingresá un nombre.", "error"); return; }
  await _withSubmitLock("modal-especialidad", async () => {
    try {
      const nueva = await api("/especialidades", { method: "POST", body: JSON.stringify({ nombre }) });
      toast("Especialidad guardada ✓", "success");
      especialidades = await api("/especialidades");
      populateSelects();
      _renderEspList();
      $("esp-nombre").value = "";
      // Dejar seleccionada la nueva en el modal de profesional si está abierto
      const sel = $("med-especialidad");
      if (sel) sel.value = nueva.id;
    } catch (e) { toast(e.message, "error"); }
  });
}
async function eliminarEspecialidad(id) {
  const esp = especialidades.find(x => x.id === id);
  if (!esp) return;
  if (!confirm(`¿Eliminar la especialidad "${esp.nombre}"?`)) return;
  try {
    await api(`/especialidades/${id}`, { method: "DELETE" });
    toast("Especialidad eliminada ✓", "success");
    especialidades = await api("/especialidades");
    populateSelects();
    _renderEspList();
  } catch (e) { toast(e.message, "error"); }
}
async function eliminarMedico(id) {
  if(!confirm("¿Eliminar este profesional y todos sus datos asociados (turnos, usuario)?"))return;
  try{await api(`/medicos/${id}?force=true`,{method:"DELETE"});toast("Profesional eliminado","success");medicos=await api("/medicos");populateSelects();renderProfesionales();}
  catch(e){toast(e.message,"error");}
}

/* ── Modal Horario ───────────────────────────────────────── */
function abrirAgregarHorario(medicoId) {
  horarioParaMedicoId=medicoId;
  $("hor-dia").value="0";$("hor-inicio").value="09:00";$("hor-fin").value="13:00";$("hor-consultorio").value="1";
  setModalTitle("modal-horario-titulo","Agregar Horario","Profesionales");
  $("modal-horario").classList.add("open");
}
async function guardarHorario() {
  const body={dia_semana:parseInt($("hor-dia").value),hora_inicio:$("hor-inicio").value,hora_fin:$("hor-fin").value,consultorio:parseInt($("hor-consultorio").value)};
  if(!body.hora_inicio||!body.hora_fin||body.hora_fin<=body.hora_inicio){toast("Horario inválido.","error");return;}
  await _withSubmitLock("modal-horario", async () => {
    try{
      await api(`/medicos/${horarioParaMedicoId}/horarios`,{method:"POST",body:JSON.stringify(body)});
      toast("Horario agregado ✓","success"); cerrarModal("modal-horario"); medicos=await api("/medicos"); renderProfesionales();
    }catch(e){toast(e.message,"error");}
  });
}
async function eliminarHorario(horarioId) {
  if(!confirm("¿Eliminar este horario?"))return;
  try{await api(`/horarios/${horarioId}`,{method:"DELETE"});toast("Horario eliminado","success");medicos=await api("/medicos");renderProfesionales();}
  catch(e){toast(e.message,"error");}
}

/* ── Modal Bloqueo ───────────────────────────────────────── */
function _isoLocal(d) {
  // d: Date → "YYYY-MM-DDTHH:MM" en hora local
  const pad = n => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
function abrirNuevoBloqueo(medicoId) {
  const sel = $("bloq-medico");
  if (sel) {
    sel.innerHTML = `<option value="">Seleccioná profesional</option>` +
      medicos.map(m => `<option value="${m.id}">${esc(m.nombre)} ${esc(m.apellido)}</option>`).join("");
    sel.value = medicoId ? String(medicoId) : "";
  }
  // Default: mañana 09:00 → mañana 19:30
  const start = new Date();
  start.setDate(start.getDate() + 1);
  start.setHours(9, 0, 0, 0);
  const end = new Date(start);
  end.setHours(19, 30, 0, 0);
  $("bloq-inicio").value = _isoLocal(start);
  $("bloq-fin").value    = _isoLocal(end);
  $("bloq-motivo").value = "";
  $("modal-bloqueo").classList.add("open");
}
async function guardarBloqueo() {
  const medicoId = parseInt($("bloq-medico").value);
  const inicio = $("bloq-inicio").value;
  const fin    = $("bloq-fin").value;
  const motivo = $("bloq-motivo").value.trim() || null;
  if (!medicoId) { toast("Seleccioná un profesional.", "error"); return; }
  if (!inicio || !fin) { toast("Completá las fechas de inicio y fin.", "error"); return; }
  if (fin <= inicio) { toast("La fecha/hora de fin debe ser posterior al inicio.", "error"); return; }
  await _withSubmitLock("modal-bloqueo", async () => {
    try {
      await api(`/medicos/${medicoId}/bloqueos`, {
        method: "POST",
        body: JSON.stringify({ fecha_inicio: inicio + ":00", fecha_fin: fin + ":00", motivo }),
      });
      toast("Bloqueo guardado ✓", "success");
      cerrarModal("modal-bloqueo");
      if (document.getElementById("view-profesionales")?.classList.contains("active")) {
        renderProfesionales();
      }
      if (document.getElementById("view-agenda")?.classList.contains("active")) {
        renderAgenda();
      }
    } catch (e) { toast(e.message, "error"); }
  });
}
async function eliminarBloqueo(id) {
  if (!confirm("¿Eliminar este bloqueo?")) return;
  try {
    await api(`/bloqueos/${id}`, { method: "DELETE" });
    toast("Bloqueo eliminado ✓", "success");
    if (document.getElementById("view-profesionales")?.classList.contains("active")) {
      renderProfesionales();
    }
    if (document.getElementById("view-agenda")?.classList.contains("active")) {
      renderAgenda();
    }
  } catch (e) { toast(e.message, "error"); }
}
function _fmtRangoBloqueo(b) {
  const ini = new Date(b.fecha_inicio);
  const fin = new Date(b.fecha_fin);
  const pad = n => String(n).padStart(2, "0");
  const sameDay = ini.toDateString() === fin.toDateString();
  const fecha = d => `${pad(d.getDate())}/${pad(d.getMonth()+1)}`;
  const hora  = d => `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  return sameDay
    ? `${fecha(ini)} ${hora(ini)}–${hora(fin)}`
    : `${fecha(ini)} ${hora(ini)} → ${fecha(fin)} ${hora(fin)}`;
}

/* ── Agregar paciente desde turno ───────────────────────── */
/* Resetea el botón "+ Agregar paciente" a su estado inicial. Evita que un
   onclick="agregarPacienteDesdeTurno" heredado de una acción previa
   dispare la creación duplicada del mismo paciente al editar otro turno. */
function _resetBotonAgregarPaciente() {
  const btn = $("btn-agregar-paciente");
  if (!btn) return;
  btn.textContent = "+ Agregar paciente";
  btn.onclick = null;
  btn.style.display = "none";
}

async function mostrarCamposPacienteNuevo() {
  $("btn-agregar-paciente").textContent = "Guardar paciente";
  $("btn-agregar-paciente").onclick = agregarPacienteDesdeTurno;
  // Prellenar nombre/apellido desde lo tipeado (convención: "NOMBRE APELLIDO").
  // La secretaria puede corregir si se confundió el orden antes de guardar.
  const tipeado = ($("turno-paciente-input").value || "").trim().toUpperCase();
  if (tipeado) {
    const partes = tipeado.split(/\s+/);
    if ($("turno-new-nombre") && !$("turno-new-nombre").value) {
      $("turno-new-nombre").value = partes.shift() || "";
    }
    if ($("turno-new-apellido") && !$("turno-new-apellido").value) {
      $("turno-new-apellido").value = partes.join(" ") || "";
    }
  }
  // Auto-generar HC si no hay valor
  if (!$("turno-new-hc").value) {
    try {
      const res = await api("/pacientes/next-hc");
      $("turno-new-hc").value = res.next_hc;
    } catch(e) { $("turno-new-hc").value = ""; }
  }
  if ($("turno-pac-info")) $("turno-pac-info").style.display = "none";
  (($("turno-new-nombre")?.value ? $("turno-new-apellido") : $("turno-new-nombre")) || {}).focus?.();
}

async function agregarPacienteDesdeTurno() {
  const nombre = ($("turno-new-nombre")?.value || "").trim().toUpperCase();
  const apellido = ($("turno-new-apellido")?.value || "").trim().toUpperCase();
  if (!nombre || !apellido) { toast("Nombre y Apellido son obligatorios","error"); return; }
  const tel = $("turno-new-tel").value.trim();
  const email = $("turno-new-email").value.trim();
  const dni = $("turno-new-dni").value.trim();
  if (!tel || !email) { toast("Teléfono y Email son obligatorios para pacientes nuevos","error"); return; }
  const financiador = $("turno-financiador").value.trim().toUpperCase() || null;
  const plan = $("turno-plan").value.trim().toUpperCase() || null;
  const nro_hc = $("turno-new-hc").value.trim() || null;
  const deriva = ($("turno-new-deriva")?.value || "").trim().toUpperCase() || null;
  if (!_confirmarSiDuplicado({dni: dni || null, nro_hc})) return;
  try {
    const nuevo = await api("/pacientes",{method:"POST",body:JSON.stringify({
      nombre,
      apellido,
      dni: dni || null,
      telefono: tel,
      email: email.toLowerCase(),
      nro_hc, financiador, plan, deriva,
    })});
    pacientes.push(nuevo);
    $("turno-paciente-id").value = nuevo.id;
    $("turno-paciente-input").value = `${nuevo.nombre} ${nuevo.apellido}`;
    _resetBotonAgregarPaciente();
    if ($("turno-pac-info")) $("turno-pac-info").style.display = "none";
    toast("Paciente agregado a la base de datos","success");
  } catch(e) { toast(e.message,"error"); }
}

/* ── Modal Turno ─────────────────────────────────────────── */
function abrirNuevoTurno(consultorio=1, fechaHora="") {
  turnoEditing=null;
  setModalTitle("modal-turno-titulo","Nuevo Turno"); $("campo-estado").style.display="none";
  $("turno-consultorio").value=consultorio; $("turno-fecha-hora").value=fechaHora;
  $("turno-paciente-input").value=""; $("turno-paciente-id").value="";
  $("turno-medico").value=""; $("turno-duracion").value="45";
  $("turno-financiador").value=""; $("turno-plan").value="";
  $("turno-obs").value="";
  _resetBotonAgregarPaciente();
  if($("turno-pac-info")) $("turno-pac-info").style.display="none";
  if($("turno-new-nombre")) $("turno-new-nombre").value="";
  if($("turno-new-apellido")) $("turno-new-apellido").value="";
  if($("turno-new-dni")) $("turno-new-dni").value="";
  if($("turno-new-tel")) $("turno-new-tel").value="";
  if($("turno-new-email")) $("turno-new-email").value="";
  if($("turno-new-hc")) $("turno-new-hc").value="";
  if($("turno-new-deriva")) $("turno-new-deriva").value="";
  const drop=$("turno-paciente-input-drop"); if(drop) drop.style.display="none";
  // Preseleccionar medico si es profesional
  if (currentUser && currentUser.role==="medico" && currentUser.medico_id) $("turno-medico").value=currentUser.medico_id;
  $("modal-turno").classList.add("open");
}
async function abrirEditarTurno(id) {
  const t=await api(`/turnos/${id}`); turnoEditing=id;
  setModalTitle("modal-turno-titulo","Editar Turno"); $("campo-estado").style.display="flex";
  $("turno-consultorio").value=t.consultorio; $("turno-fecha-hora").value=t.fecha_hora_inicio.slice(0,16);
  $("turno-paciente-input").value=`${t.paciente?.nombre} ${t.paciente?.apellido}`;
  $("turno-paciente-id").value=t.paciente_id;
  $("turno-medico").value=t.medico_id; $("turno-duracion").value=t.duracion_minutos;
  $("turno-financiador").value=t.paciente?.financiador||""; $("turno-plan").value=t.paciente?.plan||"";
  $("turno-obs").value=t.observaciones||""; $("turno-estado").value=t.estado;
  _resetBotonAgregarPaciente();
  // Popular los campos del paciente del turno que se edita.
  const p = t.paciente || {};
  if($("turno-new-nombre"))   $("turno-new-nombre").value   = p.nombre   || "";
  if($("turno-new-apellido")) $("turno-new-apellido").value = p.apellido || "";
  if($("turno-new-tel"))      $("turno-new-tel").value      = p.telefono || "";
  if($("turno-new-email"))    $("turno-new-email").value    = p.email    || "";
  if($("turno-new-dni"))      $("turno-new-dni").value      = p.dni      || "";
  if($("turno-new-hc"))       $("turno-new-hc").value       = p.nro_hc   || "";
  if($("turno-new-deriva"))   $("turno-new-deriva").value   = p.deriva   || "";
  if($("turno-pac-info"))     $("turno-pac-info").style.display = "none";
  const drop=$("turno-paciente-input-drop"); if(drop) drop.style.display="none";
  $("modal-turno").classList.add("open");
}
function abrirNuevoTurnoPaciente(pacienteId) {
  abrirNuevoTurno();
  const p=pacientes.find(x=>x.id===pacienteId);
  if(p){$("turno-paciente-input").value=`${p.nombre} ${p.apellido}`;$("turno-paciente-id").value=p.id;}
  navTo("view-agenda");
}
function _validarHorarioMedico(medicoId, fechaHora, consultorio) {
  const m = medicos.find(x => x.id === medicoId);
  if (!m || !m.horarios || !m.horarios.length) return null;
  const dt = new Date(fechaHora);
  const dia = dt.getDay() - 1; // 0=Lun ... 4=Vie (-1=Dom, 5=Sab)
  const hhmm = String(dt.getHours()).padStart(2,"0") + ":" + String(dt.getMinutes()).padStart(2,"0");
  const horariosDelDia = m.horarios.filter(h => h.dia_semana === dia && h.consultorio === consultorio);
  if (!horariosDelDia.length) {
    const diasNombre = ["Lunes","Martes","Miércoles","Jueves","Viernes"];
    return `${m.nombre} ${m.apellido} no tiene horarios cargados para ${diasNombre[dia] || "ese día"} en Consultorio ${consultorio}.`;
  }
  const enRango = horariosDelDia.some(h => hhmm >= h.hora_inicio && hhmm < h.hora_fin);
  if (!enRango) {
    const rangos = horariosDelDia.map(h => `${h.hora_inicio}–${h.hora_fin}`).join(", ");
    return `El horario ${hhmm} está fuera de la franja del profesional (${rangos}) en Consultorio ${consultorio}.`;
  }
  return null;
}

async function guardarTurno() {
  clearFormErrors("modal-turno");
  let pacienteId=parseInt($("turno-paciente-id").value);
  const medicoId=parseInt($("turno-medico").value);
  let ok = true;
  // Si no hay paciente seleccionado, exigir Nombre + Apellido + Tel + Email
  // para poder crearlo automáticamente abajo. La secretaria ya no necesita
  // apretar "+ Agregar paciente" antes — se resuelve al guardar el turno.
  if (!pacienteId) {
    const n = ($("turno-new-nombre")?.value||"").trim();
    const a = ($("turno-new-apellido")?.value||"").trim();
    const t = ($("turno-new-tel")?.value||"").trim();
    const e = ($("turno-new-email")?.value||"").trim();
    if (!n) { markFieldError("turno-new-nombre", "Nombre obligatorio"); ok = false; }
    if (!a) { markFieldError("turno-new-apellido", "Apellido obligatorio"); ok = false; }
    if (!t) { markFieldError("turno-new-tel", "Teléfono obligatorio"); ok = false; }
    if (!e) { markFieldError("turno-new-email", "Email obligatorio"); ok = false; }
  }
  if (!medicoId)   { markFieldError("turno-medico", "Seleccioná un profesional"); ok = false; }
  if (!$("turno-fecha-hora").value) { markFieldError("turno-fecha-hora", "Indicá fecha y hora"); ok = false; }
  if (!ok) { toast("Completá los campos obligatorios.","error"); return; }

  // Validar franja horaria del profesional
  const alertaHorario = _validarHorarioMedico(medicoId, $("turno-fecha-hora").value, parseInt($("turno-consultorio").value));
  if (alertaHorario && !confirm(alertaHorario + "\n\n¿Agendar de todas formas?")) return;

  await _withSubmitLock("modal-turno", async () => {
    try{
      const fin=$("turno-financiador").value.trim().toUpperCase()||null;
      const plan=$("turno-plan").value.trim().toUpperCase()||null;
      // Si el paciente no existe todavía, crearlo al vuelo con los datos del form.
      if (!pacienteId) {
        let nro_hc = ($("turno-new-hc")?.value||"").trim() || null;
        if (!nro_hc) {
          try { const r = await api("/pacientes/next-hc"); nro_hc = r.next_hc; } catch(_) {}
        }
        const dniNuevo = ($("turno-new-dni")?.value||"").trim() || null;
        if (!_confirmarSiDuplicado({dni: dniNuevo, nro_hc})) return;
        const nuevo = await api("/pacientes",{method:"POST",body:JSON.stringify({
          nombre: $("turno-new-nombre").value.trim().toUpperCase(),
          apellido: $("turno-new-apellido").value.trim().toUpperCase(),
          telefono: $("turno-new-tel").value.trim(),
          email: ($("turno-new-email").value||"").trim().toLowerCase() || null,
          dni: dniNuevo,
          deriva: ($("turno-new-deriva")?.value||"").trim().toUpperCase() || null,
          nro_hc, financiador: fin, plan,
        })});
        pacientes.push(nuevo);
        pacienteId = nuevo.id;
        $("turno-paciente-id").value = nuevo.id;
        $("turno-paciente-input").value = `${nuevo.nombre} ${nuevo.apellido}`;
      } else {
        // Paciente existente: actualizar financiador/plan si cambiaron.
        const pac=pacientes.find(p=>p.id===pacienteId);
        if(pac && (fin!==pac.financiador || plan!==pac.plan)){
          await api(`/pacientes/${pacienteId}`,{method:"PUT",body:JSON.stringify({...pac,financiador:fin,plan:plan})});
          pac.financiador=fin; pac.plan=plan;
        }
      }
      const body={paciente_id:pacienteId,medico_id:medicoId,consultorio:parseInt($("turno-consultorio").value),fecha_hora_inicio:$("turno-fecha-hora").value+":00",duracion_minutos:parseInt($("turno-duracion").value),observaciones:$("turno-obs").value||null};
      if(turnoEditing){
        await api(`/turnos/${turnoEditing}`,{method:"PUT",body:JSON.stringify({...body,estado:$("turno-estado").value})});
        toast("Turno actualizado ✓","success");
      }else{
        await api("/turnos",{method:"POST",body:JSON.stringify(body)});
        toast("Turno creado ✓","success");
      }
      cerrarModal("modal-turno"); renderAgenda(); renderDashboard();
    }catch(e){toast(e.message,"error");}
  });
}
async function cancelarTurno(id) {
  if(!confirm("¿Cancelar este turno?"))return;
  try{
    await api(`/turnos/${id}/cancelar`,{method:"DELETE"});
    toast("Turno cancelado","success");
    if ($("view-turnos")?.classList.contains("active")) renderTurnos();
    if ($("view-agenda")?.classList.contains("active")) renderAgenda();
    renderDashboard();
  }catch(e){toast(e.message,"error");}
}

async function eliminarTurno(id) {
  let t;
  try { t = await api("/turnos/"+id); }
  catch(e){ toast("No se pudo cargar el turno: "+e.message,"error"); return; }
  const paciente = `${t.paciente?.nombre||""} ${t.paciente?.apellido||""}`.trim() || "paciente desconocido";
  const fechaHora = `${fmtFechaCorta(t.fecha_hora_inicio)} a las ${fmtHoraDisplay(t.fecha_hora_inicio)} hs`;
  const medico = t.medico ? `${t.medico.nombre} ${t.medico.apellido}` : "";
  const msg = `¿Eliminar el turno de ${paciente}\n${fechaHora}${medico?` — ${medico}`:""}?\n\nEsta acción no se puede deshacer.`;
  if(!confirm(msg))return;
  try{
    await api("/turnos/"+id, {method:"DELETE"});
    toast("Turno eliminado ✓","success");
    // Re-render de las vistas afectadas (la tabla de turnos usa data-action,
    // no onclick, así que manipular el DOM por string ya no aplica).
    if ($("view-turnos")?.classList.contains("active")) renderTurnos();
    if ($("view-agenda")?.classList.contains("active")) renderAgenda();
    renderDashboard();
  }catch(e){
    toast("Error: "+e.message,"error");
  }
}

/* ── Modal Paciente ─────────────────────────────────────── */
async function abrirNuevoPaciente(prefill) {
  pacienteEditing=null; setModalTitle("modal-paciente-titulo","Nuevo Paciente");
  ["pac-nombre","pac-apellido","pac-tel","pac-email","pac-dni","pac-hc","pac-financiador","pac-plan","pac-deriva"].forEach(id=>$(id).value="");
  if (typeof prefill === "string" && prefill.trim()) {
    // "NOMBRE APELLIDO" o solo "NOMBRE" → primer token a nombre, resto a apellido
    const partes = prefill.trim().split(/\s+/);
    $("pac-nombre").value   = (partes.shift() || "").toUpperCase();
    $("pac-apellido").value = partes.join(" ").toUpperCase();
  }
  // Auto-generar HC (editable, sirve como sugerencia del próximo nro)
  try {
    const res = await api("/pacientes/next-hc");
    $("pac-hc").value = res.next_hc;
  } catch(_) {}
  $("modal-paciente").classList.add("open");
  (($("pac-nombre").value ? $("pac-apellido") : $("pac-nombre")) || {}).focus?.();
}
async function abrirEditarPaciente(id) {
  const p=await api(`/pacientes/${id}`); pacienteEditing=id;
  setModalTitle("modal-paciente-titulo","Editar Paciente");
  $("pac-nombre").value=p.nombre;$("pac-apellido").value=p.apellido;
  $("pac-tel").value=p.telefono||"";$("pac-email").value=p.email||"";
  $("pac-dni").value=p.dni||"";$("pac-hc").value=p.nro_hc||"";
  $("pac-financiador").value=p.financiador||"";$("pac-plan").value=p.plan||"";$("pac-deriva").value=p.deriva||"";
  $("modal-paciente").classList.add("open");
}
// Busca un paciente existente con DNI o HC iguales (case-insensitive,
// ignorando el id excluido para permitir editar sin auto-match). Devuelve el
// paciente encontrado o null. Usado para warnings de duplicado al alta/edición.
function _buscarPacienteDuplicado({dni, nro_hc, excludeId} = {}) {
  const norm = s => (s == null ? "" : String(s)).trim().toLowerCase();
  const dniN = norm(dni), hcN = norm(nro_hc);
  if (!dniN && !hcN) return null;
  return pacientes.find(p => {
    if (excludeId && p.id === excludeId) return false;
    if (dniN && norm(p.dni) === dniN)   return true;
    if (hcN  && norm(p.nro_hc) === hcN) return true;
    return false;
  }) || null;
}
// Si hay duplicado, pedir confirmación. Devuelve true si se puede continuar.
function _confirmarSiDuplicado({dni, nro_hc, excludeId} = {}) {
  const dup = _buscarPacienteDuplicado({dni, nro_hc, excludeId});
  if (!dup) return true;
  const campo = (dni && String(dup.dni||"").toLowerCase() === String(dni).trim().toLowerCase())
    ? `DNI ${dup.dni}`
    : `HC ${dup.nro_hc}`;
  const msg = `⚠ Ya existe un paciente con el mismo ${campo}:\n` +
              `  ${dup.nombre} ${dup.apellido}` +
              (dup.nro_hc ? ` (HC ${dup.nro_hc})` : "") +
              `\n\n¿Guardar de todas formas?`;
  return confirm(msg);
}

async function guardarPaciente() {
  clearFormErrors("modal-paciente");
  if (!validateRequired([
    {id:"pac-nombre",   msg:"El nombre es obligatorio"},
    {id:"pac-apellido", msg:"El apellido es obligatorio"},
    {id:"pac-tel",      msg:"El teléfono es obligatorio"},
    {id:"pac-email",    msg:"El email es obligatorio"},
  ])) { toast("Completá los campos obligatorios.","error"); return; }
  const dni = $("pac-dni").value.trim() || null;
  const nro_hc = $("pac-hc").value.trim() || null;
  if (!_confirmarSiDuplicado({dni, nro_hc, excludeId: pacienteEditing})) return;
  const body={nombre:$("pac-nombre").value.trim().toUpperCase(),apellido:$("pac-apellido").value.trim().toUpperCase(),telefono:$("pac-tel").value.trim()||null,email:$("pac-email").value.trim().toLowerCase()||null,dni,nro_hc,financiador:$("pac-financiador").value.trim().toUpperCase()||null,plan:$("pac-plan").value.trim().toUpperCase()||null,deriva:$("pac-deriva").value.trim().toUpperCase()||null};
  await _withSubmitLock("modal-paciente", async () => {
    try{
      if(pacienteEditing){await api(`/pacientes/${pacienteEditing}`,{method:"PUT",body:JSON.stringify(body)});toast("Paciente actualizado ✓","success");}
      else{await api("/pacientes",{method:"POST",body:JSON.stringify(body)});toast("Paciente creado ✓","success");}
      cerrarModal("modal-paciente"); pacientes=await api("/pacientes"); populateSelects(); renderPacientes();
    }catch(e){toast(e.message,"error");}
  });
}

async function eliminarPaciente(id) {
  if(!confirm("¿Eliminar este paciente y todos sus turnos?"))return;
  try{
    await api(`/pacientes/${id}`,{method:"DELETE"});
    toast("Paciente eliminado","success");
    pacientes=await api("/pacientes"); populateSelects(); renderPacientes();
  }catch(e){toast(e.message,"error");}
}

/* ── Atajos de teclado ──────────────────────────────────── */
document.addEventListener("keydown", e=>{
  // ESC cierra el último modal abierto incluso desde dentro de un input
  if (e.key === "Escape") {
    const openModals = document.querySelectorAll(".modal-overlay.open");
    if (openModals.length) {
      const last = openModals[openModals.length - 1];
      if (last.id === "modal-password" && _pwForced) return;
      last.classList.remove("open");
      clearFormErrors(last.id);
      return;
    }
  }
  if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") return;
  if (e.key === "1") navTo("view-dashboard");
  if (e.key === "2") navTo("view-agenda");
  if (e.key === "3") navTo("view-turnos");
  if (e.key === "4") navTo("view-pacientes");
  if (e.key === "5") navTo("view-profesionales");
  if (e.key === "6" && currentUser && currentUser.role === "admin") navTo("view-audit");
  if (e.key.toLowerCase() === "n") {
    if (document.querySelector("#view-agenda.active"))        abrirNuevoTurno();
    else if (document.querySelector("#view-pacientes.active")) abrirNuevoPaciente();
    else if (document.querySelector("#view-profesionales.active")) abrirNuevoMedico();
  }
});

/* ── Helpers ─────────────────────────────────────────────── */
let _pwForced = false;
function cerrarModal(id){
  if (id === "modal-password" && _pwForced) {
    toast("Debes cambiar tu contraseña antes de continuar.","error");
    return;
  }
  $(id).classList.remove("open"); clearFormErrors(id);
}

/* ── Anti double-submit ─────────────────────────────────── */
const _submitLocks = Object.create(null);
async function _withSubmitLock(modalId, fn) {
  if (_submitLocks[modalId]) return;
  _submitLocks[modalId] = true;
  const btn = document.querySelector(`#${modalId} .modal-footer .btn-primary`);
  const originalLabel = btn?.textContent;
  if (btn) { btn.disabled = true; btn.setAttribute("aria-busy", "true"); btn.textContent = "Guardando…"; }
  try { return await fn(); }
  finally {
    _submitLocks[modalId] = false;
    if (btn) { btn.disabled = false; btn.removeAttribute("aria-busy"); if (originalLabel) btn.textContent = originalLabel; }
  }
}

/* ── Accesibilidad de modales: aria-hidden, focus trap y restore ─── */
const FOCUSABLE_SEL = 'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

function _focusablesIn(modal) {
  return Array.from(modal.querySelectorAll(FOCUSABLE_SEL))
    .filter(el => el.offsetParent !== null || el === document.activeElement);
}

function _trapTab(modal, e) {
  if (e.key !== "Tab") return;
  const items = _focusablesIn(modal);
  if (items.length === 0) { e.preventDefault(); return; }
  const first = items[0], last = items[items.length - 1];
  const active = document.activeElement;
  if (e.shiftKey && active === first) {
    e.preventDefault(); last.focus();
  } else if (!e.shiftKey && active === last) {
    e.preventDefault(); first.focus();
  }
}

function _onModalOpen(overlay) {
  overlay.setAttribute("aria-hidden", "false");
  overlay._prevFocus = document.activeElement;
  const modal = overlay.querySelector(".modal") || overlay;
  const first = _focusablesIn(modal).find(el => !el.classList.contains("modal-close")) || _focusablesIn(modal)[0];
  if (first) setTimeout(() => first.focus(), 0);
  overlay._trapHandler = (e) => _trapTab(modal, e);
  overlay.addEventListener("keydown", overlay._trapHandler);
}

function _onModalClose(overlay) {
  overlay.setAttribute("aria-hidden", "true");
  if (overlay._trapHandler) {
    overlay.removeEventListener("keydown", overlay._trapHandler);
    overlay._trapHandler = null;
  }
  const prev = overlay._prevFocus;
  overlay._prevFocus = null;
  if (prev && typeof prev.focus === "function" && document.body.contains(prev)) {
    try { prev.focus(); } catch {}
  }
}

(function _setupModalA11y() {
  const overlays = document.querySelectorAll(".modal-overlay");
  const observer = new MutationObserver(muts => {
    muts.forEach(m => {
      if (m.type !== "attributes" || m.attributeName !== "class") return;
      const el = m.target;
      const isOpen = el.classList.contains("open");
      const wasOpen = el._wasOpen === true;
      if (isOpen && !wasOpen) _onModalOpen(el);
      else if (!isOpen && wasOpen) _onModalClose(el);
      el._wasOpen = isOpen;
    });
  });
  overlays.forEach(el => {
    el._wasOpen = el.classList.contains("open");
    observer.observe(el, { attributes: true, attributeFilter: ["class"] });
  });
})();

/* ── Cambiar contraseña ───────────────────────────────────── */
function abrirCambiarPassword() {
  $("pw-current").value=""; $("pw-new").value=""; $("pw-confirm").value="";
  setModalTitle("modal-password-titulo","Cambiar contraseña","Mi cuenta");
  _setPasswordModalForced(false);
  $("modal-password").classList.add("open");
}

function _setPasswordModalForced(forced) {
  _pwForced = !!forced;
  const modal = $("modal-password");
  if (!modal) return;
  const closeBtn = modal.querySelector(".modal-close");
  const footer = modal.querySelector(".modal-footer");
  if (closeBtn) closeBtn.style.display = forced ? "none" : "";
  if (footer) {
    const cancelBtn = footer.querySelector(".btn-outline");
    if (cancelBtn) cancelBtn.style.display = forced ? "none" : "";
  }
}

function _forceChangePassword() {
  $("pw-current").value=""; $("pw-new").value=""; $("pw-confirm").value="";
  setModalTitle("modal-password-titulo","Debes cambiar tu contraseña","Primer ingreso / reseteo");
  _setPasswordModalForced(true);
  $("modal-password").classList.add("open");
  // Ocultar el resto de la app hasta que cambie la contraseña
  document.body.classList.add("pw-locked");
  const first = $("pw-current");
  if (first) first.focus();
}
async function resetearPassword(userId, username) {
  if(!confirm(`¿Resetear la contraseña de "${username}"? Se generará una contraseña temporal que el usuario deberá cambiar en el primer login.`))return;
  try{
    const res=await api(`/auth/users/${userId}/reset-password`,{method:"PUT"});
    const tmp = res.temporary_password || "";
    if (tmp) {
      try { await navigator.clipboard.writeText(tmp); } catch {}
      alert(`Contraseña temporal para "${username}":\n\n${tmp}\n\n(Ya se copió al portapapeles.) El usuario deberá cambiarla al iniciar sesión.`);
      toast("Contraseña temporal generada y copiada","success");
    } else {
      toast(res.detail || "Contraseña reseteada","success");
    }
  }catch(e){toast(e.message,"error");}
}

async function guardarPassword() {
  clearFormErrors("modal-password");
  const cur=$("pw-current").value, nw=$("pw-new").value, conf=$("pw-confirm").value;
  let ok = true;
  if (!cur) { markFieldError("pw-current", "Ingresá tu contraseña actual"); ok = false; }
  if (!nw)  { markFieldError("pw-new",     "Ingresá la nueva contraseña"); ok = false; }
  if (!ok) { toast("Completá los campos obligatorios.","error"); return; }
  if (nw.length < 8) { markFieldError("pw-new", "Mínimo 8 caracteres"); $("pw-new").focus(); return; }
  if (nw === cur)    { markFieldError("pw-new", "Debe ser distinta a la actual"); $("pw-new").focus(); return; }
  if (nw !== conf)   { markFieldError("pw-confirm", "Las contraseñas no coinciden"); $("pw-confirm").focus(); return; }
  try{
    await api("/auth/change-password",{method:"PUT",body:JSON.stringify({current_password:cur,new_password:nw})});
    toast("Contraseña actualizada","success");
    if (currentUser) { currentUser.must_change_password = false; localStorage.setItem("user", JSON.stringify(currentUser)); }
    const wasForced = _pwForced;
    _setPasswordModalForced(false);
    $("modal-password").classList.remove("open"); clearFormErrors("modal-password");
    document.body.classList.remove("pw-locked");
    if (wasForced) {
      // Recargar para re-inicializar con la sesión ya desbloqueada
      location.reload();
    }
  }catch(e){toast(e.message,"error");}
}
document.querySelectorAll(".modal-overlay").forEach(m=>m.addEventListener("click",e=>{if(e.target===m){m.classList.remove("open"); clearFormErrors(m.id);}}));
$("btn-fab")?.addEventListener("click",()=>abrirNuevoTurno());
$("btn-export-csv")?.addEventListener("click", () => exportarTurnosXLSX());
$("btn-nuevo-paciente")?.addEventListener("click", () => abrirNuevoPaciente());
$("btn-nuevo-medico")?.addEventListener("click", () => abrirNuevoMedico());

/* ── Auditoría (admin) ─────────────────────────────────── */
function _fmtAuditTs(iso) {
  if (!iso) return "";
  try {
    const d = new Date(iso);
    return d.toLocaleDateString("es-AR") + " " + d.toLocaleTimeString("es-AR",{hour:"2-digit",minute:"2-digit",second:"2-digit",hour12:false});
  } catch { return iso; }
}
function _fmtAuditDetails(txt) {
  if (!txt) return "";
  try {
    const o = JSON.parse(txt);
    return Object.entries(o).map(([k,v]) => `${esc(k)}=${esc(typeof v==="object"?JSON.stringify(v):String(v))}`).join(" · ");
  } catch { return esc(txt); }
}
async function renderAudit() {
  const cont = $("audit-lista");
  if (!cont) return;
  cont.innerHTML = `<div style="text-align:center;padding:1.5rem;color:var(--muted)">Cargando...</div>`;
  const q = new URLSearchParams();
  const action = $("audit-f-action")?.value.trim();
  const username = $("audit-f-username")?.value.trim();
  const entity = $("audit-f-entity-type")?.value.trim();
  const limit = parseInt($("audit-f-limit")?.value || "200", 10);
  if (action)   q.set("action", action);
  if (username) q.set("username", username);
  if (entity)   q.set("entity_type", entity);
  if (limit)    q.set("limit", String(Math.max(1, Math.min(limit, 1000))));
  let rows = [];
  try {
    rows = await api("/auth/audit?" + q.toString()) || [];
  } catch (e) {
    cont.innerHTML = `<div style="padding:1rem;color:#c0392b">Error cargando auditoría: ${esc(e.message)}</div>`;
    return;
  }
  if (!rows.length) {
    cont.innerHTML = `<div style="padding:1rem;color:var(--muted)">Sin eventos.</div>`;
    return;
  }
  cont.innerHTML = `
    <table class="tbl">
      <thead><tr>
        <th>Fecha</th><th>Usuario</th><th>Acción</th><th>Entidad</th><th>IP</th><th>Detalles</th>
      </tr></thead>
      <tbody>
        ${rows.map(r => `<tr>
          <td style="white-space:nowrap;font-size:.8rem">${esc(_fmtAuditTs(r.timestamp))}</td>
          <td style="font-size:.82rem">${esc(r.username || "—")}</td>
          <td style="font-size:.82rem;font-family:monospace">${esc(r.action)}</td>
          <td style="font-size:.8rem">${esc([r.entity_type, r.entity_id].filter(Boolean).join(" #"))}</td>
          <td style="font-size:.78rem;color:var(--muted)">${esc(r.ip || "")}</td>
          <td style="font-size:.78rem;color:#555">${_fmtAuditDetails(r.details)}</td>
        </tr>`).join("")}
      </tbody>
    </table>`;
}
$("btn-audit-refresh")?.addEventListener("click", renderAudit);
["audit-f-action","audit-f-username","audit-f-entity-type","audit-f-limit"].forEach(id => {
  $(id)?.addEventListener("change", renderAudit);
});

/* ── 2FA ───────────────────────────────────────────────── */
async function abrir2FA() {
  $("modal-2fa").classList.add("open");
  $("tfa-setup-block").style.display = "none";
  $("tfa-disable-block").style.display = "none";
  $("tfa-start-block").style.display = "none";
  $("tfa-status-block").textContent = "Cargando…";
  try {
    const st = await api("/auth/2fa/status");
    if (st && st.enabled) {
      $("tfa-status-block").innerHTML = `<span style="color:#2d7a4f;font-weight:600">✓ 2FA activo</span>`;
      $("tfa-disable-block").style.display = "block";
      $("tfa-disable-pw").value = "";
    } else {
      $("tfa-status-block").innerHTML = `<span style="color:var(--muted)">2FA inactivo</span>`;
      $("tfa-start-block").style.display = "block";
    }
  } catch (e) {
    $("tfa-status-block").innerHTML = `<span style="color:#c0392b">Error: ${esc(e.message)}</span>`;
  }
}

async function iniciar2FA() {
  try {
    const r = await api("/auth/2fa/setup", { method: "POST" });
    $("tfa-start-block").style.display = "none";
    $("tfa-setup-block").style.display = "block";
    // Mostramos el secret para entrada manual en la app TOTP. Evitamos generar
    // un QR con servicios externos para no filtrar el secreto a terceros; la
    // CSP tampoco permite scripts externos así que la alternativa segura es
    // entrada manual (copiar la clave) + link otpauth:// para mobile.
    $("tfa-secret-line").textContent = r.secret;
    const link = $("tfa-otpauth-link");
    link.href = r.otpauth_uri;
    link.textContent = r.otpauth_uri;
    const copyBtn = $("tfa-copy-btn");
    if (copyBtn) {
      copyBtn.onclick = async () => {
        try { await navigator.clipboard.writeText(r.secret); toast("Clave copiada", "success"); }
        catch { toast("No se pudo copiar automáticamente", "warning"); }
      };
    }
    $("tfa-activate-code").value = "";
    $("tfa-activate-code").focus();
  } catch (e) { toast(e.message, "error"); }
}

async function activar2FA() {
  const code = $("tfa-activate-code").value.trim();
  if (!/^\d{6}$/.test(code)) { toast("Ingresá los 6 dígitos del código.", "warning"); return; }
  try {
    await api("/auth/2fa/activate", { method: "POST", body: JSON.stringify({ code }) });
    toast("2FA activado ✓", "success");
    cerrarModal("modal-2fa");
  } catch (e) { toast(e.message, "error"); }
}

async function desactivar2FA() {
  const password = $("tfa-disable-pw").value;
  if (!password) { toast("Ingresá tu contraseña.", "warning"); return; }
  if (!confirm("¿Desactivar 2FA? Tu cuenta volverá a requerir solo contraseña.")) return;
  try {
    await api("/auth/2fa/disable", { method: "POST", body: JSON.stringify({ password }) });
    toast("2FA desactivado", "success");
    cerrarModal("modal-2fa");
  } catch (e) { toast(e.message, "error"); }
}

init().then(() => {
  if (!localStorage.getItem("tutorial_done")) tutorialStart();
}).catch(e=>console.error("Error de inicio:",e));

/* ── Tutorial interactivo ──────────────────────────────── */
let _tutStep = 0;
let _tutSteps = [];

function _tutStepsAdmin() {
  return [
    { title: "Bienvenido a MIO MEDIC", desc: "Te vamos a mostrar las principales funciones del sistema de turnos. Hace click en Siguiente para continuar.", target: ".header-logo" },
    { title: "Dashboard", desc: "Aca ves un resumen de los turnos de hoy: cuantos hay, pendientes, confirmados, realizados y ausentes/cancelados.", target: '[data-view="view-dashboard"]', action: ()=>navTo("view-dashboard") },
    { title: "Agenda", desc: "La agenda muestra los turnos del dia en formato de grilla por consultorio. Podes hacer click en un horario libre para agendar un turno nuevo.", target: '[data-view="view-agenda"]', action: ()=>navTo("view-agenda") },
    { title: "Turnos", desc: "Aca ves la lista completa de turnos con todos los datos del paciente. Podes filtrar por fecha y buscar por nombre.", target: '[data-view="view-turnos"]', action: ()=>navTo("view-turnos") },
    { title: "Pacientes", desc: "Gestion de pacientes: buscar, agregar, editar o eliminar. Desde aca tambien podes agendar un turno rapido para un paciente.", target: '[data-view="view-pacientes"]', action: ()=>navTo("view-pacientes") },
    { title: "Profesionales", desc: "Administra los profesionales del consultorio, sus horarios de atencion y la integracion con Google Calendar.", target: '[data-view="view-profesionales"]', action: ()=>navTo("view-profesionales") },
    { title: "Google Calendar", desc: "Para sincronizar turnos con Google Calendar: 1) Edita el profesional y completa el campo Email Google Calendar con el mail del calendario. 2) En Google Calendar, compartilo con el email de la cuenta de servicio como editor. Los turnos se sincronizan automaticamente.", target: '[data-view="view-profesionales"]' },
    { title: "Nuevo Turno", desc: "Para agendar un turno nuevo, usa el boton + Turno en la agenda o el boton Turno en la ficha del paciente. Si el paciente no existe, podes crearlo en el momento.", target: "#btn-fab", action: ()=>navTo("view-agenda") },
    { title: "Cambiar contraseña", desc: "Cada usuario puede cambiar su contraseña haciendo click en el boton Clave en la esquina superior derecha.", target: "#user-display" },
    { title: "Listo!", desc: "Ya conoces las funciones principales. Si tenes dudas, explora cada seccion. Este tutorial no se va a volver a mostrar.", target: ".header-logo" },
  ];
}

function _tutStepsMedico() {
  return [
    { title: "Bienvenido a MIO MEDIC", desc: "Te vamos a mostrar tu panel profesional. Solo ves tus propios turnos agendados.", target: ".header-logo" },
    { title: "Tus turnos de hoy", desc: "El dashboard muestra un resumen de tus turnos de hoy con contadores de estado.", target: '[data-view="view-dashboard"]', action: ()=>navTo("view-dashboard") },
    { title: "Tu agenda", desc: "La agenda muestra tus turnos en formato de grilla por consultorio y horario.", target: '[data-view="view-agenda"]', action: ()=>navTo("view-agenda") },
    { title: "Lista de turnos", desc: "Aca podes ver, editar o cancelar tus turnos. Filtra por fecha o busca por nombre de paciente.", target: '[data-view="view-turnos"]', action: ()=>navTo("view-turnos") },
    { title: "Cambiar contraseña", desc: "Podes cambiar tu contraseña en cualquier momento desde el boton Clave arriba a la derecha.", target: "#user-display" },
    { title: "Listo!", desc: "Ya conoces tu panel. Si tenes dudas, explora cada seccion.", target: ".header-logo" },
  ];
}

function tutorialStart() {
  _tutSteps = (_isMedico) ? _tutStepsMedico() : _tutStepsAdmin();
  _tutStep = 0;
  $("tutorial-overlay").style.display = "block";
  _tutRender();
}

function tutorialNext() {
  _tutStep++;
  if (_tutStep >= _tutSteps.length) { tutorialSkip(); return; }
  _tutRender();
}

function tutorialSkip() {
  $("tutorial-overlay").style.display = "none";
  localStorage.setItem("tutorial_done", "1");
  navTo("view-dashboard");
}

function _tutRender() {
  const step = _tutSteps[_tutStep];
  if (step.action) step.action();
  $("tutorial-step").textContent = `Paso ${_tutStep + 1} de ${_tutSteps.length}`;
  $("tutorial-title").textContent = step.title;
  $("tutorial-desc").textContent = step.desc;
  $("tutorial-next-btn").textContent = _tutStep === _tutSteps.length - 1 ? "Finalizar" : "Siguiente";

  const target = document.querySelector(step.target);
  const highlight = $("tutorial-highlight");
  const tooltip = $("tutorial-tooltip");

  if (target) {
    const r = target.getBoundingClientRect();
    const pad = 6;
    highlight.style.display = "block";
    highlight.style.top = (r.top - pad) + "px";
    highlight.style.left = (r.left - pad) + "px";
    highlight.style.width = (r.width + pad * 2) + "px";
    highlight.style.height = (r.height + pad * 2) + "px";

    // Posicionar tooltip debajo o a la derecha del target
    const ttW = 340, ttH = 200;
    let ttTop = r.bottom + 16;
    let ttLeft = r.left;
    if (ttTop + ttH > window.innerHeight) ttTop = r.top - ttH - 16;
    if (ttLeft + ttW > window.innerWidth) ttLeft = window.innerWidth - ttW - 20;
    if (ttLeft < 10) ttLeft = 10;
    tooltip.style.top = Math.max(10, ttTop) + "px";
    tooltip.style.left = ttLeft + "px";
  } else {
    highlight.style.display = "none";
    tooltip.style.top = "50%";
    tooltip.style.left = "50%";
    tooltip.style.transform = "translate(-50%,-50%)";
  }
}

$("tutorial-next-btn")?.addEventListener("click", () => tutorialNext());
$("tutorial-skip-btn")?.addEventListener("click", () => tutorialSkip());
