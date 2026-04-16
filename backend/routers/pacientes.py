from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, Integer
from sqlalchemy.orm import Session
from typing import List, Optional
from database import get_db
import models, schemas

import re

router = APIRouter(prefix="/pacientes", tags=["pacientes"])


def _normalizar_telefono(tel):
    """Normaliza telefono argentino al formato 5491XXXXXXXXX para WhatsApp API."""
    if not tel:
        return None
    s = re.sub(r"[\s\-\(\)\.]", "", tel.strip())
    if not s:
        return None
    # Quitar + inicial
    if s.startswith("+"):
        s = s[1:]
    # Si empieza con 0 (ej: 01112345678 o 0351...)
    if s.startswith("0"):
        s = "54" + s[1:]
    # Si no empieza con 54, agregar 54
    if not s.startswith("54"):
        s = "54" + s
    # Insertar 9 después del 54 si falta (54 11 → 54 9 11)
    if s.startswith("54") and not s.startswith("549"):
        s = "549" + s[2:]
    # Quitar 15 del medio (5491115... → 549 11 sin 15)
    # Detectar: 549 + cod_area(2-4 dig) + 15 + numero(6-8 dig)
    m = re.match(r"^549(\d{2,4})15(\d{6,8})$", s)
    if m:
        s = "549" + m.group(1) + m.group(2)
    return s


@router.get("/next-hc")
def siguiente_hc(db: Session = Depends(get_db)):
    """Devuelve el siguiente numero de HC disponible."""
    max_hc = db.query(func.max(func.cast(models.Paciente.nro_hc, Integer))).scalar()
    return {"next_hc": str((max_hc or 0) + 1)}

@router.get("/", response_model=List[schemas.PacienteOut])
def listar_pacientes(
    q: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    query = db.query(models.Paciente)
    if q:
        like = f"%{q}%"
        query = query.filter(
            models.Paciente.nombre.ilike(like)   |
            models.Paciente.apellido.ilike(like) |
            models.Paciente.dni.ilike(like)      |
            models.Paciente.nro_hc.ilike(like)
        )
    return query.order_by(models.Paciente.apellido).all()

@router.get("/{paciente_id}", response_model=schemas.PacienteOut)
def obtener_paciente(paciente_id: int, db: Session = Depends(get_db)):
    p = db.query(models.Paciente).filter(models.Paciente.id == paciente_id).first()
    if not p: raise HTTPException(404, "Paciente no encontrado")
    return p

def _normalizar(d):
    """Normaliza campos del paciente: mayusculas, email, telefono."""
    for k in ("nombre", "apellido", "financiador", "plan", "deriva"):
        if d.get(k):
            d[k] = d[k].upper()
    if d.get("email"):
        d["email"] = d["email"].lower()
    d["telefono"] = _normalizar_telefono(d.get("telefono"))
    return d


@router.post("/", response_model=schemas.PacienteOut, status_code=201)
def crear_paciente(data: schemas.PacienteCreate, db: Session = Depends(get_db)):
    dump = _normalizar(data.model_dump())
    p = models.Paciente(**dump)
    db.add(p); db.commit(); db.refresh(p); return p

@router.put("/{paciente_id}", response_model=schemas.PacienteOut)
def actualizar_paciente(paciente_id: int, data: schemas.PacienteCreate, db: Session = Depends(get_db)):
    p = db.query(models.Paciente).filter(models.Paciente.id == paciente_id).first()
    if not p: raise HTTPException(404, "Paciente no encontrado")
    payload = _normalizar(data.model_dump())
    for k, v in payload.items(): setattr(p, k, v)
    db.commit(); db.refresh(p); return p

@router.delete("/{paciente_id}", status_code=204)
def eliminar_paciente(paciente_id: int, db: Session = Depends(get_db)):
    p = db.query(models.Paciente).filter(models.Paciente.id == paciente_id).first()
    if not p: raise HTTPException(404, "Paciente no encontrado")
    db.query(models.Turno).filter(models.Turno.paciente_id == paciente_id).delete()
    db.delete(p); db.commit()
