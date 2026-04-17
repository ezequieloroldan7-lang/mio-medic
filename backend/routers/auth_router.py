import secrets

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from database import get_db
from auth import (
    MIN_PASSWORD_LEN,
    create_access_token,
    get_current_user,
    hash_password,
    require_admin,
    validate_password_strength,
    verify_password,
)
import models, schemas

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=schemas.TokenOut)
def login(data: schemas.LoginRequest, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.username == data.username).first()
    if not user or not verify_password(data.password, user.password_hash):
        raise HTTPException(401, "Usuario o contraseña incorrectos")
    token = create_access_token({"sub": str(user.id)})
    return {"access_token": token, "token_type": "bearer", "user": user}


@router.get("/me", response_model=schemas.UserOut)
def me(user: models.User = Depends(get_current_user)):
    return user


@router.get("/users", response_model=List[schemas.UserOut])
def listar_usuarios(db: Session = Depends(get_db), user: models.User = Depends(require_admin)):
    return db.query(models.User).order_by(models.User.display_name).all()


@router.post("/users", response_model=schemas.UserOut, status_code=201)
def crear_usuario(data: schemas.UserCreate, db: Session = Depends(get_db), user: models.User = Depends(require_admin)):
    if db.query(models.User).filter(models.User.username == data.username).first():
        raise HTTPException(400, "El usuario ya existe")
    validate_password_strength(data.password)
    u = models.User(
        username=data.username,
        password_hash=hash_password(data.password),
        display_name=data.display_name,
        role=data.role,
        medico_id=data.medico_id,
        must_change_password=True,
    )
    db.add(u); db.commit(); db.refresh(u)
    return u


@router.put("/change-password")
def cambiar_password(
    data: schemas.ChangePassword,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    if not verify_password(data.current_password, user.password_hash):
        raise HTTPException(400, "La contraseña actual es incorrecta")
    validate_password_strength(data.new_password)
    if verify_password(data.new_password, user.password_hash):
        raise HTTPException(400, "La nueva contraseña no puede ser igual a la actual")
    user.password_hash = hash_password(data.new_password)
    user.must_change_password = False
    db.commit()
    return {"detail": "Contraseña actualizada"}


@router.put("/users/{user_id}/reset-password")
def resetear_password(
    user_id: int,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_admin),
):
    u = db.query(models.User).filter(models.User.id == user_id).first()
    if not u:
        raise HTTPException(404, "Usuario no encontrado")
    # Generar contraseña temporal aleatoria (el usuario DEBE cambiarla al loguearse)
    new_pw = secrets.token_urlsafe(9)  # ~12 chars URL-safe
    u.password_hash = hash_password(new_pw)
    u.must_change_password = True
    db.commit()
    # La contraseña temporal se devuelve UNA SOLA VEZ al admin. No queda en logs.
    return {
        "detail": f"Contraseña temporal generada para '{u.username}'. El usuario deberá cambiarla al iniciar sesión.",
        "temporary_password": new_pw,
        "min_length": MIN_PASSWORD_LEN,
    }


@router.delete("/users/{user_id}", status_code=204)
def eliminar_usuario(user_id: int, db: Session = Depends(get_db), user: models.User = Depends(require_admin)):
    u = db.query(models.User).filter(models.User.id == user_id).first()
    if not u:
        raise HTTPException(404, "Usuario no encontrado")
    if u.id == user.id:
        raise HTTPException(400, "No podés eliminar tu propio usuario")
    db.delete(u); db.commit()
