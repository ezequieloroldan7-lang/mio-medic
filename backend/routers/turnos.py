import csv
import io
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

import models
import schemas
from audit import audit, _diff_dict
from auth import get_current_user
from database import get_db, SessionLocal
from whatsapp import enviar_turno_agendado
import gcalendar

log = logging.getLogger("miomedic.turnos")

router = APIRouter(prefix="/turnos", tags=["turnos"])

HORA_INICIO = time(9, 0)
HORA_FIN    = time(19, 30)
DURACIONES_VALIDAS = (30, 45, 60, 90)
CONSULTORIOS_VALIDOS = (1, 2)

# Pool para enviar WhatsApp y sync GCal en background sin bloquear la respuesta HTTP
_bg_pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="bg")


def _sync_gcal_crear(turno_id: int, calendar_id: str):
    """Background: crea evento en GCal y guarda el event_id en el turno."""
    db = SessionLocal()
    try:
        t = _with_rel(db.query(models.Turno)).filter(models.Turno.id == turno_id).first()
        if not t:
            return
        event_id = gcalendar.crear_evento(calendar_id, t, t.paciente, t.medico)
        if event_id:
            t.google_event_id = event_id
            db.commit()
    except Exception as e:  # noqa: BLE001
        log.error("Error sync GCal crear turno %s: %s", turno_id, e)
    finally:
        db.close()


def _sync_gcal_actualizar(turno_id: int, calendar_id: str, event_id: str):
    """Background: actualiza evento en GCal."""
    db = SessionLocal()
    try:
        t = _with_rel(db.query(models.Turno)).filter(models.Turno.id == turno_id).first()
        if not t:
            return
        gcalendar.actualizar_evento(calendar_id, event_id, t, t.paciente, t.medico)
    except Exception as e:  # noqa: BLE001
        log.error("Error sync GCal actualizar turno %s: %s", turno_id, e)
    finally:
        db.close()


# ── Helpers ──────────────────────────────────────────────────
def _with_rel(q):
    return q.options(
        joinedload(models.Turno.paciente),
        joinedload(models.Turno.medico).joinedload(models.Medico.especialidad),
    )


def _validar_horario(dt: datetime):
    if dt.weekday() >= 5:
        raise HTTPException(400, "Solo se pueden agendar turnos de lunes a viernes.")
    if dt.time() < HORA_INICIO or dt.time() > HORA_FIN:
        raise HTTPException(400, "El horario de atención es de 09:00 a 19:30.")


def _hay_solapamiento(db: Session, consultorio: int, inicio: datetime,
                      duracion: int, turno_id: Optional[int] = None) -> bool:
    """Chequea si hay otro turno activo que solape el rango [inicio, inicio+duracion)."""
    fin = inicio + timedelta(minutes=duracion)
    # Traer solo los que podrían solapar (mismo día, mismo consultorio, no cancelados)
    dia_ini = inicio.replace(hour=0, minute=0, second=0, microsecond=0)
    dia_fin = dia_ini + timedelta(days=1)
    q = db.query(models.Turno).filter(
        models.Turno.consultorio == consultorio,
        models.Turno.estado != models.EstadoTurno.cancelado,
        models.Turno.fecha_hora_inicio >= dia_ini,
        models.Turno.fecha_hora_inicio <  dia_fin,
    )
    if turno_id is not None:
        q = q.filter(models.Turno.id != turno_id)
    for t in q.all():
        t_fin = t.fecha_hora_inicio + timedelta(minutes=t.duracion_minutos)
        if t.fecha_hora_inicio < fin and t_fin > inicio:
            return True
    return False


def _normalizar_duracion(d: int) -> int:
    if d not in DURACIONES_VALIDAS:
        raise HTTPException(400, f"Duración inválida. Valores aceptados: {DURACIONES_VALIDAS}")
    return d


def _normalizar_consultorio(c: int) -> int:
    if c not in CONSULTORIOS_VALIDOS:
        raise HTTPException(400, "Consultorio debe ser 1 o 2.")
    return c


