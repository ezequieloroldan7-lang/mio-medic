import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import List, Optional

from audit import audit, audit_standalone
from database import get_db
from auth import (
    MIN_PASSWORD_LEN,
    REFRESH_TOKEN_DAYS,
    create_access_token,
    generate_refresh_token,
    generate_totp_secret,
    get_current_user,
    hash_password,
    hash_refresh_token,
    require_admin,
    totp_provisioning_uri,
    validate_password_strength,
    verify_password,
    verify_totp,
)
from rate_limit import login_limiter
import models, schemas

router = APIRouter(prefix="/auth", tags=["auth"])


def _issue_refresh(db: Session, user: models.User, request: Optional[Request]) -> str:
    """Emite un refresh_token nuevo, guarda su hash y devuelve el token crudo al caller."""
    raw = generate_refresh_token()
    ua = request.headers.get("user-agent", "")[:200] if request else None
    ip = None
    if request:
        xff = request.headers.get("x-forwarded-for")
        ip = (xff.split(",")[0].strip() if xff else (request.client.host if request.client else None))
    rt = models.RefreshToken(
        user_id=user.id,
        token_hash=hash_refresh_token(raw),
        expires_at=datetime.utcnow() + timedelta(days=REFRESH_TOKEN_DAYS),
        user_agent=ua,
        ip=ip,
    )
    db.add(rt); db.flush()
    return raw


def _revoke_family(db: Session, token: models.RefreshToken, reason: str = "rotated") -> None:
    """Revoca el token y toda la cadena descendente (detección de reuso)."""
    current = token
    while current is not None:
        if current.revoked_at is None:
            current.revoked_at = datetime.utcnow()
        nxt_id = current.replaced_by
        current = db.query(models.RefreshToken).get(nxt_id) if nxt_id else None


class RefreshIn(BaseModel):
    refresh_token: str


class Disable2FA(BaseModel):
    password: str


class Activate2FA(BaseModel):
    code: str


@router.post("/login")
def login(data: schemas.LoginRequest, request: Request, db: Session = Depends(get_db)):
    """
    Login con soporte opcional de 2FA. Respuestas:
    - 200 + { access_token, refresh_token, user }  → login completo
    - 200 + { totp_required: true }                → falta el código TOTP,
      reintente con `totp_code` en el mismo body.
    - 401                                          → credenciales inválidas
    - 429                                          → rate limit
    """
    # Rate limit antes de tocar la BD o validar hashes (evita timing / DoS)
    login_limiter.check_or_raise(request, data.username)

    user = db.query(models.User).filter(models.User.username == data.username).first()
    if not user or not verify_password(data.password, user.password_hash):
        login_limiter.register_failure(request, data.username)
        audit_standalone(
            "login.fail",
            request=request,
            username=data.username,
            details={"user_exists": bool(user)},
        )
        raise HTTPException(401, "Usuario o contraseña incorrectos")

    # 2FA: si está habilitado y no vino código, avisar; si vino, validar
    if user.totp_enabled and user.totp_secret:
        code = (data.totp_code or "").strip()
        if not code:
            # No cuenta como intento fallido: password era correcta.
            return {"totp_required": True}
        if not verify_totp(user.totp_secret, code):
            login_limiter.register_failure(request, data.username)
            audit_standalone(
                "login.2fa.fail",
                request=request, user=user,
            )
            raise HTTPException(401, "Código de verificación incorrecto")

    login_limiter.register_success(request, data.username)
    access = create_access_token({"sub": str(user.id)})
    refresh_raw = _issue_refresh(db, user, request)
    audit(db, request, "login.ok", user=user, details={"2fa": user.totp_enabled})
    db.commit()
    return {
        "access_token": access,
        "refresh_token": refresh_raw,
        "token_type": "bearer",
        "user": schemas.UserOut.model_validate(user, from_attributes=True).model_dump(),
    }


