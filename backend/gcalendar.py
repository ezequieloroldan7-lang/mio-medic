"""
Google Calendar — sincronización automática de turnos.

Usa un Service Account de Google Cloud para crear/actualizar/eliminar eventos
en el Google Calendar de cada médico, sin necesidad de OAuth por usuario.

Setup:
    1. Google Cloud Console → habilitar Calendar API
    2. Crear Service Account → descargar JSON de credenciales
    3. Guardar el JSON en backend/google-credentials.json
    4. GOOGLE_CREDENTIALS_JSON=google-credentials.json  en .env
    5. Cada médico comparte su calendar con el email del service account
       (ej. miomedic@proyecto.iam.gserviceaccount.com) como "editor"
    6. Cargar el email del calendar del médico en su perfil de la app
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

log = logging.getLogger("miomedic.gcalendar")

# Lazy-load para evitar crash si las dependencias no están instaladas
_service = None
_initialized = False

CREDENTIALS_PATH = os.getenv("GOOGLE_CREDENTIALS_JSON", "google-credentials.json")
TIMEZONE = "America/Argentina/Buenos_Aires"


def _get_service():
    """Inicializa el servicio de Google Calendar (lazy, singleton)."""
    global _service, _initialized
    if _initialized:
        return _service
    _initialized = True

    try:
        from google.oauth2.service_account import Credentials
        from googleapiclient.discovery import build
    except ImportError:
        log.warning(
            "google-api-python-client / google-auth no instalados. "
            "Google Calendar deshabilitado. "
            "Instalar con: pip install google-api-python-client google-auth"
        )
        return None

    # Resolver ruta relativa al directorio del backend
    cred_path = Path(CREDENTIALS_PATH)
    if not cred_path.is_absolute():
        cred_path = Path(__file__).resolve().parent / cred_path

    if not cred_path.exists():
        log.warning("Google Calendar: archivo de credenciales no encontrado en %s", cred_path)
        return None

    try:
        creds = Credentials.from_service_account_file(
            str(cred_path),
            scopes=["https://www.googleapis.com/auth/calendar"],
        )
        _service = build("calendar", "v3", credentials=creds, cache_discovery=False)
        log.info("Google Calendar inicializado OK (service account: %s)", creds.service_account_email)
        return _service
    except Exception as e:  # noqa: BLE001
        log.error("Error al inicializar Google Calendar: %s", e)
        return None


def configurado() -> bool:
    return _get_service() is not None


# ── Helpers ──────────────────────────────────────────────────
def _dt_gcal(dt: datetime) -> dict:
    """Datetime → formato que espera la API de Google Calendar."""
    return {"dateTime": dt.isoformat(), "timeZone": TIMEZONE}


def _build_event_body(
    paciente_nombre: str,
    paciente_info: str,
    inicio: datetime,
    duracion_min: int,
    consultorio: int,
    observaciones: str = "",
    estado: str = "pendiente",
) -> dict:
    """Arma el body del evento para la API de Google Calendar."""
    fin = inicio + timedelta(minutes=duracion_min)

    description_parts = [paciente_info]
    if observaciones:
        description_parts.append(f"Obs: {observaciones}")
    description_parts.append(f"Estado: {estado}")

    color_map = {
        "pendiente": "5",    # banana (amarillo)
        "confirmado": "2",   # sage (verde)
        "cancelado": "4",    # flamingo (rojo claro)
        "ausente": "3",      # grape (violeta)
        "realizado": "9",    # blueberry (azul)
    }

    return {
        "summary": f"🏥 {paciente_nombre}",
        "description": "\n".join(description_parts),
        "location": f"MIO MEDIC — Consultorio {consultorio}",
        "start": _dt_gcal(inicio),
        "end": _dt_gcal(fin),
        "colorId": color_map.get(estado, "5"),
        "reminders": {"useDefault": False, "overrides": [{"method": "popup", "minutes": 15}]},
        "transparency": "opaque",
    }


def _paciente_info_str(paciente) -> str:
    """Arma una descripción útil del paciente para el evento."""
    parts = []
    if paciente:
        if paciente.cobertura:  parts.append(f"Cobertura: {paciente.cobertura}")
        if paciente.nro_hc:     parts.append(f"HC: {paciente.nro_hc}")
        if paciente.telefono:   parts.append(f"Tel: {paciente.telefono}")
        if paciente.email:      parts.append(f"Email: {paciente.email}")
        if paciente.dni:        parts.append(f"DNI: {paciente.dni}")
    return "\n".join(parts) if parts else ""


# ── API pública ──────────────────────────────────────────────
def crear_evento(calendar_id: str, turno, paciente, medico) -> Optional[str]:
    """
    Crea un evento en Google Calendar.
    Retorna el event_id (string) o None si falló.
    """
    svc = _get_service()
    if not svc or not calendar_id:
        return None

    nombre_pac = f"{paciente.apellido}, {paciente.nombre}" if paciente else "Sin paciente"
    info = _paciente_info_str(paciente)
    estado = turno.estado.value if turno.estado else "pendiente"

    body = _build_event_body(
        paciente_nombre=nombre_pac,
        paciente_info=info,
        inicio=turno.fecha_hora_inicio,
        duracion_min=turno.duracion_minutos or 45,
        consultorio=turno.consultorio,
        observaciones=turno.observaciones or "",
        estado=estado,
    )

    try:
        event = svc.events().insert(calendarId=calendar_id, body=body).execute()
        event_id = event.get("id")
        log.info("GCal evento creado: %s en calendar %s", event_id, calendar_id)
        return event_id
    except Exception as e:  # noqa: BLE001
        log.error("Error al crear evento en GCal (%s): %s", calendar_id, e)
        return None


def actualizar_evento(calendar_id: str, event_id: str, turno, paciente, medico) -> bool:
    """Actualiza un evento existente en Google Calendar."""
    svc = _get_service()
    if not svc or not calendar_id or not event_id:
        return False

    nombre_pac = f"{paciente.apellido}, {paciente.nombre}" if paciente else "Sin paciente"
    info = _paciente_info_str(paciente)
    estado = turno.estado.value if turno.estado else "pendiente"

    body = _build_event_body(
        paciente_nombre=nombre_pac,
        paciente_info=info,
        inicio=turno.fecha_hora_inicio,
        duracion_min=turno.duracion_minutos or 45,
        consultorio=turno.consultorio,
        observaciones=turno.observaciones or "",
        estado=estado,
    )

    # Si está cancelado o ausente, marcar como cancelado en GCal
    if estado in ("cancelado", "ausente"):
        body["summary"] = f"❌ {nombre_pac} (cancelado)"
        body["transparency"] = "transparent"

    try:
        svc.events().update(calendarId=calendar_id, eventId=event_id, body=body).execute()
        log.info("GCal evento actualizado: %s", event_id)
        return True
    except Exception as e:  # noqa: BLE001
        log.error("Error al actualizar evento en GCal (%s / %s): %s", calendar_id, event_id, e)
        return False


def cancelar_evento(calendar_id: str, event_id: str) -> bool:
    """Marca un evento como cancelado en Google Calendar."""
    svc = _get_service()
    if not svc or not calendar_id or not event_id:
        return False

    try:
        # Obtener el evento actual para preservar datos
        existing = svc.events().get(calendarId=calendar_id, eventId=event_id).execute()
        existing["summary"] = "❌ " + existing.get("summary", "Turno").replace("🏥 ", "")
        existing["colorId"] = "4"  # flamingo (rojo)
        existing["transparency"] = "transparent"
        desc = existing.get("description", "")
        if "Estado:" in desc:
            desc = desc.rsplit("Estado:", 1)[0] + "Estado: cancelado"
        else:
            desc += "\nEstado: cancelado"
        existing["description"] = desc
        svc.events().update(calendarId=calendar_id, eventId=event_id, body=existing).execute()
        log.info("GCal evento cancelado: %s", event_id)
        return True
    except Exception as e:  # noqa: BLE001
        log.error("Error al cancelar evento en GCal (%s / %s): %s", calendar_id, event_id, e)
        return False


def eliminar_evento(calendar_id: str, event_id: str) -> bool:
    """Elimina un evento de Google Calendar permanentemente."""
    svc = _get_service()
    if not svc or not calendar_id or not event_id:
        return False

    try:
        svc.events().delete(calendarId=calendar_id, eventId=event_id).execute()
        log.info("GCal evento eliminado: %s", event_id)
        return True
    except Exception as e:  # noqa: BLE001
        log.error("Error al eliminar evento en GCal (%s / %s): %s", calendar_id, event_id, e)
        return False
