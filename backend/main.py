import logging
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

BASE_DIR     = Path(__file__).resolve().parent          # backend/

# PyInstaller: frontend queda dentro de _internal/frontend
if getattr(__import__('sys'), 'frozen', False):
    FRONTEND_DIR = (BASE_DIR / "frontend").resolve()
else:
    FRONTEND_DIR = (BASE_DIR.parent / "frontend").resolve()

INDEX_HTML   = FRONTEND_DIR / "index.html"

from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session, joinedload

import models
from database import SessionLocal, engine, get_db
from routers import medicos, pacientes, turnos
from whatsapp import enviar_confirmacion


# ── Logging ──────────────────────────────────────────────────
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
)
log = logging.getLogger("miomedic")


# ── Scheduler para WhatsApp ──────────────────────────────────
scheduler = AsyncIOScheduler()


def tarea_whatsapp():
    """Corre cada hora: busca turnos de mañana y envía recordatorio."""
    db = SessionLocal()
    try:
        manana_inicio = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        manana_fin    = manana_inicio + timedelta(days=1)
        turnos_pendientes = db.query(models.Turno).options(
            joinedload(models.Turno.paciente),
            joinedload(models.Turno.medico).joinedload(models.Medico.especialidad),
        ).filter(
            models.Turno.fecha_hora_inicio >= manana_inicio,
            models.Turno.fecha_hora_inicio <  manana_fin,
            models.Turno.estado != models.EstadoTurno.cancelado,
            models.Turno.whatsapp_enviado == False,  # noqa: E712
        ).all()

        for t in turnos_pendientes:
            p = t.paciente
            if not p or not p.telefono:
                continue
            nombre   = f"{p.nombre} {p.apellido}"
            medico   = f"Dr/a. {t.medico.nombre} {t.medico.apellido}"
            esp      = t.medico.especialidad.nombre if t.medico.especialidad else ""
            fecha_hr = t.fecha_hora_inicio.strftime("%d/%m/%Y a las %H:%M hs")
            if enviar_confirmacion(nombre, p.telefono, fecha_hr, medico, esp):
                t.whatsapp_enviado = True
        db.commit()
    except Exception as e:  # noqa: BLE001
        log.exception("Error en tarea_whatsapp: %s", e)
        db.rollback()
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    models.Base.metadata.create_all(bind=engine)
    _seed_datos_iniciales()
    scheduler.add_job(tarea_whatsapp, "interval", hours=1, id="wa_reminders", replace_existing=True)
    scheduler.start()
    log.info("Scheduler iniciado. App lista.")
    yield
    scheduler.shutdown()


# ── App ──────────────────────────────────────────────────────
app = FastAPI(title="MIO MEDIC — Sistema de Turnos", version="2.0.0", lifespan=lifespan)

# CORS — lista blanca desde env, con fallback a "*" (útil en dev / LAN interna)
_cors_origins = os.getenv("CORS_ORIGINS", "*")
origins = [o.strip() for o in _cors_origins.split(",")] if _cors_origins != "*" else ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(pacientes.router)
app.include_router(turnos.router)
app.include_router(medicos.router)

# Servir frontend estático
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.get("/")
def root():
    return FileResponse(str(INDEX_HTML))


@app.get("/health")
def health():
    return {"status": "ok", "timestamp": datetime.now().isoformat(), "version": app.version}


# ── Resumen rápido para el dashboard ─────────────────────────
@app.get("/resumen")
def resumen(db: Session = Depends(get_db)):
    """Resumen ligero para el header/dashboard (hoy, mañana, semana)."""
    hoy = date.today()
    ini_hoy    = datetime.combine(hoy, time.min)
    fin_hoy    = datetime.combine(hoy, time.max)
    ini_manana = ini_hoy + timedelta(days=1)
    fin_manana = fin_hoy + timedelta(days=1)
    ini_sem    = ini_hoy - timedelta(days=hoy.weekday())
    fin_sem    = ini_sem + timedelta(days=6, hours=23, minutes=59, seconds=59)

    def _count(desde, hasta, extra=None):
        q = db.query(models.Turno).filter(
            models.Turno.fecha_hora_inicio.between(desde, hasta),
        )
        if extra is not None:
            q = q.filter(extra)
        return q.count()

    activo = models.Turno.estado != models.EstadoTurno.cancelado

    return {
        "hoy": {
            "total":       _count(ini_hoy, fin_hoy),
            "pendientes":  _count(ini_hoy, fin_hoy, models.Turno.estado == models.EstadoTurno.pendiente),
            "confirmados": _count(ini_hoy, fin_hoy, models.Turno.estado == models.EstadoTurno.confirmado),
            "ausentes":    _count(ini_hoy, fin_hoy, models.Turno.estado == models.EstadoTurno.ausente),
            "realizados":  _count(ini_hoy, fin_hoy, models.Turno.estado == models.EstadoTurno.realizado),
        },
        "manana":   _count(ini_manana, fin_manana, activo),
        "semana":   _count(ini_sem,    fin_sem,    activo),
        "pacientes_total": db.query(models.Paciente).count(),
    }


# ── Seed datos iniciales ─────────────────────────────────────
def _seed_datos_iniciales():
    db = SessionLocal()
    try:
        if db.query(models.Especialidad).count() > 0:
            return  # ya existe

        especialidades = [
            "Cosmetología", "Nutrición", "Sexología",
            "Ginecología",  "Dermatología",
        ]
        esp_objs = {}
        for nombre in especialidades:
            e = models.Especialidad(nombre=nombre)
            db.add(e)
            db.flush()
            esp_objs[nombre] = e

        medicos_seed = [
            ("María de los Ángeles", "Garrido",   "Ginecología"),
            ("Carlos",               "Pereyra",   "Cosmetología"),
            ("Martín",               "Rodríguez", "Nutrición"),
            ("Sofía",                "Méndez",    "Sexología"),
            ("Laura",                "Fernández", "Dermatología"),
        ]
        for nombre, apellido, esp in medicos_seed:
            db.add(models.Medico(
                nombre=nombre,
                apellido=apellido,
                especialidad_id=esp_objs[esp].id,
            ))

        db.commit()
        log.info("Seed inicial cargado (especialidades + médicos).")
    except Exception as e:  # noqa: BLE001
        db.rollback()
        log.error("Error en seed inicial: %s", e)
    finally:
        db.close()
