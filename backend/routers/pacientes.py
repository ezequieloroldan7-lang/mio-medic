from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional
from database import get_db
import models, schemas

router = APIRouter(prefix="/pacientes", tags=["pacientes"])

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

@router.post("/", response_model=schemas.PacienteOut, status_code=201)
def crear_paciente(data: schemas.PacienteCreate, db: Session = Depends(get_db)):
    p = models.Paciente(**data.model_dump())
    db.add(p); db.commit(); db.refresh(p); return p

@router.put("/{paciente_id}", response_model=schemas.PacienteOut)
def actualizar_paciente(paciente_id: int, data: schemas.PacienteCreate, db: Session = Depends(get_db)):
    p = db.query(models.Paciente).filter(models.Paciente.id == paciente_id).first()
    if not p: raise HTTPException(404, "Paciente no encontrado")
    for k, v in data.model_dump().items(): setattr(p, k, v)
    db.commit(); db.refresh(p); return p

@router.delete("/{paciente_id}", status_code=204)
def eliminar_paciente(paciente_id: int, db: Session = Depends(get_db)):
    p = db.query(models.Paciente).filter(models.Paciente.id == paciente_id).first()
    if not p: raise HTTPException(404, "Paciente no encontrado")
    db.delete(p); db.commit()
