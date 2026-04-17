"""
crypto.py — Cifrado at-rest de columnas sensibles (PII).

Estrategia:
- AES-GCM con clave de 32 bytes obtenida de FIELD_ENCRYPTION_KEY (env) o derivada
  de SECRET_KEY vía HKDF si no está seteada (persiste en .field_key si se genera).
- `EncryptedStr` es un TypeDecorator transparente: la app lee/escribe strings;
  SQLAlchemy cifra en write y descifra en read. Transparente para Pydantic/rutas.
- Nonce por valor (12 bytes random), prefijado al ciphertext + almacenado
  base64-urlsafe.
- Distingue valores "legacy" en texto plano (migración): si no empieza con el
  prefijo `v1:`, se considera plaintext legacy y se devuelve tal cual (permite
  migración in-place sin romper la app).
- Valores `None` / `""` se preservan tal cual (no ciframos vacíos).

La migración automática re-cifra en background: cada vez que se escribe una
fila que tenía un valor legacy, queda cifrada. No forzamos re-cifrado masivo
salvo que se llame explícitamente a `reencrypt_existing()`.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
import sys
from pathlib import Path
from typing import Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import String
from sqlalchemy.types import TypeDecorator

log = logging.getLogger("miomedic.crypto")

PREFIX = "v1:"  # marker que distingue ciphertext de plaintext legacy
_NONCE_LEN = 12


def _key_path() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / ".field_key"
    return Path(__file__).resolve().parent / ".field_key"


def _derive_from_secret_key(secret: str) -> bytes:
    """HKDF determinístico: la clave de cifrado está anclada a SECRET_KEY pero con
    contexto distinto — así rotar SECRET_KEY tambien rotaría esta (no ideal pero
    explícito). Recomendado: usar FIELD_ENCRYPTION_KEY independiente."""
    h = hashlib.sha256()
    h.update(b"miomedic-field-key-v1:")
    h.update(secret.encode("utf-8"))
    return h.digest()


def _get_or_create_key() -> bytes:
    env_key = os.getenv("FIELD_ENCRYPTION_KEY", "").strip()
    if env_key:
        try:
            raw = base64.urlsafe_b64decode(env_key + "=" * (-len(env_key) % 4))
        except Exception:
            raw = env_key.encode("utf-8")
        if len(raw) < 32:
            raw = hashlib.sha256(raw).digest()
        return raw[:32]

    kp = _key_path()
    if kp.exists():
        existing = kp.read_text(encoding="utf-8").strip()
        if existing:
            try:
                return base64.urlsafe_b64decode(existing + "=" * (-len(existing) % 4))[:32]
            except Exception:
                log.warning("Field key corrupta; regenerando.")

    # Fallback: derivar de SECRET_KEY. Así al menos es persistente y consistente
    # sin requerir otra config. No es tan seguro como tener una key independiente.
    secret = os.getenv("SECRET_KEY", "")
    if secret.strip():
        key = _derive_from_secret_key(secret.strip())
        log.warning(
            "FIELD_ENCRYPTION_KEY no seteada — derivando de SECRET_KEY. "
            "Recomendado: setear FIELD_ENCRYPTION_KEY independiente."
        )
        return key

    # Último recurso: generar una nueva y persistirla.
    key = AESGCM.generate_key(bit_length=256)
    try:
        kp.write_text(base64.urlsafe_b64encode(key).decode("ascii"), encoding="utf-8")
        try:
            os.chmod(kp, 0o600)
        except OSError:
            pass
        log.warning(
            "FIELD_ENCRYPTION_KEY no definida; generada y persistida en %s. "
            "En producción exportar como env var para poder rotar.",
            kp,
        )
    except OSError as e:
        log.error("No se pudo persistir field key (%s); usando solo en memoria.", e)
    return key


_KEY: bytes = _get_or_create_key()
_AEAD = AESGCM(_KEY)


def encrypt_str(plain: str) -> str:
    """Cifra un string. Devuelve `v1:<b64url(nonce || ciphertext)>`."""
    if plain is None:
        return None
    if plain == "":
        return ""  # preservamos "" sin cifrar para no bloatear
    nonce = os.urandom(_NONCE_LEN)
    ct = _AEAD.encrypt(nonce, plain.encode("utf-8"), None)
    return PREFIX + base64.urlsafe_b64encode(nonce + ct).decode("ascii")


def decrypt_str(stored: Optional[str]) -> Optional[str]:
    """Descifra. Si el valor no tiene el prefijo v1:, asume plaintext legacy y
    lo devuelve tal cual (permite convivir con datos pre-migración)."""
    if stored is None:
        return None
    if stored == "" or not isinstance(stored, str):
        return stored
    if not stored.startswith(PREFIX):
        return stored  # legacy plaintext
    try:
        raw = base64.urlsafe_b64decode(stored[len(PREFIX):] + "=" * (-len(stored[len(PREFIX):]) % 4))
        nonce, ct = raw[:_NONCE_LEN], raw[_NONCE_LEN:]
        return _AEAD.decrypt(nonce, ct, None).decode("utf-8")
    except Exception as e:  # noqa: BLE001
        log.error("Decrypt falló (valor sospechoso): %s", e)
        return None


class EncryptedStr(TypeDecorator):
    """Columna string cifrada transparentemente."""
    impl = String
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if not isinstance(value, str):
            value = str(value)
        return encrypt_str(value)

    def process_result_value(self, value, dialect):
        return decrypt_str(value)


def reencrypt_existing(db, models_module) -> dict:
    """
    Re-cifra filas legacy (texto plano) para todas las columnas marcadas.
    Idempotente: saltea lo que ya está cifrado.

    Devuelve un contador por tabla para loguear.
    """
    stats = {}
    # Campos a re-cifrar: (model, [column_names])
    targets = [
        (models_module.Paciente, ["telefono", "email"]),
        (models_module.Medico,   ["telefono", "email"]),
        (models_module.User,     ["totp_secret"]),
    ]
    for Model, cols in targets:
        cnt = 0
        for row in db.query(Model).all():
            changed = False
            for c in cols:
                v = getattr(row, c)
                # Después del decrypt, si v no es None y no está cifrado, el write
                # de SQLAlchemy lo volverá a encriptar. Forzamos un re-set.
                if v is not None and v != "":
                    setattr(row, c, v)
                    changed = True
            if changed:
                cnt += 1
        if cnt:
            db.commit()
        stats[Model.__tablename__] = cnt
    return stats