@router.post("/refresh")
def refresh(data: RefreshIn, request: Request, db: Session = Depends(get_db)):
    """
    Rota el refresh token: valida el actual, lo marca como reemplazado, emite
    uno nuevo + un nuevo access_token. Si el token ya fue usado (revoked_at no
    None) → detección de reuso: revoca TODA la familia y devuelve 401.
    """
    if not data.refresh_token:
        raise HTTPException(401, "Refresh token requerido")
    h = hash_refresh_token(data.refresh_token)
    rt = db.query(models.RefreshToken).filter(models.RefreshToken.token_hash == h).first()
    if not rt:
        # Token desconocido: puede ser falsificado o ya borrado. No revelar.
        audit_standalone("token.refresh.unknown", request=request)
        raise HTTPException(401, "Refresh token inválido")

    if rt.revoked_at is not None:
        # REUSO detectado: alguien usó un token ya rotado. Revoco toda la cadena
        # (y cualquier otra activa del mismo usuario) — forzamos re-login.
        _revoke_family(db, rt, reason="reuse-detected")
        for other in db.query(models.RefreshToken).filter(
            models.RefreshToken.user_id == rt.user_id,
            models.RefreshToken.revoked_at.is_(None),
        ).all():
            other.revoked_at = datetime.utcnow()
        audit_standalone(
            "token.refresh.reuse", request=request,
            user=rt.user, details={"token_id": rt.id},
        )
        db.commit()
        raise HTTPException(401, "Refresh token reutilizado — sesión revocada")

    if rt.expires_at < datetime.utcnow():
        raise HTTPException(401, "Refresh token expirado")

    user = rt.user
    new_raw = _issue_refresh(db, user, request)
    # Marcar el viejo como rotado/reemplazado
    rt.revoked_at = datetime.utcnow()
    new_rt = db.query(models.RefreshToken).filter(
        models.RefreshToken.token_hash == hash_refresh_token(new_raw)
    ).first()
    if new_rt:
        rt.replaced_by = new_rt.id
    access = create_access_token({"sub": str(user.id)})
    audit(db, request, "token.refresh.ok", user=user)
    db.commit()
    return {
        "access_token": access,
        "refresh_token": new_raw,
        "token_type": "bearer",
    }


@router.post("/logout", status_code=204)
def logout(
    data: Optional[RefreshIn] = None,
    request: Request = None,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Revoca el refresh token actual (si se provee). Access_token vence solo."""
    if data and data.refresh_token:
        rt = db.query(models.RefreshToken).filter(
            models.RefreshToken.token_hash == hash_refresh_token(data.refresh_token),
            models.RefreshToken.user_id == user.id,
        ).first()
        if rt and rt.revoked_at is None:
            rt.revoked_at = datetime.utcnow()
    audit(db, request, "logout", user=user)
    db.commit()


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


@router.get("/2fa/status")
def estado_2fa(user: models.User = Depends(get_current_user)):
    """Devuelve si el usuario actual tiene 2FA activo."""
    return {"enabled": bool(user.totp_enabled)}


@router.post("/2fa/setup")
def iniciar_2fa(
    request: Request,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """
    Genera un secret nuevo (reemplaza el anterior si había uno pendiente) y
    devuelve la URI otpauth:// para armar el QR. El 2FA queda INACTIVO hasta
    que el usuario confirme un código válido en /2fa/activate.
    """
    if user.totp_enabled:
        raise HTTPException(400, "2FA ya está activo. Desactivarlo primero para regenerar.")
    secret = generate_totp_secret()
    user.totp_secret = secret
    user.totp_enabled = False
    uri = totp_provisioning_uri(secret, user.username)
    audit(db, request, "2fa.setup", user=user)
    db.commit()
    # Devolvemos el secret en plano SOLO en esta respuesta — el usuario lo
    # escanea en su app y nunca más se muestra.
    return {"secret": secret, "otpauth_uri": uri}


@router.post("/2fa/activate")
def activar_2fa(
    data: Activate2FA,
    request: Request,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Activa 2FA confirmando que el usuario puede generar códigos válidos."""
    if not user.totp_secret:
        raise HTTPException(400, "Primero llamá a /2fa/setup para generar un secret.")
    if user.totp_enabled:
        return {"detail": "2FA ya estaba activo"}
    if not verify_totp(user.totp_secret, data.code):
        audit_standalone("2fa.activate.fail", request=request, user=user)
        raise HTTPException(400, "Código inválido")
    user.totp_enabled = True
    audit(db, request, "2fa.activate.ok", user=user)
    db.commit()
    return {"detail": "2FA activado correctamente"}


@router.post("/2fa/disable")
def desactivar_2fa(
    data: Disable2FA,
    request: Request,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Desactiva 2FA. Requiere confirmar la contraseña actual."""
    if not verify_password(data.password, user.password_hash):
        audit_standalone(
            "2fa.disable.fail", request=request, user=user,
            details={"reason": "wrong_password"},
        )
        raise HTTPException(400, "Contraseña incorrecta")
    user.totp_enabled = False
    user.totp_secret = None
    audit(db, request, "2fa.disable.ok", user=user)
    db.commit()
    return {"detail": "2FA desactivado"}


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