# ── LIST ─────────────────────────────────────────────────────
@router.get("/", response_model=List[schemas.TurnoOut])
def listar_turnos(
    fecha:       Optional[date] = Query(None),
    desde:       Optional[date] = Query(None, description="Rango desde (inclusive)"),
    hasta:       Optional[date] = Query(None, description="Rango hasta (inclusive)"),
    consultorio: Optional[int]  = Query(None),
    medico_id:   Optional[int]  = Query(None),
    estado:      Optional[models.EstadoTurno] = Query(None),
    db: Session = Depends(get_db),
):
    q = _with_rel(db.query(models.Turno))

    if fecha:
        ini = datetime.combine(fecha, time.min)
        fin = datetime.combine(fecha, time.max)
        q = q.filter(models.Turno.fecha_hora_inicio.between(ini, fin))
    elif desde or hasta:
        if desde:
            q = q.filter(models.Turno.fecha_hora_inicio >= datetime.combine(desde, time.min))
        if hasta:
            q = q.filter(models.Turno.fecha_hora_inicio <= datetime.combine(hasta, time.max))

    if consultorio:
        q = q.filter(models.Turno.consultorio == consultorio)
    if medico_id:
        q = q.filter(models.Turno.medico_id == medico_id)
    if estado:
        q = q.filter(models.Turno.estado == estado)

    return q.order_by(models.Turno.fecha_hora_inicio).all()


# ── STATS (dashboard) ────────────────────────────────────────
@router.get("/stats")
def stats(
    desde: Optional[date] = Query(None),
    hasta: Optional[date] = Query(None),
    db: Session = Depends(get_db),
):
    """
    Devuelve métricas agregadas útiles para dashboards.
    Si no se pasan fechas, usa el mes actual.
    """
    hoy = date.today()
    if not desde:
        desde = hoy.replace(day=1)
    if not hasta:
        hasta = hoy

    ini = datetime.combine(desde, time.min)
    fin = datetime.combine(hasta, time.max)

    base = db.query(models.Turno).filter(
        models.Turno.fecha_hora_inicio.between(ini, fin)
    )

    # Por estado
    por_estado = dict(
        db.query(models.Turno.estado, func.count(models.Turno.id))
          .filter(models.Turno.fecha_hora_inicio.between(ini, fin))
          .group_by(models.Turno.estado)
          .all()
    )
    por_estado_out = {e.value: por_estado.get(e, 0) for e in models.EstadoTurno}

    # Por médico
    por_medico = (
        db.query(models.Medico.id, models.Medico.apellido, models.Medico.nombre,
                 func.count(models.Turno.id))
          .join(models.Turno, models.Turno.medico_id == models.Medico.id)
          .filter(models.Turno.fecha_hora_inicio.between(ini, fin))
          .group_by(models.Medico.id)
          .order_by(func.count(models.Turno.id).desc())
          .all()
    )

    # Por financiador
    por_financiador = (
        db.query(models.Paciente.financiador, func.count(models.Turno.id))
          .join(models.Turno, models.Turno.paciente_id == models.Paciente.id)
          .filter(models.Turno.fecha_hora_inicio.between(ini, fin))
          .group_by(models.Paciente.financiador)
          .order_by(func.count(models.Turno.id).desc())
          .all()
    )

    total = base.count()
    activos = base.filter(models.Turno.estado != models.EstadoTurno.cancelado).count()

    return {
        "desde": desde.isoformat(),
        "hasta": hasta.isoformat(),
        "total": total,
        "activos": activos,
        "por_estado": por_estado_out,
        "por_medico": [
            {"medico_id": mid, "nombre": f"{ap}, {nm}", "cantidad": c}
            for mid, ap, nm, c in por_medico
        ],
        "por_financiador": [
            {"financiador": (fin or "Sin especificar"), "cantidad": c}
            for fin, c in por_financiador
        ],
    }


