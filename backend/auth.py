"""
auth.py — Autenticación JWT para MIO MEDIC.
Roles: admin (secretaria) y medico (profesional).
"""
import hmac
import hashlib
import logging
import os
import secrets
import sys
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from database import get_db
import models

log = logging.getLogger("miomedic.auth")


def _get_or_create_secret_key() -> str:
    """
    Obtiene SECRET_KEY desde env. Si no está definido, genera uno persistente
    en el directorio de la BD para que los tokens sobrevivan reinicios del .exe.
    En Render, SECRET_KEY DEBE venir como env var (ver render.yaml, generateValue).
    """
    key = os.getenv("SECRET_KEY")
    if key and key.strip() and key != "miomedic-dev-secret-change-in-production":
        return key.strip()

    if getattr(sys, "frozen", False):
        key_dir = Path(sys.executable).resolve().parent
    else:
        key_dir = Path(__file__).resolve().parent
    key_file = key_dir / ".secret_key"

    if key_file.exists():
        existing = key_file.read_text(encoding="utf-8").strip()
        if existing:
            return existing

    new_key = secrets.token_urlsafe(48)
    try:
        key_file.write_text(new_key, encoding="utf-8")
        try:
            os.chmod(key_file, 0o600)
        except OSError:
            pass
        log.warning(
            "SECRET_KEY no definido en env; generado uno persistente en %s. "
            "En producción setear SECRET_KEY como variable de entorno.",
            key_file,
        )
    except OSError as e:
        log.error("No se pudo persistir SECRET_KEY (%s); usando solo en memoria.", e)
    return new_key


SECRET_KEY = _get_or_create_secret_key()
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = int(os.getenv("ACCESS_TOKEN_EXPIRE_HOURS", "4"))
MIN_PASSWORD_LEN = int(os.getenv("MIN_PASSWORD_LEN", "8"))

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> models.User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Token inválido o expirado",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        sub = payload.get("sub")
        if sub is None:
            raise credentials_exception
        user_id = int(sub)
    except (JWTError, ValueError, TypeError):
        raise credentials_exception

    user = db.query(models.User).filter(models.User.id == user_id).first()
    if user is None:
        raise credentials_exception
    return user


def require_admin(user: models.User = Depends(get_current_user)) -> models.User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Acceso denegado. Se requiere rol de administrador.")
    return user


# ── iCal signed token (para feed público que pegás en Google Calendar) ──
def generate_ical_token() -> str:
    """Token opaco de 32 bytes para feeds iCal."""
    return secrets.token_urlsafe(32)


def verify_ical_token(medico_id: int, provided: str, stored: str | None) -> bool:
    """Comparación en tiempo constante contra el token almacenado del médico."""
    if not provided or not stored:
        return False
    return hmac.compare_digest(provided, stored)


# ── Password policy ─────────────────────────────────────────
def validate_password_strength(pw: str) -> None:
    """Lanza HTTPException 400 si la password no cumple la política mínima."""
    if not pw or len(pw) < MIN_PASSWORD_LEN:
        raise HTTPException(
            400,
            f"La contraseña debe tener al menos {MIN_PASSWORD_LEN} caracteres.",
        )
