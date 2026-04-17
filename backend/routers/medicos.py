import logging
from datetime import date, datetime, time, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session, joinedload

import models
import schemas
from audit import audit
from auth import generate_ical_token, get_current_user, require_staff, verify_ical_token
from database import get_db

log = logging.getLogger("miomedic.medicos")

# Router principal: protegido con auth global en main.py
router = APIRouter(tags=["medicos"])

# Router público para feeds firmados (iCal). El token firmado reemplaza al Bearer
# porque Google Calendar no puede mandar headers personalizados.
public_router = APIRouter(tags=["medicos-public"])


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
def crear_especialidad(
    data: schemas.EspecialidadCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_staff),
):
    """Crea una especialidad nueva. Si ya existe (case-insensitive), devuelve
    la existente. Solo admin y secretaría (role='turnos'); médicos reciben 403.
    """
    nombre = data.nombre.strip()
    if not nombre:
        raise HTTPException(400, "El nombre no puede estar vacío.")
    existente = db.query(models.Especialidad).filter(
        models.Especialidad.nombre.ilike(nombre)
    ).first()
    if existente:
        return existente
    e = models.Especialidad(nombre=nombre)
    db.add(e); db.flush()
    audit(db, request, "especialidad.create", user=user,
          entity_type="especialidad", entity_id=e.id,
          details={"nombre": e.nombre})
    db.commit(); db.refresh(e)
    return e


