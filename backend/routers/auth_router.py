from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from database import get_db
from auth import hash_password, verify_password, create_access_token, get_current_user, require_admin
import models, schemas

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=schemas.TokenOut)
def login(data: schemas.LoginRequest, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.username == data.username).first()
    if not user or not verify_password(data.password, user.password_hash):
        raise HTTPException(401, "Usuario o contraseña incorrectos")
    token = create_access_token({"sub": user.id})
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
    u = models.User(
        username=data.username,
        password_hash=hash_password(data.password),
        display_name=data.display_name,
        role=data.role,
        medico_id=data.medico_id,
    )
    db.add(u); db.commit(); db.refresh(u)
    return u


@router.delete("/users/{user_id}", status_code=204)
def eliminar_usuario(user_id: int, db: Session = Depends(get_db), user: models.User = Depends(require_admin)):
    u = db.query(models.User).filter(models.User.id == user_id).first()
    if not u:
        raise HTTPException(404, "Usuario no encontrado")
    if u.id == user.id:
        raise HTTPException(400, "No podés eliminar tu propio usuario")
    db.delete(u); db.commit()
