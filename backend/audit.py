"""
audit.py — Helper para registrar eventos sensibles en la tabla audit_log.

Uso típico:
    from audit import audit
    audit(db, request, "paciente.create", entity_type="paciente",
          entity_id=p.id, user=current_user)

El caller es responsable del commit (la función hace add pero no commit), así
los eventos quedan en la misma transacción que la acción. Si el caller no
hace commit explícito, el evento también queda sin persistir — eso es lo
correcto (si la acción falló, no queremos log falso).

Para eventos que ocurren en endpoints sin sesión (p. ej. login fallido),
usar `audit_standalone()` que abre su propia sesión y commitea solo.
"""
import json
import logging
from typing import Any, Optional

from fastapi import Request
from sqlalchemy.orm import Session

import models
from database import SessionLocal

log = logging.getLogger("miomedic.audit")


def _client_ip(request: Optional[Request]) -> Optional[str]:
    if request is None:
        return None
    # Render/Proxies ponen la IP real en X-Forwarded-For. Tomamos el primer hop.
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    if request.client:
        return request.client.host
    return None


def _make_entry(
    action: str,
    request: Optional[Request] = None,
    user: Optional[models.User] = None,
    entity_type: Optional[str] = None,
    entity_id: Optional[int] = None,
    details: Optional[dict] = None,
    username: Optional[str] = None,
) -> models.AuditLog:
    details_json = None
    if details:
        try:
            details_json = json.dumps(details, ensure_ascii=False, default=str)[:2000]
        except (TypeError, ValueError):
            details_json = str(details)[:2000]
    return models.AuditLog(
        action=action,
        user_id=user.id if user is not None else None,
        username=(user.username if user is not None else username),
        entity_type=entity_type,
        entity_id=entity_id,
        details=details_json,
        ip=_client_ip(request),
    )


def audit(
    db: Session,
    request: Optional[Request],
    action: str,
    *,
    user: Optional[models.User] = None,
    entity_type: Optional[str] = None,
    entity_id: Optional[int] = None,
    details: Optional[dict] = None,
    username: Optional[str] = None,
) -> None:
    """Agrega un evento al audit log dentro de la sesión del caller (no commitea)."""
    try:
        db.add(_make_entry(action, request, user, entity_type, entity_id, details, username))
    except Exception as e:  # noqa: BLE001
        # Audit nunca debe romper el flujo principal
        log.error("No se pudo registrar audit event '%s': %s", action, e)


def audit_standalone(
    action: str,
    *,
    request: Optional[Request] = None,
    user: Optional[models.User] = None,
    entity_type: Optional[str] = None,
    entity_id: Optional[int] = None,
    details: Optional[dict] = None,
    username: Optional[str] = None,
) -> None:
    """Registra un evento con su propia sesión + commit. Útil para login fail, etc."""
    db = SessionLocal()
    try:
        db.add(_make_entry(action, request, user, entity_type, entity_id, details, username))
        db.commit()
    except Exception as e:  # noqa: BLE001
        log.error("No se pudo registrar audit event standalone '%s': %s", action, e)
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
    finally:
        db.close()


def _diff_dict(before: dict, after: dict, keys: list[str]) -> dict:
    """Construye un dict con solo los campos que cambiaron (before → after)."""
    out = {}
    for k in keys:
        b = before.get(k)
        a = after.get(k)
        if b != a:
            out[k] = {"before": b, "after": a}
    return out
