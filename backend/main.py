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
from auth import generate_ical_token, get_current_user
from backup import run_backup
from database import SessionLocal, engine, get_db
from routers import medicos, pacientes, turnos
from routers.auth_router import router as auth_router
from security_headers import SecurityHeadersMiddleware
from whatsapp import enviar_confirmacion


# ── Logging ──────────────────────────────────────────────────
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
)
log = logging.getLogger("miomedic")


# ── Scheduler para WhatsApp ──────────────────────────────────
scheduler = AsyncIOScheduler()


def tarea_backup():
    """Corre una vez al día: backup del SQLite con rotación."""
    try:
        run_backup(engine)
    except Exception as e:  # noqa: BLE001
        log.exception("Error en tarea_backup: %s", e)


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


def _migrate_db():
    """Migraciones incrementales para SQLite."""
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    if "pacientes" in insp.get_table_names():
        cols = [c["name"] for c in insp.get_columns("pacientes")]
        with engine.begin() as conn:
            if "cobertura" in cols and "financiador" not in cols:
                conn.execute(text("ALTER TABLE pacientes RENAME COLUMN cobertura TO financiador"))
                log.info("Migración: cobertura → financiador")
            if "plan" not in cols:
                conn.execute(text("ALTER TABLE pacientes ADD COLUMN plan TEXT"))
                log.info("Migración: agregada columna plan")

    # Nuevas columnas de seguridad (v2.1): must_change_password y ical_token
    if "users" in insp.get_table_names():
        user_cols = [c["name"] for c in insp.get_columns("users")]
        if "must_change_password" not in user_cols:
            with engine.begin() as conn:
                conn.execute(text(
                    "ALTER TABLE users ADD COLUMN must_change_password BOOLEAN NOT NULL DEFAULT 0"
                ))
            log.info("Migración: agregada columna users.must_change_password")
        # v2.2: 2FA TOTP opcional por usuario
        if "totp_secret" not in user_cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE users ADD COLUMN totp_secret TEXT"))
            log.info("Migración: agregada columna users.totp_secret")
        if "totp_enabled" not in user_cols:
            with engine.begin() as conn:
                conn.execute(text(
                    "ALTER TABLE users ADD COLUMN totp_enabled BOOLEAN NOT NULL DEFAULT 0"
                ))
            log.info("Migración: agregada columna users.totp_enabled")
    if "medicos" in insp.get_table_names():
        med_cols = [c["name"] for c in insp.get_columns("medicos")]
        if "ical_token" not in med_cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE medicos ADD COLUMN ical_token TEXT"))
            log.info("Migración: agregada columna medicos.ical_token")

    # audit_log y refresh_tokens se crean automáticamente vía
    # Base.metadata.create_all cuando los modelos están definidos.

    # v2.2: re-cifrado opcional de filas legacy (telefono/email/totp_secret).
    # Activar con REENCRYPT_ON_START=1 — es O(n) por tabla y commitea.
    if os.getenv("REENCRYPT_ON_START", "0") == "1":
        from crypto import reencrypt_existing
        db = SessionLocal()
        try:
            stats = reencrypt_existing(db, models)
            log.info("Re-cifrado legacy completado: %s", stats)
        except Exception as e:  # noqa: BLE001
            db.rollback()
            log.error("Error en re-cifrado legacy: %s", e)
        finally:
            db.close()

    # Eliminar medicos de prueba (dejar solo Garrido)
    db = SessionLocal()
    try:
        apellidos_borrar = ["Pereyra", "Rodríguez", "Méndez", "Fernández"]
        for ap in apellidos_borrar:
            m = db.query(models.Medico).filter(models.Medico.apellido == ap).first()
            if m:
                db.query(models.User).filter(models.User.medico_id == m.id).delete()
                db.query(models.Turno).filter(models.Turno.medico_id == m.id).delete()
                db.delete(m)
                log.info("Migración: eliminado médico de prueba %s", ap)
        db.commit()
    except Exception as e:  # noqa: BLE001
        db.rollback()
        log.error("Error eliminando médicos de prueba: %s", e)
    finally:
        db.close()

    # Seed pacientes ficticios (una sola vez)
    db = SessionLocal()
    try:
        if db.query(models.Paciente).filter(models.Paciente.nro_hc == "100").first():
            pass
        else:
            pacientes_demo = [
                ("GARCIA", "LAURA", "30456789", "5491134567890", "laura.garcia@gmail.com", "OSDE", "310"),
                ("LOPEZ", "CARLOS", "28123456", "5491145678901", "carlos.lopez@hotmail.com", "SWISS MEDICAL", "SMG20"),
                ("RODRIGUEZ", "ANA", "35678901", "5491156789012", "ana.rodriguez@gmail.com", "GALENO", "ORO"),
                ("MARTINEZ", "DIEGO", "40234567", "5491167890123", None, "MEDIFE", "BRONCE"),
                ("SANCHEZ", "VALENTINA", "42345678", "5491178901234", "valentina.sanchez@gmail.com", "OSDE", "210"),
                ("DIAZ", "MARTIN", "33456789", "5491189012345", "martin.diaz@outlook.com", "PARTICULAR", None),
                ("TORRES", "SOFIA", "38567890", "5491190123456", None, "SWISS MEDICAL", "SMG40"),
                ("RAMIREZ", "NICOLAS", "29678901", "5491101234567", "nicolas.ramirez@gmail.com", "OMINT", "GLOBAL"),
                ("FLORES", "CAMILA", "41789012", "5491112345678", "camila.flores@yahoo.com", "OSDE", "450"),
                ("ACOSTA", "FACUNDO", "36890123", "5491123456789", None, "MEDICUS", "FAMILIAR"),
            ]
            for i, (ap, nom, dni, tel, email, fin, plan) in enumerate(pacientes_demo):
                db.add(models.Paciente(
                    apellido=ap, nombre=nom, dni=dni, telefono=tel,
                    email=email, nro_hc=str(100 + i),
                    financiador=fin, plan=plan,
                ))
            db.commit()
            log.info("Seed: 10 pacientes ficticios cargados (HC 100-109).")
    except Exception as e:  # noqa: BLE001
        db.rollback()
        log.error("Error en seed pacientes: %s", e)
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    models.Base.metadata.create_all(bind=engine)
    _migrate_db()
    _seed_datos_iniciales()
    _seed_admin_user()
    scheduler.add_job(tarea_whatsapp, "interval", hours=1, id="wa_reminders", replace_existing=True)
    # Backup diario del SQLite a las 03:00 (hora del servidor). No-op en Postgres.
    scheduler.add_job(tarea_backup, "cron", hour=3, minute=0, id="db_backup", replace_existing=True)
    scheduler.start()
    log.info("Scheduler iniciado. App lista.")
    yield
    scheduler.shutdown()


