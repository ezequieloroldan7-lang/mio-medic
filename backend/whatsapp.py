"""
WhatsApp Cloud API (Meta Business Platform).

Reemplaza a Twilio. Usa la API directa de Meta:
    POST https://graph.facebook.com/v21.0/{PHONE_NUMBER_ID}/messages

Variables de entorno necesarias:
    WHATSAPP_ACCESS_TOKEN    → token permanente (System User Access Token)
    WHATSAPP_PHONE_NUMBER_ID → ID del número de WhatsApp Business
    WHATSAPP_TEMPLATE_NAME   → nombre de la plantilla aprobada (ej. "recordatorio_turno")
    WHATSAPP_TEMPLATE_LANG   → código de idioma (default: "es_AR")
    WHATSAPP_API_VERSION     → versión de la Graph API (default: "v21.0")

IMPORTANTE — ventana de 24 horas:
    Meta permite mensajes "libres" (texto) solo dentro de las 24 hs desde el último
    mensaje del usuario. Los recordatorios son mensajes iniciados por el negocio,
    así que DEBEN usar una plantilla (template) previamente aprobada.

    La plantilla típica para un recordatorio podría ser así:

        Hola {{1}} 👋
        Te recordamos que mañana tenés turno en MIO MEDIC:
        📅 {{2}}
        👩‍⚕️ {{3}}
        🏥 {{4}}

        Respondé SI para confirmar o avisanos si necesitás cancelar.

    Donde {{1}}..{{4}} son variables que esta función inyecta en orden:
        1 = nombre del paciente
        2 = fecha y hora del turno
        3 = nombre del profesional
        4 = especialidad
"""
from __future__ import annotations

import logging
import os
import re

import httpx

log = logging.getLogger("miomedic.whatsapp")

WA_TOKEN         = os.getenv("WHATSAPP_ACCESS_TOKEN", "")
WA_PHONE_ID      = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
WA_TEMPLATE_NAME       = os.getenv("WHATSAPP_TEMPLATE_NAME", "recordatorio_turno")
WA_TEMPLATE_AGENDADO   = os.getenv("WHATSAPP_TEMPLATE_AGENDADO", "turno_agendado")
WA_TEMPLATE_LANG       = os.getenv("WHATSAPP_TEMPLATE_LANG", "es_AR")
WA_API_VERSION         = os.getenv("WHATSAPP_API_VERSION", "v21.0")

_GRAPH_URL = f"https://graph.facebook.com/{WA_API_VERSION}/{{pid}}/messages"


def formatear_telefono(tel: str) -> str:
    """
    Normaliza teléfonos argentinos al formato E.164 para WhatsApp.
    Meta quiere el número SIN el "+" al inicio, solo dígitos.
    Para celulares argentinos: 549 + área + número.
    """
    if not tel:
        return ""
    tel = re.sub(r"[\s\-()+]", "", str(tel).strip())
    if not tel:
        return ""

    # Ya viene con 549 al inicio → respetar tal cual
    if tel.startswith("549") and len(tel) >= 12:
        return tel

    # Viene con 54 (línea fija o sin el 9) → quitar para normalizar
    if tel.startswith("54"):
        tel = tel[2:]

    # Quitar 0 del área (código nacional)
    if tel.startswith("0"):
        tel = tel[1:]

    # Quitar 15 del celular local (ej. 11 15 1234 5678 → 11 1234 5678)
    if len(tel) == 12 and tel[2:4] == "15":
        tel = tel[:2] + tel[4:]

    # Celulares argentinos: 10 u 11 dígitos → anteponer 549
    if 10 <= len(tel) <= 11:
        return "549" + tel

    # Fallback conservador
    return "54" + tel


def _configurado() -> bool:
    if not WA_TOKEN or not WA_PHONE_ID:
        log.warning("WhatsApp Cloud API no configurada "
                    "(faltan WHATSAPP_ACCESS_TOKEN / WHATSAPP_PHONE_NUMBER_ID).")
        return False
    return True