@router.delete("/especialidades/{esp_id}", status_code=204)
def eliminar_especialidad(
    esp_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_staff),
):
    """Elimina una especialidad. Falla si hay profesionales con esa especialidad."""
    esp = db.query(models.Especialidad).filter(models.Especialidad.id == esp_id).first()
    if not esp:
        raise HTTPException(404, "Especialidad no encontrada")
    en_uso = db.query(models.Medico.id).filter(
        models.Medico.especialidad_id == esp_id,
    ).first()
    if en_uso:
        raise HTTPException(
            400,
            "No se puede eliminar: hay profesionales con esta especialidad. Reasignalos primero.",
        )
    audit(db, request, "especialidad.delete", user=user,
          entity_type="especialidad", entity_id=esp.id,
          details={"nombre": esp.nombre})
    db.delete(esp); db.commit()


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
def crear_medico(
    data: schemas.MedicoCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    m = models.Medico(**data.model_dump())
    m.ical_token = generate_ical_token()
    db.add(m); db.flush()
    audit(db, request, "medico.create", user=user,
          entity_type="medico", entity_id=m.id,
          details={"nombre": m.nombre, "apellido": m.apellido, "matricula": m.matricula})
    db.commit(); db.refresh(m)
    # Auto-crear usuario para el profesional con password temporal
    import secrets, unicodedata
    from auth import hash_password
    def _c(s):
        s = unicodedata.normalize("NFD", s.lower())
        return "".join(c for c in s if unicodedata.category(c) != "Mn").replace(" ", "")
    username = f"{_c(m.nombre)[0]}.{_c(m.apellido)}"
    if not db.query(models.User).filter(models.User.username == username).first():
        temp_pw = secrets.token_urlsafe(9)
        u = models.User(
            username=username,
            password_hash=hash_password(temp_pw),
            display_name=f"{m.nombre} {m.apellido}",
            role="medico",
            medico_id=m.id,
            must_change_password=True,
        )
        db.add(u)
        db.commit()
        log.warning(
            "Usuario '%s' auto-creado para %s %s. Contraseña temporal: %s (anotala, se muestra una sola vez).",
            username, m.nombre, m.apellido, temp_pw,
        )
    return _get_medico(m.id, db)


@router.put("/medicos/{medico_id}", response_model=schemas.MedicoOut)
def actualizar_medico(
    medico_id: int,
    data: schemas.MedicoCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    m = db.query(models.Medico).filter(models.Medico.id == medico_id).first()
    if not m:
        raise HTTPException(404, "Médico no encontrado")
    payload = data.model_dump()
    changed_fields = [k for k in payload if getattr(m, k) != payload[k]]
    for k, v in payload.items():
        setattr(m, k, v)
    if changed_fields:
        audit(db, request, "medico.update", user=user,
              entity_type="medico", entity_id=m.id,
              details={"fields": changed_fields})
    db.commit()
    return _get_medico(medico_id, db)


@router.delete("/medicos/{medico_id}", status_code=204)
def eliminar_medico(
    medico_id: int,
    request: Request,
    force: bool = False,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    m = db.query(models.Medico).filter(models.Medico.id == medico_id).first()
    if not m:
        raise HTTPException(404, "Médico no encontrado")

    if not force:
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

    audit(db, request, "medico.delete", user=user,
          entity_type="medico", entity_id=m.id,
          details={"nombre": m.nombre, "apellido": m.apellido, "force": bool(force)})
    # Eliminar turnos, usuario y horarios asociados
    db.query(models.Turno).filter(models.Turno.medico_id == medico_id).delete()
    db.query(models.User).filter(models.User.medico_id == medico_id).delete()
    db.delete(m); db.commit()
    log.info("Médico id=%s eliminado con sus datos asociados", medico_id)


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

    # Bloqueos del profesional que intersecten el día (aplican a cualquier consultorio)
    bloqueos = db.query(models.BloqueoMedico).filter(
        models.BloqueoMedico.medico_id == medico_id,
        models.BloqueoMedico.fecha_inicio < fin_dia,
        models.BloqueoMedico.fecha_fin    > ini_dia,
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
                for b in bloqueos:
                    if b.fecha_inicio < actual + delta and b.fecha_fin > actual:
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
@router.get("/medicos/{medico_id}/calendario-url")
def calendario_url(
    medico_id: int,
    request: Request,
    regenerate: bool = Query(False),
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """
    Devuelve la URL firmada (token) del feed iCal del médico. Si aún no tiene
    token o se pide `regenerate=true`, se genera uno nuevo (invalidando el anterior).
    Requiere autenticación — solo el admin / el propio médico deberían usarlo.
    """
    m = db.query(models.Medico).filter(models.Medico.id == medico_id).first()
    if not m:
        raise HTTPException(404, "Médico no encontrado")
    if regenerate or not m.ical_token:
        m.ical_token = generate_ical_token()
        audit(db, request, "medico.ical_token.rotate", user=user,
              entity_type="medico", entity_id=m.id,
              details={"reason": "regenerate" if regenerate else "initial"})
        db.commit()
    return {
        "medico_id": m.id,
        "ical_token": m.ical_token,
        "path": f"/feed/medicos/{m.id}/calendario.ics?token={m.ical_token}",
    }


@public_router.get("/feed/medicos/{medico_id}/calendario.ics")
def calendario_ical(
    medico_id: int,
    token: str = Query(..., description="Token firmado del médico (ver /medicos/{id}/calendario-url)"),
    db: Session = Depends(get_db),
):
    """
    Feed iCal público firmado con los turnos del médico.
    El profesional pega esta URL en Google Calendar → "Otros calendarios" →
    "Desde URL" y los turnos se sincronizan automáticamente.
    El `token` reemplaza al Bearer porque Google Calendar no manda headers.
    """
    m = db.query(models.Medico).options(
        joinedload(models.Medico.especialidad),
    ).filter(models.Medico.id == medico_id).first()
    if not m or not verify_ical_token(medico_id, token, m.ical_token):
        # 404 en vez de 403 para no filtrar existencia
        raise HTTPException(404, "Feed no encontrado o token inválido")

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

    cal_name = f"Turnos - {m.apellido}"
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


# ── Bloqueos (profesional no disponible) ──────────────────
@router.get("/medicos/{medico_id}/bloqueos", response_model=List[schemas.BloqueoOut])
def listar_bloqueos(
    medico_id: int,
    desde: Optional[date] = Query(None),
    hasta: Optional[date] = Query(None),
    db: Session = Depends(get_db),
):
    q = db.query(models.BloqueoMedico).filter(models.BloqueoMedico.medico_id == medico_id)
    if desde:
        q = q.filter(models.BloqueoMedico.fecha_fin > datetime.combine(desde, time.min))
    if hasta:
        q = q.filter(models.BloqueoMedico.fecha_inicio < datetime.combine(hasta, time.max))
    return q.order_by(models.BloqueoMedico.fecha_inicio).all()


@router.get("/bloqueos", response_model=List[schemas.BloqueoOut])
def listar_bloqueos_fecha(
    fecha: date = Query(..., description="Fecha a consultar"),
    db: Session = Depends(get_db),
):
    """Bloqueos de todos los profesionales que intersecten `fecha`. Usado por la agenda diaria."""
    ini = datetime.combine(fecha, time.min)
    fin = datetime.combine(fecha, time.max)
    return db.query(models.BloqueoMedico).filter(
        models.BloqueoMedico.fecha_inicio < fin,
        models.BloqueoMedico.fecha_fin    > ini,
    ).order_by(models.BloqueoMedico.fecha_inicio).all()


@router.post("/medicos/{medico_id}/bloqueos", response_model=schemas.BloqueoOut, status_code=201)
def crear_bloqueo(
    medico_id: int,
    data: schemas.BloqueoCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    m = db.query(models.Medico).filter(models.Medico.id == medico_id).first()
    if not m:
        raise HTTPException(404, "Médico no encontrado")
    if data.fecha_fin <= data.fecha_inicio:
        raise HTTPException(400, "La fecha/hora de fin debe ser posterior a la de inicio.")
    b = models.BloqueoMedico(
        medico_id=medico_id,
        fecha_inicio=data.fecha_inicio,
        fecha_fin=data.fecha_fin,
        motivo=(data.motivo or None),
        creado_por=user.id,
    )
    db.add(b); db.flush()
    audit(db, request, "bloqueo.create", user=user,
          entity_type="bloqueo", entity_id=b.id,
          details={"medico_id": medico_id,
                   "desde": data.fecha_inicio.isoformat(),
                   "hasta": data.fecha_fin.isoformat(),
                   "motivo": b.motivo})
    db.commit(); db.refresh(b)
    return b


@router.delete("/bloqueos/{bloqueo_id}", status_code=204)
def eliminar_bloqueo(
    bloqueo_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    b = db.query(models.BloqueoMedico).filter(models.BloqueoMedico.id == bloqueo_id).first()
    if not b:
        raise HTTPException(404, "Bloqueo no encontrado")
    audit(db, request, "bloqueo.delete", user=user,
          entity_type="bloqueo", entity_id=b.id,
          details={"medico_id": b.medico_id,
                   "desde": b.fecha_inicio.isoformat(),
                   "hasta": b.fecha_fin.isoformat(),
                   "motivo": b.motivo})
    db.delete(b); db.commit()
