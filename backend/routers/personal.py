"""Router para gestionar el Personal administrativo (role='administrativo')."""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

import models
from auth import hash_password, require_admin
from database import get_db

router = APIRouter(prefix="/personal", tags=["personal"])


class PersonalBase(BaseModel):
    nombre:   str
    apellido: str
    telefono: Optional[str] = None
    email:    Optional[str] = None


class PersonalCreate(PersonalBase):
    username: str
    password: str


class PersonalUpdate(PersonalBase):
    username: Optional[str] = None


class PersonalOut(BaseModel):
    id: int
    username: str
    nombre: str
    apellido: str
    display_name: str
    telefono: Optional[str] = None
    email:    Optional[str] = None

    class Config:
        from_attributes = True


def _split_display_name(dn: str):
    parts = (dn or "").strip().split(None, 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return (parts[0] if parts else ""), ""


def _to_out(u: models.User) -> PersonalOut:
    nombre, apellido = _split_display_name(u.display_name)
    return PersonalOut(
        id=u.id,
        username=u.username,
        nombre=nombre,
        apellido=apellido,
        display_name=u.display_name,
        telefono=u.telefono,
        email=u.email,
    )


@router.get("", response_model=List[PersonalOut])
def listar_personal(
    db: Session = Depends(get_db),
    _: models.User = Depends(require_admin),
):
    users = (
        db.query(models.User)
          .filter(models.User.role == "administrativo")
          .order_by(models.User.display_name)
          .all()
    )
    return [_to_out(u) for u in users]


@router.post("", response_model=PersonalOut, status_code=201)
def crear_personal(
    data: PersonalCreate,
    db: Session = Depends(get_db),
    _: models.User = Depends(require_admin),
):
    username = data.username.strip().lower()
    if not username:
        raise HTTPException(400, "El usuario no puede estar vacío")
    if db.query(models.User).filter(models.User.username == username).first():
        raise HTTPException(400, "El usuario ya existe")
    if not data.password or len(data.password) < 4:
        raise HTTPException(400, "La contraseña debe tener al menos 4 caracteres")

    display_name = f"{data.nombre.strip()} {data.apellido.strip()}".strip()
    if not display_name:
        raise HTTPException(400, "Nombre y apellido son obligatorios")

    u = models.User(
        username=username,
        password_hash=hash_password(data.password),
        display_name=display_name,
        role="administrativo",
        telefono=(data.telefono or "").strip() or None,
        email=(data.email or "").strip() or None,
    )
    db.add(u); db.commit(); db.refresh(u)
    return _to_out(u)


@router.put("/{user_id}", response_model=PersonalOut)
def actualizar_personal(
    user_id: int,
    data: PersonalUpdate,
    db: Session = Depends(get_db),
    _: models.User = Depends(require_admin),
):
    u = db.query(models.User).filter(
        models.User.id == user_id,
        models.User.role == "administrativo",
    ).first()
    if not u:
        raise HTTPException(404, "Personal no encontrado")

    if data.username and data.username.strip().lower() != u.username:
        new_username = data.username.strip().lower()
        if db.query(models.User).filter(models.User.username == new_username).first():
            raise HTTPException(400, "El usuario ya existe")
        u.username = new_username

    display_name = f"{data.nombre.strip()} {data.apellido.strip()}".strip()
    if not display_name:
        raise HTTPException(400, "Nombre y apellido son obligatorios")
    u.display_name = display_name
    u.telefono = (data.telefono or "").strip() or None
    u.email    = (data.email    or "").strip() or None
    db.commit(); db.refresh(u)
    return _to_out(u)


@router.delete("/{user_id}", status_code=204)
def eliminar_personal(
    user_id: int,
    db: Session = Depends(get_db),
    admin: models.User = Depends(require_admin),
):
    u = db.query(models.User).filter(
        models.User.id == user_id,
        models.User.role == "administrativo",
    ).first()
    if not u:
        raise HTTPException(404, "Personal no encontrado")
    if u.id == admin.id:
        raise HTTPException(400, "No podés eliminar tu propio usuario")
    db.delete(u); db.commit()


@router.put("/{user_id}/reset-password")
def resetear_password(
    user_id: int,
    db: Session = Depends(get_db),
    _: models.User = Depends(require_admin),
):
    u = db.query(models.User).filter(
        models.User.id == user_id,
        models.User.role == "administrativo",
    ).first()
    if not u:
        raise HTTPException(404, "Personal no encontrado")
    new_pw = "mio2026"
    u.password_hash = hash_password(new_pw)
    db.commit()
    return {"detail": f"Contraseña de '{u.username}' reseteada a '{new_pw}'"}
