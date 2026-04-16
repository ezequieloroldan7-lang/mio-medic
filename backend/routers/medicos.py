import logging
from datetime import date, datetime, time, timedelta
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session, joinedload

import models
import schemas
from database import get_db

log = logging.getLogger("miomedic.medicos")
router = APIRouter(tags=["medicos"])


# ── iCal helpers ─────────────────────────────────────────────
def _ical_escape(s: str) -> str:
    """Escapa texto para campos iCal (RFC 5545)."""
    return (s or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _ical_dt(dt: datetime) -> str:
    """Datetime → formato iCal local (sin Z, porque es America/Argentina/Buenos_Aires)."""
    return dt.strftime("%Y%m%dT%H%M%S")


_ESTADO_ICAL = {
    models.EstadoTurno.pendiente:  "TENTATIVE",
    models.EstadoTurno.confirmado: "CONFIRMED",
    models.EstadoTurno.cancelado:  "CANCELLED",
    models.EstadoTurno.ausente:    "CANCELLED",
    models.EstadoTurno.realizado:  "CONFIRMED",
}


# ── Especialidades ────────────────────────────────────────
@router.get("/especialidades", response_model=List[schemas.EspecialidadOut])
def listar_especialidades(db: Session = Depends(get_db)):
    return db.query(models.Especialidad).order_by(models.Especialidad.nombre).all()


@router.post("/especialidades", response_model=schemas.EspecialidadOut, status_code=201)
def crear_especialidad(nombre: str, db: Session = Depends(get_db)):
    nombre = nombre.strip()
    if not nombre:
        raise HTTPException(400, "El nombre no puede estar vacío.")
    existente = db.query(models.Especialidad).filter(models.Especialidad.nombre == nombre).first()
    if existente:
        return existente
    e = models.Especialidad(nombre=nombre)
    db.add(e); db.commit(); db.refresh(e)
    return e


# ── Médicos ───────────────────────────────────────────────
def _get_medico(id: int, db: Session) -> models.Medico:
    m = db.query(models.Medico).options(
        joinedload(models.Medico.especialidad),
        joinedload(models.Medico.horarios),
    ).filter(models.Medico.id == id).first()
    if not m:
        raise HTTPException(404, "Médico no encontrado")
    return m


@router.get("/medicos", response_model=List[schemas.MedicoOut])
def listar_medicos(db: Session = Depends(get_db)):
    return db.query(models.Medico).options(
        joinedload(models.Medico.especialidad),
        joinedload(models.Medico.horarios),
    ).order_by(models.Medico.apellido).all()


@router.get("/medicos/{medico_id}", response_model=schemas.MedicoOut)
def obtener_medico(medico_id: int, db: Session = Depends(get_db)):
    return _get_medico(medico_id, db)


@router.post("/medicos", response_model=schemas.MedicoOut, status_code=201)
def crear_medico(data: schemas.MedicoCreate, db: Session = Depends(get_db)):
    m = models.Medico(**data.model_dump())
    db.add(m); db.commit(); db.refresh(m)
    return _get_medico(m.id, db)


@router.put("/medicos/{medico_id}", response_model=schemas.MedicoOut)
def actualizar_medico(medico_id: int, data: schemas.MedicoCreate, db: Session = Depends(get_db)):
    m = db.query(models.Medico).filter(models.Medico.id == medico_id).first()
    if not m:
        raise HTTPException(404, "Médico no encontrado")
    for k, v in data.model_dump().items():
        setattr(m, k, v)
    db.commit()
    return _get_medico(medico_id, db)


@router.delete("/medicos/{medico_id}", status_code=204)
def eliminar_medico(medico_id: int, db: Session = Depends(get_db)):
    m = db.query(models.Medico).filter(models.Medico.id == medico_id).first()
    if not m:
        raise HTTPException(404, "Médico no encontrado")

    tiene_activos = db.query(models.Turno.id).filter(
        models.Turno.medico_id == medico_id,
        models.Turno.estado.in_([
            models.EstadoTurno.pendiente,
            models.EstadoTurno.confirmado,
        ]),
    ).first()
    if tiene_activos:
        raise HTTPException(
            400,
            "No se puede eliminar: tiene turnos pendientes o confirmados. Cancelalos primero.",
        )
    db.delete(m); db.commit()
    log.info("Médico id=%s eliminado", medico_id)


# ── Disponibilidad (slots libres de un médico para una fecha) ─
@router.get("/medicos/{medico_id}/disponibilidad")
def disponibilidad(
    medico_id: int,
    fecha: date = Query(..., description="Fecha a consultar"),
    duracion: int = Query(45, description="Duración del turno en minutos"),
    db: Session = Depends(get_db),
):
    """
    Devuelve los slots libres de un médico en una fecha, basado en:
    - Sus horarios cargados para ese día de la semana
    - Los turnos ya asignados en el mismo consultorio
    """
    m = _get_medico(medico_id, db)

    if fecha.weekday() >= 5:
        return {"fecha": fecha.isoformat(), "slots": [], "motivo": "Fin de semana"}

    horarios_dia = [h for h in m.horarios if h.dia_semana == fecha.weekday()]
    if not horarios_dia:
        return {"fecha": fecha.isoformat(), "slots": [], "motivo": "Sin horarios ese día"}

    # Turnos del día (cualquier consultorio donde el médico trabaje ese día)
    ini_dia = datetime.combine(fecha, time.min)
    fin_dia = datetime.combine(fecha, time.max)
    turnos = db.query(models.Turno).filter(
        models.Turno.medico_id == medico_id,
        models.Turno.estado != models.EstadoTurno.cancelado,
        models.Turno.fecha_hora_inicio.between(ini_dia, fin_dia),
    ).all()

    slots = []
    for h in horarios_dia:
        hi_h, hi_m = map(int, h.hora_inicio.split(":"))
        hf_h, hf_m = map(int, h.hora_fin.split(":"))
        actual = datetime.combine(fecha, time(hi_h, hi_m))
        fin    = datetime.combine(fecha, time(hf_h, hf_m))
        delta  = timedelta(minutes=duracion)

        while actual + delta <= fin:
            ocupado = False
            for t in turnos:
                t_fin = t.fecha_hora_inicio + timedelta(minutes=t.duracion_minutos)
                if t.consultorio == h.consultorio and \
                   t.fecha_hora_inicio < actual + delta and t_fin > actual:
                    ocupado = True
                    break
            if not ocupado:
                slots.append({
                    "fecha_hora_inicio": actual.isoformat(),
                    "consultorio": h.consultorio,
                    "duracion": duracion,
                })
            actual += delta

    return {
        "fecha": fecha.isoformat(),
        "medico_id": medico_id,
        "duracion": duracion,
        "slots": slots,
    }


# ── Calendario iCal (.ics) ────────────────────────────────
@router.get("/medicos/{medico_id}/calendario.ics")
def calendario_ical(medico_id: int, db: Session = Depends(get_db)):
    """
    Feed iCal con los turnos del médico.
    El profesional pega esta URL en Google Calendar → "Otros calendarios" →
    "Desde URL" y los turnos se sincronizan automáticamente.
    """
    m = _get_medico(medico_id, db)

    # Últimos 30 días + todos los futuros
    desde = datetime.now() - timedelta(days=30)
    turnos = (
        db.query(models.Turno)
          .options(joinedload(models.Turno.paciente))
          .filter(
              models.Turno.medico_id == medico_id,
              models.Turno.fecha_hora_inicio >= desde,
          )
          .order_by(models.Turno.fecha_hora_inicio)
          .all()
    )

    cal_name = f"Turnos - Dr/a. {m.apellido}"
    esp = m.especialidad.nombre if m.especialidad else ""
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//MIO MEDIC//Turnos//ES",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_ical_escape(cal_name)}",
        "X-WR-TIMEZONE:America/Argentina/Buenos_Aires",
        # Timezone definition para clientes que lo necesiten
        "BEGIN:VTIMEZONE",
        "TZID:America/Argentina/Buenos_Aires",
        "BEGIN:STANDARD",
        "DTSTART:19700101T000000",
        "TZOFFSETFROM:-0300",
        "TZOFFSETTO:-0300",
        "END:STANDARD",
        "END:VTIMEZONE",
    ]

    for t in turnos:
        fin = t.fecha_hora_inicio + timedelta(minutes=t.duracion_minutos or 45)
        p = t.paciente
        nombre_pac = f"{p.apellido}, {p.nombre}" if p else "Sin paciente"
        status = _ESTADO_ICAL.get(t.estado, "TENTATIVE")

        # Armar description con datos útiles del paciente
        desc_parts = []
        if p:
            if p.financiador:
                fin_str = p.financiador
                if p.plan: fin_str += f" — {p.plan}"
                desc_parts.append(f"Financiador: {fin_str}")
            if p.nro_hc:      desc_parts.append(f"HC: {p.nro_hc}")
            if p.telefono:    desc_parts.append(f"Tel: {p.telefono}")
            if p.email:       desc_parts.append(f"Email: {p.email}")
        if t.observaciones:   desc_parts.append(f"Obs: {t.observaciones}")
        desc_parts.append(f"Estado: {t.estado.value}")
        description = "\\n".join(desc_parts)

        lines.extend([
            "BEGIN:VEVENT",
            f"UID:turno-{t.id}@miomedic",
            f"DTSTART;TZID=America/Argentina/Buenos_Aires:{_ical_dt(t.fecha_hora_inicio)}",
            f"DTEND;TZID=America/Argentina/Buenos_Aires:{_ical_dt(fin)}",
            f"SUMMARY:{_ical_escape(nombre_pac)}",
            f"DESCRIPTION:{_ical_escape(description)}",
            f"LOCATION:Consultorio {t.consultorio}",
            f"STATUS:{status}",
            "END:VEVENT",
        ])

    lines.append("END:VCALENDAR")
    ical_body = "\r\n".join(lines) + "\r\n"

    return Response(
        content=ical_body,
        media_type="text/calendar; charset=utf-8",
        headers={
            "Content-Disposition": f'inline; filename="turnos-{m.apellido.lower()}.ics"',
            "Cache-Control": "no-cache, max-age=0",
        },
    )


