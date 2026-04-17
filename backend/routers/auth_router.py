import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from typing import List

from audit import audit, audit_standalone
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
from rate_limit import login_limiter
import models, schemas

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=schemas.TokenOut)
def login(data: schemas.LoginRequest, request: Request, db: Session = Depends(get_db)):
    # Rate limit antes de tocar la BD o validar hashes (evita timing / DoS)
    login_limiter.check_or_raise(request, data.username)

    user = db.query(models.User).filter(models.User.username == data.username).first()
    if not user or not verify_password(data.password, user.password_hash):
        login_limiter.register_failure(request, data.username)
        # Audit fuera de la sesión (no queremos que un error en BD anule el log)
        audit_standalone(
            "login.fail",
            request=request,
            username=data.username,
            details={"user_exists": bool(user)},
        )
        raise HTTPException(401, "Usuario o contraseña incorrectos")

    login_limiter.register_success(request, data.username)
    token = create_access_token({"sub": str(user.id)})
    audit(db, request, "login.ok", user=user)
    db.commit()
    return {"access_token": token, "token_type": "bearer", "user": user}


@router.get("/me", response_model=schemas.UserOut)
def me(user: models.User = Depends(get_current_user)):
    return user


@router.get("/users", response_model=List[schemas.UserOut])
def listar_usuarios(db: Session = Depends(get_db), user: models.User = Depends(require_admin)):
    return db.query(models.User).order_by(models.User.display_name).all()


@router.post("/users", response_model=schemas.UserOut, status_code=201)
def crear_usuario(
    data: schemas.UserCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_admin),
):
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
    db.add(u); db.flush()
    audit(
        db, request, "user.create", user=user,
        entity_type="user", entity_id=u.id,
        details={"username": u.username, "role": u.role, "medico_id": u.medico_id},
    )
    db.commit(); db.refresh(u)
    return u


@router.put("/change-password")
def cambiar_password(
    data: schemas.ChangePassword,
    request: Request,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    if not verify_password(data.current_password, user.password_hash):
        audit_standalone(
            "password.change.fail", request=request, user=user,
            details={"reason": "current_password_wrong"},
        )
        raise HTTPException(400, "La contraseña actual es incorrecta")
    validate_password_strength(data.new_password)
    if verify_password(data.new_password, user.password_hash):
        raise HTTPException(400, "La nueva contraseña no puede ser igual a la actual")
    user.password_hash = hash_password(data.new_password)
    user.must_change_password = False
    audit(db, request, "password.change.ok", user=user)
    db.commit()
    return {"detail": "Contraseña actualizada"}


@router.put("/users/{user_id}/reset-password")
def resetear_password(
    user_id: int,
    request: Request,
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
    audit(
        db, request, "password.reset", user=user,
        entity_type="user", entity_id=u.id,
        details={"target_username": u.username},
    )
    db.commit()
    # La contraseña temporal se devuelve UNA SOLA VEZ al admin. No queda en logs.
    return {
        "detail": f"Contraseña temporal generada para '{u.username}'. El usuario deberá cambiarla al iniciar sesión.",
        "temporary_password": new_pw,
        "min_length": MIN_PASSWORD_LEN,
    }


@router.get("/audit")
def listar_audit(
    limit: int = 200,
    action: str | None = None,
    username: str | None = None,
    entity_type: str | None = None,
    entity_id: int | None = None,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_admin),
):
    """
    Lista los eventos del audit log (solo admin). Devuelve los más recientes
    primero. Filtrable por action, username, entity. Límite máximo: 1000.
    """
    limit = max(1, min(limit, 1000))
    q = db.query(models.AuditLog)
    if action:
        q = q.filter(models.AuditLog.action == action)
    if username:
        q = q.filter(models.AuditLog.username == username)
    if entity_type:
        q = q.filter(models.AuditLog.entity_type == entity_type)
    if entity_id is not None:
        q = q.filter(models.AuditLog.entity_id == entity_id)
    rows = q.order_by(models.AuditLog.id.desc()).limit(limit).all()
    return [
        {
            "id": r.id,
            "timestamp": r.timestamp.isoformat() if r.timestamp else None,
            "user_id": r.user_id,
            "username": r.username,
            "action": r.action,
            "entity_type": r.entity_type,
            "entity_id": r.entity_id,
            "details": r.details,
            "ip": r.ip,
        }
        for r in rows
    ]


@router.delete("/users/{user_id}", status_code=204)
def eliminar_usuario(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_admin),
):
    u = db.query(models.User).filter(models.User.id == user_id).first()
    if not u:
        raise HTTPException(404, "Usuario no encontrado")
    if u.id == user.id:
        raise HTTPException(400, "No podés eliminar tu propio usuario")
    audit(
        db, request, "user.delete", user=user,
        entity_type="user", entity_id=u.id,
        details={"username": u.username, "role": u.role},
    )
    db.delete(u); db.commit()