# ── App ──────────────────────────────────────────────────────
app = FastAPI(title="MIO MEDIC — Sistema de Turnos", version="2.0.0", lifespan=lifespan)

# CORS — lista blanca obligatoria desde env. Si no está seteada, solo se permite
# same-origin (la app + el backend sirven desde el mismo dominio en Render).
# Para habilitar clientes en otro origen, listar en CORS_ORIGINS separados por coma.
# "*" solo se acepta si está explícitamente pedido (dev / LAN interna).
_cors_origins = os.getenv("CORS_ORIGINS", "").strip()
if _cors_origins == "*":
    origins = ["*"]
    log.warning("CORS configurado como '*' — aceptable solo en dev/LAN interna.")
elif _cors_origins:
    origins = [o.strip() for o in _cors_origins.split(",") if o.strip()]
else:
    origins = []  # same-origin only (frontend servido por la misma app)
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

# Cabeceras de seguridad (CSP, HSTS, X-Frame-Options, etc).
app.add_middleware(SecurityHeadersMiddleware)

# ── Routers ──
# auth_router: /auth/login debe ser público (si no, nadie puede loguearse).
# Los endpoints de gestión de usuarios dentro de auth_router ya usan Depends(require_admin).
app.include_router(auth_router)

# public_router de medicos: feed iCal firmado con token en query param (Google
# Calendar no puede mandar Authorization header). Va SIN auth global.
app.include_router(medicos.public_router)

# El resto requiere autenticación. Dependencies a nivel de router aplica a TODOS
# los endpoints del router — cierra de golpe el agujero de CRUD público de pacientes,
# turnos y médicos (incluyendo export.xlsx, disponibilidad, resumen, etc.).
_auth_dep = [Depends(get_current_user)]
app.include_router(pacientes.router, dependencies=_auth_dep)
app.include_router(turnos.router,    dependencies=_auth_dep)
app.include_router(medicos.router,   dependencies=_auth_dep)

# Servir frontend estático
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


LOGIN_HTML = FRONTEND_DIR / "login.html"

@app.get("/")
def root():
    return FileResponse(str(INDEX_HTML))

@app.get("/login")
def login_page():
    return FileResponse(str(LOGIN_HTML))


@app.get("/health")
@app.get("/healthz")
def health():
    """
    Liveness probe. Render usa esto para detectar caídas (ver healthCheckPath en
    render.yaml). Devuelve 200 si la app responde; no toca la BD para no tirar
    el healthcheck por problemas transitorios del scheduler.
    """
    return {"status": "ok", "timestamp": datetime.now().isoformat(), "version": app.version}