# ── EXPORT CSV ───────────────────────────────────────────────
@router.get("/export.csv")
def export_csv(
    desde: Optional[date] = Query(None),
    hasta: Optional[date] = Query(None),
    db: Session = Depends(get_db),
):
    """Exporta los turnos del rango (o últimos 30 días) a CSV."""
    if not hasta:
        hasta = date.today()
    if not desde:
        desde = hasta - timedelta(days=30)

    q = _with_rel(db.query(models.Turno)).filter(
        models.Turno.fecha_hora_inicio.between(
            datetime.combine(desde, time.min),
            datetime.combine(hasta, time.max),
        )
    ).order_by(models.Turno.fecha_hora_inicio)

    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow([
        "ID", "Fecha", "Hora", "Consultorio", "Duración (min)",
        "Paciente", "DNI", "HC", "Teléfono", "Financiador", "Plan",
        "Profesional", "Especialidad", "Estado", "WhatsApp enviado", "Observaciones",
    ])
    for t in q.all():
        p = t.paciente
        m = t.medico
        w.writerow([
            t.id,
            t.fecha_hora_inicio.strftime("%Y-%m-%d"),
            t.fecha_hora_inicio.strftime("%H:%M"),
            t.consultorio,
            t.duracion_minutos,
            f"{p.apellido}, {p.nombre}" if p else "",
            (p.dni if p else "") or "",
            (p.nro_hc if p else "") or "",
            (p.telefono if p else "") or "",
            (p.financiador if p else "") or "",
            (p.plan if p else "") or "",
            f"{m.apellido}, {m.nombre}" if m else "",
            (m.especialidad.nombre if m and m.especialidad else "") or "",
            t.estado.value if t.estado else "",
            "sí" if t.whatsapp_enviado else "no",
            (t.observaciones or "").replace("\n", " "),
        ])
    buf.seek(0)
    filename = f"turnos_{desde.isoformat()}_{hasta.isoformat()}.csv"
    # BOM para que Excel detecte UTF-8
    data = "\ufeff" + buf.getvalue()
    return StreamingResponse(
        iter([data]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── DETAIL ───────────────────────────────────────────────────
@router.get("/{turno_id}", response_model=schemas.TurnoOut)
def obtener_turno(turno_id: int, db: Session = Depends(get_db)):
    t = _with_rel(db.query(models.Turno)).filter(models.Turno.id == turno_id).first()
    if not t:
        raise HTTPException(404, "Turno no encontrado")
    return t


_TURNO_AUDIT_FIELDS = [
    "paciente_id", "medico_id", "consultorio",
    "fecha_hora_inicio", "duracion_minutos", "estado", "observaciones",
]


# ── CREATE ───────────────────────────────────────────────────
@router.post("/", response_model=schemas.TurnoOut, status_code=201)
def crear_turno(
    data: schemas.TurnoCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    _validar_horario(data.fecha_hora_inicio)
    _normalizar_consultorio(data.consultorio)
    _normalizar_duracion(data.duracion_minutos)

    if not db.query(models.Paciente.id).filter(models.Paciente.id == data.paciente_id).first():
        raise HTTPException(404, "Paciente inexistente.")
    if not db.query(models.Medico.id).filter(models.Medico.id == data.medico_id).first():
        raise HTTPException(404, "Médico inexistente.")

    if _hay_solapamiento(db, data.consultorio, data.fecha_hora_inicio, data.duracion_minutos):
        raise HTTPException(409, "Ya existe un turno en ese consultorio y horario.")

    t = models.Turno(**data.model_dump())
    db.add(t); db.flush()
    audit(db, request, "turno.create", user=user,
          entity_type="turno", entity_id=t.id,
          details={
              "paciente_id": t.paciente_id, "medico_id": t.medico_id,
              "consultorio": t.consultorio,
              "fecha_hora_inicio": t.fecha_hora_inicio.isoformat(),
              "duracion_minutos": t.duracion_minutos,
          })
    db.commit()
    db.refresh(t)
    log.info("Turno creado id=%s paciente=%s medico=%s fecha=%s",
             t.id, t.paciente_id, t.medico_id, t.fecha_hora_inicio)

    # ── Background: WhatsApp + Google Calendar ─────────────
    turno_completo = obtener_turno(t.id, db)
    p = turno_completo.paciente
    m = turno_completo.medico

    # WhatsApp al paciente
    if p and p.telefono:
        nombre   = f"{p.nombre} {p.apellido}"
        medico_n = f"Dr/a. {m.nombre} {m.apellido}" if m else ""
        esp      = m.especialidad.nombre if m and m.especialidad else ""
        fecha_hr = t.fecha_hora_inicio.strftime("%d/%m/%Y a las %H:%M hs")
        _bg_pool.submit(
            enviar_turno_agendado,
            nombre, p.telefono, fecha_hr, medico_n, esp,
            t.consultorio, t.duracion_minutos,
        )

    # Google Calendar del médico
    if m and m.google_calendar_id:
        _bg_pool.submit(_sync_gcal_crear, t.id, m.google_calendar_id)

    return turno_completo


# ── UPDATE ───────────────────────────────────────────────────
@router.put("/{turno_id}", response_model=schemas.TurnoOut)
def actualizar_turno(
    turno_id: int,
    data: schemas.TurnoUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    t = db.query(models.Turno).filter(models.Turno.id == turno_id).first()
    if not t:
        raise HTTPException(404, "Turno no encontrado")

    payload = data.model_dump(exclude_none=True)
    before = {k: getattr(t, k) for k in _TURNO_AUDIT_FIELDS}

    # Si cambian datos que afectan solapamiento, revalidamos
    nuevo_consultorio = payload.get("consultorio", t.consultorio)
    nueva_fecha       = payload.get("fecha_hora_inicio", t.fecha_hora_inicio)
    nueva_duracion    = payload.get("duracion_minutos",  t.duracion_minutos)
    nuevo_estado      = payload.get("estado", t.estado)

    if "consultorio" in payload or "fecha_hora_inicio" in payload or "duracion_minutos" in payload:
        _validar_horario(nueva_fecha)
        _normalizar_consultorio(nuevo_consultorio)
        _normalizar_duracion(nueva_duracion)
        if nuevo_estado != models.EstadoTurno.cancelado and _hay_solapamiento(
            db, nuevo_consultorio, nueva_fecha, nueva_duracion, turno_id=turno_id
        ):
            raise HTTPException(409, "Ya existe un turno en ese consultorio y horario.")

    for k, v in payload.items():
        setattr(t, k, v)
    after = {k: getattr(t, k) for k in _TURNO_AUDIT_FIELDS}
    diff = _diff_dict(before, after, _TURNO_AUDIT_FIELDS)
    if diff:
        audit(db, request, "turno.update", user=user,
              entity_type="turno", entity_id=t.id, details={"diff": diff})
    db.commit()
    db.refresh(t)

    # ── GCal: actualizar evento si existe ────────────────────
    m = db.query(models.Medico).filter(models.Medico.id == t.medico_id).first()
    if m and m.google_calendar_id and t.google_event_id:
        _bg_pool.submit(_sync_gcal_actualizar, t.id, m.google_calendar_id, t.google_event_id)

    return obtener_turno(turno_id, db)


# ── SOFT CANCEL ──────────────────────────────────────────────
@router.delete("/{turno_id}/cancelar", status_code=204)
def cancelar_turno(
    turno_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    t = db.query(models.Turno).filter(models.Turno.id == turno_id).first()
    if not t:
        raise HTTPException(404, "Turno no encontrado")
    t.estado = models.EstadoTurno.cancelado
    # GCal: cancelar evento
    m = db.query(models.Medico).filter(models.Medico.id == t.medico_id).first()
    gcal_id = m.google_calendar_id if m else None
    event_id = t.google_event_id
    audit(db, request, "turno.cancel", user=user, entity_type="turno", entity_id=t.id)
    db.commit()
    log.info("Turno id=%s cancelado", turno_id)
    if gcal_id and event_id:
        _bg_pool.submit(gcalendar.cancelar_evento, gcal_id, event_id)


# ── HARD DELETE ──────────────────────────────────────────────
@router.delete("/{turno_id}", status_code=204)
def eliminar_turno(
    turno_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    t = db.query(models.Turno).filter(models.Turno.id == turno_id).first()
    if not t:
        raise HTTPException(404, "Turno no encontrado")
    # Guardar datos GCal antes del delete
    m = db.query(models.Medico).filter(models.Medico.id == t.medico_id).first()
    gcal_id = m.google_calendar_id if m else None
    event_id = t.google_event_id
    audit(db, request, "turno.delete", user=user,
          entity_type="turno", entity_id=t.id,
          details={
              "paciente_id": t.paciente_id,
              "medico_id": t.medico_id,
              "fecha_hora_inicio": t.fecha_hora_inicio.isoformat(),
          })
    db.delete(t)
    db.commit()
    log.info("Turno id=%s ELIMINADO permanentemente", turno_id)
    if gcal_id and event_id:
        _bg_pool.submit(gcalendar.eliminar_evento, gcal_id, event_id)
