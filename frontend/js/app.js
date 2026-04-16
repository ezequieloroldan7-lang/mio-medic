/* app.js — MIO MEDIC v9 */
const API = "";
const DIAS = ["Lunes","Martes","Miércoles","Jueves","Viernes"];

let medicos = [], especialidades = [], pacientes = [];
let turnoEditing = null, pacienteEditing = null, medicoEditing = null, horarioParaMedicoId = null;

/* Estado de ordenamiento de la tabla de pacientes */
let pacSort = { key: "apellido", dir: "asc" };

/* ── Auth ──────────────────────────────────────────────── */
const currentUser = JSON.parse(localStorage.getItem("user") || "null");
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
  const el=document.createElement("div"); el.className=`toast ${type}`; el.textContent=msg;
  $("toast-container").appendChild(el); setTimeout(()=>el.remove(),3500);
}
function logout() { localStorage.removeItem("token"); localStorage.removeItem("user"); window.location.href="/login"; }

async function api(path, opts={}) {
  const url = API + path;
  const token = localStorage.getItem("token");
  const headers = {"Content-Type":"application/json"};
  if (token) headers["Authorization"] = "Bearer " + token;
  const res = await fetch(url, { headers, cache: "no-store", ...opts });
  if(res.status===401){ logout(); return; }
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
    input.value = `${p.apellido} ${p.nombre}`;
    hidden.value = p.id;
    drop.style.display = "none";
    if ($("turno-financiador")) $("turno-financiador").value = p.financiador || "";
    if ($("turno-plan")) $("turno-plan").value = p.plan || "";
    if ($("btn-agregar-paciente")) $("btn-agregar-paciente").style.display = "none";
    // Mostrar info del paciente
    const infoEl = $("turno-pac-info");
    if (infoEl) {
      const parts = [];
      if (p.nro_hc) parts.push(`HC: <span>${esc(p.nro_hc)}</span>`);
      if (p.dni) parts.push(`DNI: <span>${esc(p.dni)}</span>`);
      if (p.telefono) parts.push(`Tel: <span>${esc(p.telefono)}</span>`);
      infoEl.innerHTML = parts.join(" &nbsp;|&nbsp; ");
      infoEl.style.display = parts.length ? "flex" : "none";
    }
    // Ocultar campos de paciente nuevo
    if ($("turno-new-pac-fields")) $("turno-new-pac-fields").classList.remove("open");
  }

  function renderDrop(lista) {
    if (!lista.length) { drop.style.display="none"; return; }
    drop.innerHTML = lista.map(p => {
      const hc = p.nro_hc ? ` · HC ${esc(p.nro_hc)}` : "";
      const label = `${esc(p.apellido)} ${esc(p.nombre)}`;
      return `<div class="pac-ac-item" data-id="${p.id}" data-label="${label}">
        <span class="pac-ac-nombre">${esc(p.apellido)}, ${esc(p.nombre)}</span>
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

  input.addEventListener("input", function() {
    const q = this.value.trim().toLowerCase();
    hidden.value = "";
    if (!q) { drop.style.display="none"; if($("btn-agregar-paciente"))$("btn-agregar-paciente").style.display="none"; return; }
    const filtered = pacientes.filter(p =>
      p.apellido.toLowerCase().includes(q) ||
      p.nombre.toLowerCase().includes(q)   ||
      (p.nro_hc && p.nro_hc.toLowerCase().includes(q))
    ).slice(0, 12);
    renderDrop(filtered);
    // Mostrar boton agregar paciente si no hay resultados exactos
    if ($("btn-agregar-paciente")) {
      $("btn-agregar-paciente").style.display = filtered.length === 0 ? "inline-block" : "none";
    }
  });

  input.addEventListener("focus", function() {
    if (this.value.trim()) this.dispatchEvent(new Event("input"));
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
  document.querySelectorAll(".nav-item").forEach(n=>n.classList.remove("active"));
  $(view).classList.add("active");
  document.querySelector(`[data-view="${view}"]`)?.classList.add("active");
  document.querySelector(".sidebar").classList.remove("open");
  $("btn-fab").style.display = view==="view-agenda" ? "flex" : "none";
  if(view==="view-agenda")        renderAgenda();
  if(view==="view-pacientes")     renderPacientes();
  if(view==="view-turnos")        renderTurnos();
  if(view==="view-dashboard")     renderDashboard();
  if(view==="view-profesionales") renderProfesionales();
}
document.querySelectorAll(".nav-item[data-view]").forEach(el=>el.addEventListener("click",()=>navTo(el.dataset.view)));
$("menu-toggle").addEventListener("click",()=>{
  document.querySelector(".sidebar").classList.toggle("open");
});
// Cerrar sidebar al tocar fuera (mobile)
document.querySelector(".main").addEventListener("click",()=>{
  document.querySelector(".sidebar").classList.remove("open");
});

/* ── Init ───────────────────────────────────────────────── */
async function init() {
  // Mostrar nombre de usuario
  if (currentUser) {
    if ($("user-display")) $("user-display").textContent = currentUser.display_name;
    if ($("sidebar-user-name")) $("sidebar-user-name").textContent = currentUser.display_name;
  }

  // Si es medico, ocultar secciones que no corresponden
  if (currentUser && currentUser.role === "medico") {
    document.querySelectorAll('[data-view="view-pacientes"],[data-view="view-profesionales"]').forEach(el=>el.style.display="none");
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
          if (p?.financiador) info.push(p.financiador + (p.plan ? " — "+p.plan : ""));
          if (p?.telefono) info.push(p.telefono);
          const infoHtml = info.length ? `<div class="dash-turno-info">${info.map(i=>`<span>${esc(i)}</span>`).join("")}</div>` : "";
          return `<div class="dash-turno-card" onclick="abrirEditarTurno(${t.id})">
            <span class="dash-turno-hora">${fmtHoraDisplay(t.fecha_hora_inicio)}</span>
            <span class="dash-turno-paciente">${esc(p?.apellido)}, ${esc(p?.nombre)}</span>
            <span class="dash-turno-consultorio">C${t.consultorio}</span>
            <span class="dash-turno-medico">${esc(t.medico?.apellido)}</span>
            <span class="badge badge-${t.estado}">${t.estado}</span>
            <span class="dash-turno-actions"><button class="btn btn-sm btn-outline" onclick="event.stopPropagation();abrirEditarTurno(${t.id})">Editar</button></span>
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
  $("agenda-titulo").textContent=fmtFecha(fecha+"T12:00:00");
  const turnos_raw=await api(`/turnos?fecha=${fecha}`);
  const turnos=_filtrarPorRol(turnos_raw);
  const activos=turnos.filter(t=>t.estado!=="cancelado");
  renderColumna(1,activos.filter(t=>t.consultorio===1),fecha);
  renderColumna(2,activos.filter(t=>t.consultorio===2),fecha);
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

function renderColumna(consultorio, turnos, fecha) {
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

  // Render de los slots (siempre todos, para mantener la grilla horaria)
  horas.forEach((hora, i) => {
    const esExacta = hora.endsWith(":00");
    const slot = document.createElement("div");
    slot.className = "time-slot"
      + (esExacta ? " slot-exacta" : "")
      + (cubiertos.has(i) ? " slot-cubierto" : "");
    slot.innerHTML = `<span class="time-label${esExacta ? " exacta" : ""}">${hora}</span><span class="time-content"></span>`;
    if (!turnosPorSlot.has(i) && !cubiertos.has(i)) {
      slot.addEventListener("click", () => abrirNuevoTurno(consultorio, `${fecha}T${hora}`));
    }
    grid.appendChild(slot);
  });

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

function chipInnerHTML(t) {
  const hc   = t.paciente?.nro_hc ? `HC ${esc(t.paciente.nro_hc)}` : "";
  const prof = `Dr/a. ${esc(t.medico?.apellido || "")}`;
  const esp  = esc(t.medico?.especialidad?.nombre || "");
  const hIni = fmtHora(t.fecha_hora_inicio);
  const hFin = (() => {
    const d = new Date(t.fecha_hora_inicio);
    d.setMinutes(d.getMinutes() + (t.duracion_minutos || 0));
    return `${String(d.getHours()).padStart(2,"0")}:${String(d.getMinutes()).padStart(2,"0")}`;
  })();
  const obsIcon = t.observaciones ? ` <span title="${esc(t.observaciones)}" style="opacity:.8">📝</span>` : "";
  return `
    <span class="chip-nombre">${esc(t.paciente?.apellido)}, ${esc(t.paciente?.nombre)}${obsIcon}</span>
    <span class="chip-hc">${hc}${hc ? " · " : ""}${hIni}–${hFin}</span>
    <span class="chip-esp">${prof} · ${esp}</span>`;
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
async function renderTurnos(q="") {
  const fecha=$("filtro-fecha")?.value||"";
  const turnos_raw=await api("/turnos?"+(fecha?`fecha=${fecha}&`:""));
  const turnos=_filtrarPorRol(turnos_raw);
  const f=q?turnos.filter(t=>`${t.paciente?.apellido} ${t.paciente?.nombre}`.toLowerCase().includes(q.toLowerCase())):turnos;
  $("tabla-turnos").innerHTML=f.length===0
    ?`<div style="text-align:center;color:var(--muted);padding:2rem">Sin turnos</div>`
    :f.map(t=>{
      const p=t.paciente;
      const obs=t.observaciones?`<div class="dash-turno-obs">${esc(t.observaciones)}</div>`:"";
      const info=[];
      if(p?.nro_hc)info.push(`HC: ${esc(p.nro_hc)}`);
      if(p?.dni)info.push(`DNI: ${esc(p.dni)}`);
      if(p?.telefono)info.push(`WhatsApp: ${esc(p.telefono)}`);
      if(p?.financiador)info.push(p.financiador+(p.plan?" — "+p.plan:""));
      if(p?.email)info.push(p.email);
      const infoHtml=info.length?`<div class="dash-turno-info">${info.map(i=>`<span>${i}</span>`).join("")}</div>`:"";
      return `<div class="dash-turno-card" onclick="abrirEditarTurno(${t.id})">
        <span class="dash-turno-hora">${fmtFechaCorta(t.fecha_hora_inicio)} ${fmtHoraDisplay(t.fecha_hora_inicio)}</span>
        <span class="dash-turno-paciente">${esc(p?.apellido)}, ${esc(p?.nombre)}</span>
        <span class="dash-turno-consultorio">C${t.consultorio}</span>
        <span class="dash-turno-medico">${esc(t.medico?.nombre)} ${esc(t.medico?.apellido)} — ${esc(t.medico?.especialidad?.nombre||"")}</span>
        <span class="badge badge-${t.estado}">${t.estado}</span>
        <span class="dash-turno-actions" onclick="event.stopPropagation()">
          <button class="btn btn-sm btn-primary" onclick="abrirEditarTurno(${t.id})">Reprogramar</button>
          <button class="btn btn-sm btn-outline" onclick="cancelarTurno(${t.id})" style="color:var(--warning);border-color:var(--warning)">Cancelar</button>
          <button class="btn btn-sm btn-danger" onclick="eliminarTurno(${t.id})">Eliminar</button>
        </span>
        ${infoHtml}
        ${obs}
      </div>`;
    }).join("");
}
$("filtro-fecha")?.addEventListener("change",()=>renderTurnos());
$("filtro-buscar-turno")?.addEventListener("input",e=>renderTurnos(e.target.value));

/* ── Exportar CSV ───────────────────────────────────────── */
function exportarTurnosCSV() {
  const desde = prompt("Desde (YYYY-MM-DD). Dejar vacío para últimos 30 días:") || "";
  const hasta = prompt("Hasta (YYYY-MM-DD). Dejar vacío para hoy:") || "";
  const qs = [];
  if (desde) qs.push("desde=" + encodeURIComponent(desde));
  if (hasta) qs.push("hasta=" + encodeURIComponent(hasta));
  window.open("/turnos/export.csv" + (qs.length?("?"+qs.join("&")):""), "_blank");
}
window.exportarTurnosCSV = exportarTurnosCSV;

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

async function renderPacientes(q="") {
  try {
    const lista = await api(q ? `/pacientes?q=${encodeURIComponent(q)}` : "/pacientes");
    const ordenados = _ordenarPacientes(lista);
    $("pacientes-count").textContent = `${lista.length} pacientes`;
    $("tabla-pacientes").innerHTML = ordenados.length === 0
      ? `<div style="text-align:center;color:var(--muted);padding:2rem">Sin resultados</div>`
      : ordenados.map(p => {
          const info = [];
          if (p.nro_hc) info.push(`HC: ${esc(p.nro_hc)}`);
          if (p.dni) info.push(`DNI: ${esc(p.dni)}`);
          if (p.telefono) info.push(esc(p.telefono));
          if (p.email) info.push(esc(p.email));
          if (p.financiador) info.push(p.financiador + (p.plan ? " — " + p.plan : ""));
          const infoStr = info.length ? `<div class="dash-turno-info">${info.map(i=>`<span>${i}</span>`).join("")}</div>` : "";
          return `<div class="dash-turno-card pac-card">
            <div class="pac-card-top">
              <span class="pac-card-nombre">${esc(p.apellido)}, ${esc(p.nombre)}</span>
              <span class="pac-card-btns">
                <button class="btn btn-sm btn-primary" onclick="abrirNuevoTurnoPaciente(${p.id})">Turno</button>
                <button class="btn btn-sm btn-outline" onclick="abrirEditarPaciente(${p.id})">Editar</button>
                <button class="btn btn-sm btn-danger" onclick="eliminarPaciente(${p.id})">Eliminar</button>
              </span>
            </div>
            ${infoStr}
          </div>`;
        }).join("");
          </div>`;
        }).join("");
  } catch (e) { toast("Error al cargar pacientes: " + e.message, "error"); }
}
$("buscar-paciente").addEventListener("input", e => renderPacientes(e.target.value));

/* ── Profesionales ──────────────────────────────────────── */
async function renderProfesionales() {
  try {
    medicos = await api("/medicos");
    const grid = $("prof-grid");
    if (!medicos.length) {
      grid.innerHTML = `<div class="empty-state"><span class="empty-state-icon">✦</span>No hay profesionales registrados</div>`;
      return;
    }
    grid.innerHTML = medicos.map(m => {
      const iniciales = ((m.nombre[0] || "") + (m.apellido[0] || "")).toUpperCase();
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
          </div>
          ${infoRows.length ? `<div class="prof-info">${infoRows.join("")}</div>` : ""}
          <div class="prof-section">
            <div class="prof-section-label">Horarios de atención</div>
            <div class="horario-list">${renderHorariosPills(m.horarios || [], m.id)}</div>
          </div>
          <div class="prof-actions">
            <button class="btn btn-sm btn-outline" onclick="abrirAgregarHorario(${m.id})">+ Horario</button>
            <button class="btn btn-sm btn-outline" onclick="abrirEditarMedico(${m.id})">Editar</button>
            <button class="btn btn-sm btn-danger" onclick="eliminarMedico(${m.id})">Eliminar</button>
          </div>
        </div>`;
    }).join("");
  } catch (e) { toast("Error al cargar profesionales: " + e.message, "error"); }
}

function renderHorariosPills(horarios, medicoId) {
  if (!horarios.length) return `<span style="font-size:.75rem;color:var(--muted);font-style:italic">Sin horarios cargados</span>`;
  return horarios.slice().sort((a, b) => a.dia_semana - b.dia_semana).map(h =>
    `<span class="horario-pill">${DIAS[h.dia_semana].slice(0,3)} · ${esc(h.hora_inicio)}–${esc(h.hora_fin)} · C${h.consultorio}
      <button onclick="eliminarHorario(${h.id})" title="Eliminar">×</button></span>`
  ).join("");
}

/* ── Link Calendario iCal ────────────────────────────────── */
function copiarLinkCalendario(medicoId) {
  const url = `${location.origin}/medicos/${medicoId}/calendario.ics`;
  navigator.clipboard.writeText(url).then(() => {
    toast("Link del calendario copiado al portapapeles. Pegalo en Google Calendar → Otros calendarios → Desde URL.", "success");
  }).catch(() => {
    // Fallback si clipboard no disponible (http sin https)
    prompt("Copiá este link y pegalo en Google Calendar → Otros calendarios → Desde URL:", url);
  });
}
window.copiarLinkCalendario = copiarLinkCalendario;

/* ── Modal Médico ────────────────────────────────────────── */
function abrirNuevoMedico() {
  medicoEditing=null; $("modal-medico-titulo").textContent="Nuevo Profesional";
  ["med-nombre","med-apellido","med-matricula","med-telefono","med-email","med-gcal"].forEach(id=>$(id).value="");
  $("med-especialidad").value=""; $("modal-medico").classList.add("open");
}
async function abrirEditarMedico(id) {
  const m=await api(`/medicos/${id}`); medicoEditing=id;
  $("modal-medico-titulo").textContent="Editar Profesional";
  $("med-nombre").value=m.nombre; $("med-apellido").value=m.apellido;
  $("med-especialidad").value=m.especialidad_id; $("med-matricula").value=m.matricula||"";
  $("med-telefono").value=m.telefono||""; $("med-email").value=m.email||"";
  $("med-gcal").value=m.google_calendar_id||"";
  $("modal-medico").classList.add("open");
}
async function guardarMedico() {
  const body={nombre:$("med-nombre").value.trim(),apellido:$("med-apellido").value.trim(),especialidad_id:parseInt($("med-especialidad").value),matricula:$("med-matricula").value.trim()||null,telefono:$("med-telefono").value.trim()||null,email:$("med-email").value.trim()||null,google_calendar_id:$("med-gcal").value.trim()||null};
  if(!body.nombre||!body.apellido||!body.especialidad_id){toast("Nombre, apellido y especialidad son obligatorios.","error");return;}
  try{
    if(medicoEditing){await api(`/medicos/${medicoEditing}`,{method:"PUT",body:JSON.stringify(body)});toast("Profesional actualizado ✓","success");}
    else{await api("/medicos",{method:"POST",body:JSON.stringify(body)});toast("Profesional creado ✓","success");}
    cerrarModal("modal-medico"); medicos=await api("/medicos"); populateSelects(); renderProfesionales();
  }catch(e){toast(e.message,"error");}
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
  $("modal-horario").classList.add("open");
}
async function guardarHorario() {
  const body={dia_semana:parseInt($("hor-dia").value),hora_inicio:$("hor-inicio").value,hora_fin:$("hor-fin").value,consultorio:parseInt($("hor-consultorio").value)};
  if(!body.hora_inicio||!body.hora_fin||body.hora_fin<=body.hora_inicio){toast("Horario inválido.","error");return;}
  try{
    await api(`/medicos/${horarioParaMedicoId}/horarios`,{method:"POST",body:JSON.stringify(body)});
    toast("Horario agregado ✓","success"); cerrarModal("modal-horario"); medicos=await api("/medicos"); renderProfesionales();
  }catch(e){toast(e.message,"error");}
}
async function eliminarHorario(horarioId) {
  if(!confirm("¿Eliminar este horario?"))return;
  try{await api(`/horarios/${horarioId}`,{method:"DELETE"});toast("Horario eliminado","success");medicos=await api("/medicos");renderProfesionales();}
  catch(e){toast(e.message,"error");}
}

/* ── Agregar paciente desde turno ───────────────────────── */
async function mostrarCamposPacienteNuevo() {
  const fields = $("turno-new-pac-fields");
  if (!fields) return;
  fields.classList.add("open");
  $("btn-agregar-paciente").textContent = "Guardar paciente";
  $("btn-agregar-paciente").onclick = agregarPacienteDesdeTurno;
  // Auto-generar HC
  try {
    const res = await api("/pacientes/next-hc");
    $("turno-new-hc").value = res.next_hc;
  } catch(e) { $("turno-new-hc").value = ""; }
  // Ocultar info de paciente existente
  if ($("turno-pac-info")) $("turno-pac-info").style.display = "none";
}

async function agregarPacienteDesdeTurno() {
  const nombre_completo = $("turno-paciente-input").value.trim();
  if (!nombre_completo) { toast("Escribi el nombre del paciente","error"); return; }
  const dni = $("turno-new-dni").value.trim();
  const tel = $("turno-new-tel").value.trim();
  if (!dni || !tel) { toast("DNI y Telefono son obligatorios para pacientes nuevos","error"); return; }
  const partes = nombre_completo.toUpperCase().split(/\s+/);
  const apellido = partes[0] || "";
  const nombre = partes.slice(1).join(" ") || "";
  const financiador = $("turno-financiador").value.trim().toUpperCase() || null;
  const plan = $("turno-plan").value.trim().toUpperCase() || null;
  const nro_hc = $("turno-new-hc").value.trim() || null;
  try {
    const nuevo = await api("/pacientes",{method:"POST",body:JSON.stringify({nombre:nombre||apellido,apellido,dni,telefono:tel,nro_hc,financiador,plan})});
    pacientes.push(nuevo);
    $("turno-paciente-id").value = nuevo.id;
    $("turno-paciente-input").value = `${nuevo.apellido} ${nuevo.nombre}`;
    $("btn-agregar-paciente").style.display = "none";
    $("turno-new-pac-fields").classList.remove("open");
    // Mostrar info
    const infoEl = $("turno-pac-info");
    if (infoEl) {
      infoEl.innerHTML = `HC: <span>${esc(nuevo.nro_hc)}</span> &nbsp;|&nbsp; DNI: <span>${esc(nuevo.dni)}</span> &nbsp;|&nbsp; Tel: <span>${esc(nuevo.telefono)}</span>`;
      infoEl.style.display = "flex";
    }
    toast("Paciente agregado a la base de datos","success");
  } catch(e) { toast(e.message,"error"); }
}

/* ── Modal Turno ─────────────────────────────────────────── */
function abrirNuevoTurno(consultorio=1, fechaHora="") {
  turnoEditing=null;
  $("modal-turno-titulo").textContent="Nuevo Turno"; $("campo-estado").style.display="none";
  $("turno-consultorio").value=consultorio; $("turno-fecha-hora").value=fechaHora;
  $("turno-paciente-input").value=""; $("turno-paciente-id").value="";
  $("turno-medico").value=""; $("turno-duracion").value="45";
  $("turno-financiador").value=""; $("turno-plan").value="";
  $("turno-obs").value="";
  $("btn-agregar-paciente").style.display="none";
  $("btn-agregar-paciente").textContent="+ Agregar paciente";
  $("btn-agregar-paciente").onclick=mostrarCamposPacienteNuevo;
  if($("turno-pac-info")) $("turno-pac-info").style.display="none";
  if($("turno-new-pac-fields")) $("turno-new-pac-fields").classList.remove("open");
  if($("turno-new-dni")) $("turno-new-dni").value="";
  if($("turno-new-tel")) $("turno-new-tel").value="";
  if($("turno-new-hc")) $("turno-new-hc").value="";
  const drop=$("turno-paciente-input-drop"); if(drop) drop.style.display="none";
  // Preseleccionar medico si es profesional
  if (currentUser && currentUser.role==="medico" && currentUser.medico_id) $("turno-medico").value=currentUser.medico_id;
  $("modal-turno").classList.add("open");
}
async function abrirEditarTurno(id) {
  const t=await api(`/turnos/${id}`); turnoEditing=id;
  $("modal-turno-titulo").textContent="Editar Turno"; $("campo-estado").style.display="flex";
  $("turno-consultorio").value=t.consultorio; $("turno-fecha-hora").value=t.fecha_hora_inicio.slice(0,16);
  $("turno-paciente-input").value=`${t.paciente?.apellido} ${t.paciente?.nombre}`;
  $("turno-paciente-id").value=t.paciente_id;
  $("turno-medico").value=t.medico_id; $("turno-duracion").value=t.duracion_minutos;
  $("turno-financiador").value=t.paciente?.financiador||""; $("turno-plan").value=t.paciente?.plan||"";
  $("turno-obs").value=t.observaciones||""; $("turno-estado").value=t.estado;
  $("btn-agregar-paciente").style.display="none";
  if($("turno-new-pac-fields")) $("turno-new-pac-fields").classList.remove("open");
  // Mostrar info del paciente
  const p=t.paciente;
  const infoEl=$("turno-pac-info");
  if(infoEl && p){
    const parts=[];
    if(p.nro_hc)parts.push(`HC: <span>${esc(p.nro_hc)}</span>`);
    if(p.dni)parts.push(`DNI: <span>${esc(p.dni)}</span>`);
    if(p.telefono)parts.push(`Tel: <span>${esc(p.telefono)}</span>`);
    infoEl.innerHTML=parts.join(" &nbsp;|&nbsp; ");
    infoEl.style.display=parts.length?"flex":"none";
  }
  $("modal-turno").classList.add("open");
}
function abrirNuevoTurnoPaciente(pacienteId) {
  abrirNuevoTurno();
  const p=pacientes.find(x=>x.id===pacienteId);
  if(p){$("turno-paciente-input").value=`${p.apellido} ${p.nombre}`;$("turno-paciente-id").value=p.id;}
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
  const pacienteId=parseInt($("turno-paciente-id").value), medicoId=parseInt($("turno-medico").value);
  if(!pacienteId||!medicoId||!$("turno-fecha-hora").value){toast("Completá todos los campos obligatorios.","error");return;}

  // Validar turno duplicado (mismo paciente mismo día)
  if (!turnoEditing) {
    const fechaTurno = $("turno-fecha-hora").value.slice(0, 10);
    try {
      const turnosDelDia = await api(`/turnos?fecha=${fechaTurno}`);
      const duplicado = turnosDelDia.find(t => t.paciente_id === pacienteId && t.estado !== "cancelado");
      if (duplicado) {
        const hora = fmtHoraDisplay(duplicado.fecha_hora_inicio);
        if (!confirm(`Este paciente ya tiene un turno el ${fechaTurno} a las ${hora}.\n\n¿Agendar otro turno de todas formas?`)) return;
      }
    } catch(e) { /* continuar si falla la validación */ }
  }

  // Validar franja horaria del profesional
  const alertaHorario = _validarHorarioMedico(medicoId, $("turno-fecha-hora").value, parseInt($("turno-consultorio").value));
  if (alertaHorario && !confirm(alertaHorario + "\n\n¿Agendar de todas formas?")) return;

  try{
    // Actualizar financiador/plan del paciente si cambiaron
    const fin=$("turno-financiador").value.trim().toUpperCase()||null;
    const plan=$("turno-plan").value.trim().toUpperCase()||null;
    const pac=pacientes.find(p=>p.id===pacienteId);
    if(pac && (fin!==pac.financiador || plan!==pac.plan)){
      await api(`/pacientes/${pacienteId}`,{method:"PUT",body:JSON.stringify({...pac,financiador:fin,plan:plan})});
      pac.financiador=fin; pac.plan=plan;
    }
    const body={paciente_id:pacienteId,medico_id:medicoId,consultorio:parseInt($("turno-consultorio").value),fecha_hora_inicio:$("turno-fecha-hora").value+":00",duracion_minutos:parseInt($("turno-duracion").value),observaciones:$("turno-obs").value||null};
    if(turnoEditing){
      await api(`/turnos/${turnoEditing}`,{method:"PUT",body:JSON.stringify({...body,estado:$("turno-estado").value})});
      toast("Turno actualizado ✓","success");
    }else{
      const turnoNuevo = await api("/turnos",{method:"POST",body:JSON.stringify(body)});
      // Mostrar resumen del turno creado
      const p = turnoNuevo?.paciente;
      const m = turnoNuevo?.medico;
      const dt = new Date(turnoNuevo.fecha_hora_inicio);
      const resumen = $("turno-creado-resumen");
      if (resumen) {
        resumen.innerHTML = `
          <div><strong>Paciente:</strong> ${esc(p?.apellido)}, ${esc(p?.nombre)}</div>
          ${p?.telefono ? `<div><strong>WhatsApp:</strong> ${esc(p.telefono)}</div>` : ""}
          <div><strong>Profesional:</strong> ${esc(m?.nombre)} ${esc(m?.apellido)} — ${esc(m?.especialidad?.nombre||"")}</div>
          <div><strong>Fecha:</strong> ${fmtFecha(turnoNuevo.fecha_hora_inicio)}</div>
          <div><strong>Hora:</strong> ${fmtHoraDisplay(turnoNuevo.fecha_hora_inicio)} — Consultorio ${turnoNuevo.consultorio}</div>
          <div><strong>Duración:</strong> ${turnoNuevo.duracion_minutos} minutos</div>
          ${turnoNuevo.observaciones ? `<div><strong>Obs:</strong> ${esc(turnoNuevo.observaciones)}</div>` : ""}
        `;
        $("modal-turno-creado").classList.add("open");
      }
    }
    cerrarModal("modal-turno"); renderAgenda(); renderDashboard();
  }catch(e){toast(e.message,"error");}
}
async function cancelarTurno(id) {
  if(!confirm("¿Cancelar este turno?"))return;
  try{
    await api(`/turnos/${id}/cancelar`,{method:"DELETE"});
    toast("Turno cancelado","success");
    document.querySelectorAll("tr").forEach(tr=>{
      if(tr.innerHTML.includes('cancelarTurno('+id+')')){
        const badge = tr.querySelector(".badge");
        if(badge){ badge.className="badge badge-cancelado"; badge.textContent="cancelado"; }
      }
    });
    renderDashboard();
  }catch(e){toast(e.message,"error");}
}

async function eliminarTurno(id) {
  if(!confirm("¿Eliminar turno #"+id+" permanentemente?"))return;
  try{
    await api("/turnos/"+id, {method:"DELETE"});
    toast("Turno eliminado ✓","success");
    document.querySelectorAll("tr").forEach(tr=>{
      if(tr.innerHTML.includes('eliminarTurno('+id+')')){
        tr.style.opacity="0.3";
        tr.style.transition="opacity 0.3s";
        setTimeout(()=>tr.remove(), 300);
      }
    });
    renderDashboard();
  }catch(e){
    toast("Error: "+e.message,"error");
  }
}

/* ── Modal Paciente ─────────────────────────────────────── */
function abrirNuevoPaciente() {
  pacienteEditing=null; $("modal-paciente-titulo").textContent="Nuevo Paciente";
  ["pac-nombre","pac-apellido","pac-tel","pac-email","pac-dni","pac-hc","pac-financiador","pac-plan","pac-deriva"].forEach(id=>$(id).value="");
  $("modal-paciente").classList.add("open");
}
async function abrirEditarPaciente(id) {
  const p=await api(`/pacientes/${id}`); pacienteEditing=id;
  $("modal-paciente-titulo").textContent="Editar Paciente";
  $("pac-nombre").value=p.nombre;$("pac-apellido").value=p.apellido;
  $("pac-tel").value=p.telefono||"";$("pac-email").value=p.email||"";
  $("pac-dni").value=p.dni||"";$("pac-hc").value=p.nro_hc||"";
  $("pac-financiador").value=p.financiador||"";$("pac-plan").value=p.plan||"";$("pac-deriva").value=p.deriva||"";
  $("modal-paciente").classList.add("open");
}
async function guardarPaciente() {
  const body={nombre:$("pac-nombre").value.trim().toUpperCase(),apellido:$("pac-apellido").value.trim().toUpperCase(),telefono:$("pac-tel").value.trim()||null,email:$("pac-email").value.trim().toLowerCase()||null,dni:$("pac-dni").value.trim()||null,nro_hc:$("pac-hc").value.trim()||null,financiador:$("pac-financiador").value.trim().toUpperCase()||null,plan:$("pac-plan").value.trim().toUpperCase()||null,deriva:$("pac-deriva").value.trim().toUpperCase()||null};
  if(!body.nombre||!body.apellido){toast("Nombre y apellido son obligatorios.","error");return;}
  try{
    if(pacienteEditing){await api(`/pacientes/${pacienteEditing}`,{method:"PUT",body:JSON.stringify(body)});toast("Paciente actualizado ✓","success");}
    else{await api("/pacientes",{method:"POST",body:JSON.stringify(body)});toast("Paciente creado ✓","success");}
    cerrarModal("modal-paciente"); pacientes=await api("/pacientes"); populateSelects(); renderPacientes();
  }catch(e){toast(e.message,"error");}
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
  if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") return;
  if (e.key === "Escape") document.querySelectorAll(".modal-overlay.open").forEach(m=>m.classList.remove("open"));
  if (e.key === "1") navTo("view-dashboard");
  if (e.key === "2") navTo("view-agenda");
  if (e.key === "3") navTo("view-turnos");
  if (e.key === "4") navTo("view-pacientes");
  if (e.key === "5") navTo("view-profesionales");
  if (e.key.toLowerCase() === "n" && document.querySelector("#view-agenda.active")) abrirNuevoTurno();
});

/* ── Helpers ─────────────────────────────────────────────── */
function cerrarModal(id){$(id).classList.remove("open");}

/* ── Cambiar contraseña ───────────────────────────────────── */
function abrirCambiarPassword() {
  $("pw-current").value=""; $("pw-new").value=""; $("pw-confirm").value="";
  $("modal-password").classList.add("open");
}
async function resetearPassword(userId, username) {
  if(!confirm(`¿Resetear la contraseña de "${username}" a "mio2026"?`))return;
  try{
    const res=await api(`/auth/users/${userId}/reset-password`,{method:"PUT"});
    toast(res.detail,"success");
  }catch(e){toast(e.message,"error");}
}

async function guardarPassword() {
  const cur=$("pw-current").value, nw=$("pw-new").value, conf=$("pw-confirm").value;
  if(!cur||!nw){toast("Completa todos los campos","error");return;}
  if(nw!==conf){toast("Las contraseñas no coinciden","error");return;}
  if(nw.length<4){toast("La contraseña debe tener al menos 4 caracteres","error");return;}
  try{
    await api("/auth/change-password",{method:"PUT",body:JSON.stringify({current_password:cur,new_password:nw})});
    toast("Contraseña actualizada","success"); cerrarModal("modal-password");
  }catch(e){toast(e.message,"error");}
}
document.querySelectorAll(".modal-overlay").forEach(m=>m.addEventListener("click",e=>{if(e.target===m)m.classList.remove("open");}));
$("btn-fab")?.addEventListener("click",()=>abrirNuevoTurno());

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