# ── Resumen rápido para el dashboard ─────────────────────────
@app.get("/resumen", dependencies=[Depends(get_current_user)])
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
            "cancelados":  _count(ini_hoy, fin_hoy, models.Turno.estado == models.EstadoTurno.cancelado),
            "realizados":  _count(ini_hoy, fin_hoy, models.Turno.estado == models.EstadoTurno.realizado),
        },
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


def _seed_admin_user():
    """
    Crea los usuarios iniciales si no existen:
    - 'admin' (role=admin): para gestión de usuarios y auditoría.
    - 'mioturnos' (role=turnos): uso diario de la secretaría (agenda de turnos).
    Más un usuario por cada médico.

    Seguridad: la password inicial del admin sale de INITIAL_ADMIN_PASSWORD;
    para mioturnos y médicos, de INITIAL_USER_PASSWORD o se genera aleatoria.
    En todos los casos se fuerza must_change_password=True.

    Migración idempotente: si existe 'mioturnos' con role='admin' (legacy),
    se lo demota a 'turnos'. Es seguro re-correrlo: solo actúa si el rol
    todavía es 'admin'.
    """
    import secrets
    from auth import hash_password
    db = SessionLocal()
    try:
        # Usuario admin dedicado (gestión de usuarios + auditoría)
        if not db.query(models.User).filter(models.User.username == "admin").first():
            pw = os.getenv("INITIAL_ADMIN_PASSWORD") or secrets.token_urlsafe(9)
            admin = models.User(
                username="admin",
                password_hash=hash_password(pw),
                display_name="Administrador",
                role="admin",
                must_change_password=True,
            )
            db.add(admin)
            db.commit()
            log.warning(
                "Usuario 'admin' creado. Contraseña inicial: %s "
                "(cambiala en el primer login). Anotala: no se mostrará de nuevo.",
                pw,
            )

        # Usuario de secretaría / turnos
        mioturnos = db.query(models.User).filter(models.User.username == "mioturnos").first()
        if not mioturnos:
            pw = os.getenv("INITIAL_USER_PASSWORD") or secrets.token_urlsafe(9)
            db.add(models.User(
                username="mioturnos",
                password_hash=hash_password(pw),
                display_name="MIO TURNOS",
                role="turnos",
                must_change_password=True,
            ))
            db.commit()
            log.warning(
                "Usuario 'mioturnos' (rol turnos) creado. Contraseña inicial: %s "
                "(cambiala en el primer login).",
                pw,
            )
        elif mioturnos.role == "admin":
            # Deploys previos creaban mioturnos con role=admin. Ahora la
            # gestión/auditoría vive en el usuario 'admin' aparte.
            mioturnos.role = "turnos"
            db.commit()
            log.warning("Usuario 'mioturnos' demotado de 'admin' a 'turnos'.")

        # Un usuario por cada médico que no tenga usuario
        medicos_sin_user = db.query(models.Medico).filter(
            ~models.Medico.id.in_(
                db.query(models.User.medico_id).filter(models.User.medico_id.isnot(None))
            )
        ).all()
        for m in medicos_sin_user:
            import unicodedata
            def _clean(s):
                s = unicodedata.normalize("NFD", s.lower())
                return "".join(c for c in s if unicodedata.category(c) != "Mn").replace(" ", "")
            username = f"{_clean(m.nombre)[0]}.{_clean(m.apellido)}"
            if db.query(models.User).filter(models.User.username == username).first():
                continue
            pw = os.getenv("INITIAL_USER_PASSWORD") or secrets.token_urlsafe(9)
            u = models.User(
                username=username,
                password_hash=hash_password(pw),
                display_name=f"Dr/a. {m.nombre} {m.apellido}",
                role="medico",
                medico_id=m.id,
                must_change_password=True,
            )
            db.add(u)
            log.warning(
                "Usuario medico '%s' creado para %s %s. Contraseña inicial: %s "
                "(cambiala en el primer login).",
                username, m.nombre, m.apellido, pw,
            )
        db.commit()

        # Generar ical_token para médicos que no lo tengan (feed firmado)
        medicos_sin_token = db.query(models.Medico).filter(
            (models.Medico.ical_token.is_(None)) | (models.Medico.ical_token == "")
        ).all()
        for m in medicos_sin_token:
            m.ical_token = generate_ical_token()
        if medicos_sin_token:
            db.commit()
            log.info("Generados %d ical_token para médicos existentes.", len(medicos_sin_token))
    except Exception as e:  # noqa: BLE001
        db.rollback()
        log.error("Error creando usuarios: %s", e)
    finally:
        db.close()