def _post(payload: dict) -> bool:
    url = _GRAPH_URL.format(pid=WA_PHONE_ID)
    headers = {
        "Authorization": f"Bearer {WA_TOKEN}",
        "Content-Type":  "application/json",
    }
    try:
        with httpx.Client(timeout=15) as client:
            r = client.post(url, json=payload, headers=headers)
        if r.status_code >= 300:
            log.error("WhatsApp API %s: %s", r.status_code, r.text[:500])
            return False
        data = r.json()
        mid = (data.get("messages") or [{}])[0].get("id")
        log.info("WhatsApp enviado OK (message_id=%s)", mid)
        return True
    except httpx.HTTPError as e:
        log.error("Error HTTP al enviar WhatsApp: %s", e)
        return False
    except Exception as e:  # noqa: BLE001
        log.exception("Error inesperado al enviar WhatsApp: %s", e)
        return False


def enviar_template(to_num: str, template: str, lang: str, variables: list[str]) -> bool:
    """Envía un mensaje de plantilla (apto para mensajes iniciados por el negocio)."""
    payload = {
        "messaging_product": "whatsapp",
        "to": to_num,
        "type": "template",
        "template": {
            "name": template,
            "language": {"code": lang},
            "components": [
                {
                    "type": "body",
                    "parameters": [{"type": "text", "text": v} for v in variables],
                }
            ] if variables else [],
        },
    }
    return _post(payload)


def enviar_texto(to_num: str, texto: str) -> bool:
    """
    Envía un mensaje de texto libre.
    ⚠️ SOLO funciona si el usuario escribió en las últimas 24 hs
    (ventana de atención al cliente). Para recordatorios usar enviar_template.
    """
    payload = {
        "messaging_product": "whatsapp",
        "to": to_num,
        "type": "text",
        "text": {"preview_url": False, "body": texto},
    }
    return _post(payload)


# ── API pública ──────────────────────────────────────────────

def enviar_confirmacion(nombre: str, telefono: str, fecha_hora: str,
                        medico: str, especialidad: str) -> bool:
    """
    Recordatorio 24 hs antes del turno (scheduler automático).
    Usa la plantilla WHATSAPP_TEMPLATE_NAME ("recordatorio_turno").
    """
    if not _configurado():
        return False

    destino = formatear_telefono(telefono)
    if not destino:
        log.warning("Teléfono inválido/vacío para %s.", nombre)
        return False

    variables = [nombre, fecha_hora, medico, especialidad]
    ok = enviar_template(destino, WA_TEMPLATE_NAME, WA_TEMPLATE_LANG, variables)
    if ok:
        log.info("Recordatorio enviado a %s (%s)", nombre, destino)
    return ok


def enviar_turno_agendado(nombre: str, telefono: str, fecha_hora: str,
                          medico: str, especialidad: str,
                          consultorio: int, duracion: int) -> bool:
    """
    Aviso instantáneo al paciente cuando se agenda un turno nuevo.
    Usa la plantilla WHATSAPP_TEMPLATE_AGENDADO ("turno_agendado").

    Variables de plantilla (6):
        {{1}} = nombre del paciente
        {{2}} = fecha y hora
        {{3}} = profesional
        {{4}} = especialidad
        {{5}} = consultorio
        {{6}} = duración en minutos
    """
    if not _configurado():
        return False

    destino = formatear_telefono(telefono)
    if not destino:
        log.warning("Teléfono inválido/vacío para %s.", nombre)
        return False

    variables = [
        nombre,
        fecha_hora,
        medico,
        especialidad,
        str(consultorio),
        f"{duracion} minutos",
    ]
    ok = enviar_template(destino, WA_TEMPLATE_AGENDADO, WA_TEMPLATE_LANG, variables)
    if ok:
        log.info("Aviso de turno agendado enviado a %s (%s)", nombre, destino)
    return ok