# ── Horarios ──────────────────────────────────────────────
@router.get("/medicos/{medico_id}/horarios", response_model=List[schemas.HorarioOut])
def listar_horarios(medico_id: int, db: Session = Depends(get_db)):
    return db.query(models.HorarioMedico).filter(
        models.HorarioMedico.medico_id == medico_id,
    ).order_by(models.HorarioMedico.dia_semana, models.HorarioMedico.hora_inicio).all()


@router.post("/medicos/{medico_id}/horarios", response_model=schemas.HorarioOut, status_code=201)
def agregar_horario(medico_id: int, data: schemas.HorarioCreate, db: Session = Depends(get_db)):
    if data.hora_fin <= data.hora_inicio:
        raise HTTPException(400, "La hora de fin debe ser posterior a la de inicio.")
    if data.dia_semana < 0 or data.dia_semana > 4:
        raise HTTPException(400, "Día inválido (0=Lun a 4=Vie).")
    if data.consultorio not in (1, 2):
        raise HTTPException(400, "Consultorio debe ser 1 o 2.")

    h = models.HorarioMedico(medico_id=medico_id, **data.model_dump())
    db.add(h); db.commit(); db.refresh(h)
    return h


@router.delete("/horarios/{horario_id}", status_code=204)
def eliminar_horario(horario_id: int, db: Session = Depends(get_db)):
    h = db.query(models.HorarioMedico).filter(models.HorarioMedico.id == horario_id).first()
    if not h:
        raise HTTPException(404, "Horario no encontrado")
    db.delete(h); db.commit()
