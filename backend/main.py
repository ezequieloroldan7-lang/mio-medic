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


# ── Modo demo ────────────────────────────────────────────────
# Activado por env var DEMO_MODE. Hace tres cosas:
# 1) Siembra datos ficticios extra (médicos, horarios, turnos) al primer arranque.
# 2) Fija las credenciales de admin/mioturnos/médicos a DEMO_PASSWORD (default "demo123")
#    y saltea el flujo de must_change_password.
# 3) Expone /demo-info público con las credenciales para la pantalla de login.
# No alterar en producción.
DEMO_MODE = os.getenv("DEMO_MODE", "").strip().lower() in ("1", "true", "yes", "on")
DEMO_PASSWORD = os.getenv("DEMO_PASSWORD", "demo123")


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
            medico   = f"{t.medico.nombre} {t.medico.apellido}"
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

    # Limpieza one-time: quitar prefijos "Dr/a.", "Dra.", "Dr." persistidos en
    # users.display_name. Se introdujeron antes de cambiar la política a
    # "solo nombre completo". Idempotente: solo toca filas que aún tienen el prefijo.
    if "users" in insp.get_table_names():
        try:
            with engine.begin() as conn:
                for prefijo in ("Dr/a. ", "Dr/a ", "Dra. ", "Dra ", "Dr. ", "Dr "):
                    conn.execute(
                        text(
                            "UPDATE users SET display_name = SUBSTR(display_name, :n) "
                            "WHERE display_name LIKE :pat"
                        ),
                        {"n": len(prefijo) + 1, "pat": f"{prefijo}%"},
                    )
            log.info("Migración: prefijos Dr/a./Dra./Dr. eliminados de users.display_name (si existían).")
        except Exception as e:  # noqa: BLE001
            log.error("Error limpiando prefijos en users.display_name: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    models.Base.metadata.create_all(bind=engine)
    _migrate_db()
    _seed_datos_iniciales()
    if DEMO_MODE:
        # Médicos + horarios deben existir antes de _seed_admin_user (que crea
        # un user por médico) y antes de _seed_demo_turnos.
        _seed_demo_medicos_horarios()
    _seed_admin_user()
    if DEMO_MODE:
        _seed_demo_pacientes_extra()
        _seed_demo_turnos()
    # En modo demo no tiene sentido mandar recordatorios reales ni persistir
    # backups del SQLite efímero — salteamos el scheduler entero.
    if not DEMO_MODE:
        scheduler.add_job(tarea_whatsapp, "interval", hours=1, id="wa_reminders", replace_existing=True)
        # Backup diario del SQLite a las 03:00 (hora del servidor). No-op en Postgres.
        scheduler.add_job(tarea_backup, "cron", hour=3, minute=0, id="db_backup", replace_existing=True)
        scheduler.start()
        log.info("Scheduler iniciado. App lista.")
    else:
        log.info("Modo demo activo: scheduler de WhatsApp y backup deshabilitados.")
    yield
    if scheduler.running:
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
MANIFEST_FILE = FRONTEND_DIR / "manifest.webmanifest"
SW_FILE       = FRONTEND_DIR / "service-worker.js"

@app.get("/")
def root():
    return FileResponse(str(INDEX_HTML))

@app.get("/login")
def login_page():
    return FileResponse(str(LOGIN_HTML))

# PWA: manifest y service-worker se sirven desde el root para que el scope
# del SW cubra toda la app (un SW en /static/ solo controlaría /static/*).
@app.get("/manifest.webmanifest")
def pwa_manifest():
    return FileResponse(
        str(MANIFEST_FILE),
        media_type="application/manifest+json",
    )

@app.get("/service-worker.js")
def pwa_service_worker():
    # no-cache para que al deployar se tome el SW nuevo al primer fetch.
    return FileResponse(
        str(SW_FILE),
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"},
    )


@app.get("/health")
@app.get("/healthz")
def health():
    """
    Liveness probe. Render usa esto para detectar caídas (ver healthCheckPath en
    render.yaml). Devuelve 200 si la app responde; no toca la BD para no tirar
    el healthcheck por problemas transitorios del scheduler.
    """
    return {"status": "ok", "timestamp": datetime.now().isoformat(), "version": app.version}


@app.get("/demo-info")
def demo_info():
    """
    Endpoint público para que el frontend detecte si está corriendo en modo demo
    y muestre el banner de credenciales. En producción siempre devuelve
    {demo: false} — no filtra nada sensible.
    """
    if not DEMO_MODE:
        return {"demo": False}
    return {
        "demo": True,
        "mensaje": "Modo demo — datos ficticios, WhatsApp y backups deshabilitados.",
        "credenciales": [
            {"rol": "Administrador", "usuario": "admin",     "password": DEMO_PASSWORD},
            {"rol": "Secretaría",    "usuario": "mioturnos", "password": DEMO_PASSWORD},
            {"rol": "Médico",        "usuario": "m.garrido", "password": DEMO_PASSWORD},
        ],
    }


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
        # En modo demo forzamos DEMO_PASSWORD en todos los roles y anulamos
        # must_change_password — así el visitante entra directo con las credenciales
        # que se muestran en la pantalla de login.
        must_change = False if DEMO_MODE else True

        # Usuario admin dedicado (gestión de usuarios + auditoría)
        existing_admin = db.query(models.User).filter(models.User.username == "admin").first()
        if not existing_admin:
            pw = DEMO_PASSWORD if DEMO_MODE else (os.getenv("INITIAL_ADMIN_PASSWORD") or secrets.token_urlsafe(9))
            admin = models.User(
                username="admin",
                password_hash=hash_password(pw),
                display_name="Administrador",
                role="admin",
                must_change_password=must_change,
            )
            db.add(admin)
            db.commit()
            if DEMO_MODE:
                log.info("Demo: usuario 'admin' creado con DEMO_PASSWORD.")
            else:
                log.warning(
                    "Usuario 'admin' creado. Contraseña inicial: %s "
                    "(cambiala en el primer login). Anotala: no se mostrará de nuevo.",
                    pw,
                )
        elif DEMO_MODE:
            # Idempotencia en demo: si el user ya existe (p.ej. tras un redeploy),
            # reescribimos el hash con DEMO_PASSWORD para que la UI siga siendo
            # consistente con lo que muestra el banner.
            existing_admin.password_hash = hash_password(DEMO_PASSWORD)
            existing_admin.must_change_password = False
            db.commit()

        # Usuario de secretaría / turnos
        mioturnos = db.query(models.User).filter(models.User.username == "mioturnos").first()
        if not mioturnos:
            pw = DEMO_PASSWORD if DEMO_MODE else (os.getenv("INITIAL_USER_PASSWORD") or secrets.token_urlsafe(9))
            db.add(models.User(
                username="mioturnos",
                password_hash=hash_password(pw),
                display_name="MIO TURNOS",
                role="turnos",
                must_change_password=must_change,
            ))
            db.commit()
            if DEMO_MODE:
                log.info("Demo: usuario 'mioturnos' creado con DEMO_PASSWORD.")
            else:
                log.warning(
                    "Usuario 'mioturnos' (rol turnos) creado. Contraseña inicial: %s "
                    "(cambiala en el primer login).",
                    pw,
                )
        else:
            if mioturnos.role == "admin":
                # Deploys previos creaban mioturnos con role=admin. Ahora la
                # gestión/auditoría vive en el usuario 'admin' aparte.
                mioturnos.role = "turnos"
                db.commit()
                log.warning("Usuario 'mioturnos' demotado de 'admin' a 'turnos'.")
            if DEMO_MODE:
                mioturnos.password_hash = hash_password(DEMO_PASSWORD)
                mioturnos.must_change_password = False
                db.commit()

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
            pw = DEMO_PASSWORD if DEMO_MODE else (os.getenv("INITIAL_USER_PASSWORD") or secrets.token_urlsafe(9))
            u = models.User(
                username=username,
                password_hash=hash_password(pw),
                display_name=f"{m.nombre} {m.apellido}",
                role="medico",
                medico_id=m.id,
                must_change_password=must_change,
            )
            db.add(u)
            if DEMO_MODE:
                log.info("Demo: usuario medico '%s' creado con DEMO_PASSWORD.", username)
            else:
                log.warning(
                    "Usuario medico '%s' creado para %s %s. Contraseña inicial: %s "
                    "(cambiala en el primer login).",
                    username, m.nombre, m.apellido, pw,
                )
        db.commit()

        # En demo, también resetear la clave de los users de médicos ya existentes
        # para que no quede ningún residuo con contraseña random de deploys previos.
        if DEMO_MODE:
            for u in db.query(models.User).filter(models.User.role == "medico").all():
                u.password_hash = hash_password(DEMO_PASSWORD)
                u.must_change_password = False
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


# ── Seeds exclusivos de modo demo ────────────────────────────
# Solo se ejecutan si DEMO_MODE está activo. Son idempotentes: cada uno checkea
# si ya hizo su trabajo y retorna si sí. Pensados para que el visitante de la
# demo vea una app "viva" apenas entra (agenda con turnos, buscador con
# muchos pacientes, estados variados).

_DEMO_MEDICOS_EXTRA = [
    # (nombre, apellido, especialidad, consultorio)
    ("Juan Martín",  "Pérez",   "Dermatología",  2),
    ("Carla",        "Ruiz",    "Nutrición",     3),
    ("Ignacio",      "Vázquez", "Cosmetología",  1),
]


def _seed_demo_medicos_horarios():
    """
    Agrega los médicos adicionales de la demo (si aún no existen por apellido)
    y carga horarios Lun-Vie 09:00-18:00 para todos los médicos que no tengan.
    """
    db = SessionLocal()
    try:
        # Cargar especialidades por nombre
        esps = {e.nombre: e for e in db.query(models.Especialidad).all()}

        # Médicos adicionales
        nuevos = 0
        for nombre, apellido, esp_nombre, _consul in _DEMO_MEDICOS_EXTRA:
            if db.query(models.Medico).filter(models.Medico.apellido == apellido).first():
                continue
            esp = esps.get(esp_nombre)
            if not esp:
                continue
            db.add(models.Medico(
                nombre=nombre, apellido=apellido, especialidad_id=esp.id,
            ))
            nuevos += 1
        if nuevos:
            db.commit()
            log.info("Seed demo: %d médicos adicionales cargados.", nuevos)

        # Horarios Lun-Vie 09:00-18:00 — uno por médico sin horarios.
        # Asignamos consultorio según el seed extra o 1 por default.
        consul_por_apellido = {ap: c for _, ap, _, c in _DEMO_MEDICOS_EXTRA}
        medicos_sin_horarios = db.query(models.Medico).filter(
            ~models.Medico.id.in_(
                db.query(models.HorarioMedico.medico_id)
                .filter(models.HorarioMedico.medico_id.isnot(None))
            )
        ).all()
        total_horarios = 0
        for m in medicos_sin_horarios:
            consul = consul_por_apellido.get(m.apellido, 1)
            for dia in range(5):  # 0=Lun … 4=Vie
                db.add(models.HorarioMedico(
                    medico_id=m.id, dia_semana=dia,
                    hora_inicio="09:00", hora_fin="18:00",
                    consultorio=consul,
                ))
                total_horarios += 1
        if total_horarios:
            db.commit()
            log.info("Seed demo: %d franjas de horario cargadas (Lun-Vie 09-18).", total_horarios)
    except Exception as e:  # noqa: BLE001
        db.rollback()
        log.error("Error en seed demo médicos/horarios: %s", e)
    finally:
        db.close()


_DEMO_PACIENTES_EXTRA = [
    # (apellido, nombre, dni, tel, email, financiador, plan)
    ("GONZALEZ",  "MARIA",      "27345678", "5491134000001", "maria.gonzalez@gmail.com",  "OSDE",          "510"),
    ("PEREYRA",   "LUCIA",      "31456789", "5491134000002", None,                         "SWISS MEDICAL", "SMG30"),
    ("BENITEZ",   "MATIAS",     "34567890", "5491134000003", "matias.benitez@hotmail.com", "GALENO",        "AZUL"),
    ("ROMERO",    "AGUSTINA",   "38678901", "5491134000004", "agus.romero@gmail.com",      "OSDE",          "210"),
    ("SOSA",      "JOAQUIN",    "29789012", "5491134000005", None,                         "MEDIFE",        "PLATA"),
    ("ALVAREZ",   "PAULA",      "32890123", "5491134000006", "paula.alvarez@yahoo.com",    "PARTICULAR",    None),
    ("MOLINA",    "SANTIAGO",   "40901234", "5491134000007", "santi.molina@gmail.com",     "OMINT",         "FAMILIAR"),
    ("GIMENEZ",   "FLORENCIA",  "33012345", "5491134000008", None,                         "SWISS MEDICAL", "SMG20"),
    ("CASTRO",    "JULIAN",     "28123789", "5491134000009", "julian.castro@outlook.com",  "OSDE",          "310"),
    ("HERRERA",   "ANTONELLA",  "37234567", "5491134000010", "anto.herrera@gmail.com",     "MEDICUS",       "ORO"),
    ("AGUIRRE",   "FEDERICO",   "30345001", "5491134000011", None,                         "PARTICULAR",    None),
    ("SILVA",     "GABRIELA",   "35456002", "5491134000012", "gabriela.silva@gmail.com",   "GALENO",        "VERDE"),
    ("QUIROGA",   "TOMAS",      "39567003", "5491134000013", None,                         "OSDE",          "210"),
    ("MEDINA",    "CAROLINA",   "31678004", "5491134000014", "caro.medina@hotmail.com",    "MEDIFE",        "BRONCE"),
    ("VARGAS",    "ESTEBAN",    "26789005", "5491134000015", "esteban.vargas@gmail.com",   "OMINT",         "GLOBAL"),
]


def _seed_demo_pacientes_extra():
    """Agrega 15 pacientes ficticios (HC 110-124) además de los 10 base."""
    db = SessionLocal()
    try:
        # Si ya existe HC 110 → asumimos cargado.
        if db.query(models.Paciente).filter(models.Paciente.nro_hc == "110").first():
            return
        for i, (ap, nom, dni, tel, email, fin, plan) in enumerate(_DEMO_PACIENTES_EXTRA):
            if db.query(models.Paciente).filter(models.Paciente.dni == dni).first():
                continue
            db.add(models.Paciente(
                apellido=ap, nombre=nom, dni=dni, telefono=tel,
                email=email, nro_hc=str(110 + i),
                financiador=fin, plan=plan,
            ))
        db.commit()
        log.info("Seed demo: 15 pacientes adicionales cargados (HC 110-124).")
    except Exception as e:  # noqa: BLE001
        db.rollback()
        log.error("Error en seed demo pacientes: %s", e)
    finally:
        db.close()


def _seed_demo_turnos():
    """
    Carga ~30 turnos repartidos entre semana pasada, hoy, mañana y próxima semana,
    en distintos estados, para que la agenda y el dashboard se vean poblados.
    Solo corre si no hay turnos en la BD (idempotente).
    """
    db = SessionLocal()
    try:
        if db.query(models.Turno).count() > 0:
            return

        medicos = db.query(models.Medico).all()
        pacientes = db.query(models.Paciente).all()
        if not medicos or not pacientes:
            log.warning("Seed demo turnos: faltan médicos o pacientes, salteo.")
            return

        # Helper para consultorio por médico (usa el del primer horario, o 1).
        def _consul_de(m):
            if m.horarios:
                return m.horarios[0].consultorio or 1
            return 1

        hoy = date.today()
        # Monday de esta semana
        lunes_esta = hoy - timedelta(days=hoy.weekday())
        lunes_prox = lunes_esta + timedelta(days=7)
        lunes_pasado = lunes_esta - timedelta(days=7)

        def _at(d, hh, mm=0):
            return datetime.combine(d, time(hh, mm))

        E = models.EstadoTurno
        # Repartimos: (día, hora, minuto, idx_medico, idx_paciente, estado, wa_enviado)
        plan_turnos = [
            # HOY — 6 turnos mixtos
            (hoy,                 9, 30, 0, 0,  E.pendiente,  False),
            (hoy,                10, 15, 1, 1,  E.confirmado, True),
            (hoy,                11,  0, 2, 2,  E.realizado,  True),
            (hoy,                12,  0, 3 % max(len(medicos),1), 3, E.ausente, True),
            (hoy,                15,  0, 0, 4,  E.pendiente,  False),
            (hoy,                16, 30, 1, 5,  E.confirmado, True),
            # MAÑANA — 5 turnos
            (hoy + timedelta(1),  9,  0, 2, 6,  E.confirmado, True),
            (hoy + timedelta(1), 10,  0, 3 % max(len(medicos),1), 7, E.pendiente, False),
            (hoy + timedelta(1), 11, 30, 0, 8,  E.confirmado, True),
            (hoy + timedelta(1), 14, 30, 1, 9,  E.pendiente,  False),
            (hoy + timedelta(1), 17,  0, 2, 10, E.pendiente,  False),
            # Próxima semana (Lun-Jue) — 8 turnos
            (lunes_prox + timedelta(0),  9, 30, 0, 11, E.pendiente,  False),
            (lunes_prox + timedelta(0), 15,  0, 1, 12, E.pendiente,  False),
            (lunes_prox + timedelta(1), 10,  0, 2, 13, E.confirmado, True),
            (lunes_prox + timedelta(1), 16, 30, 3 % max(len(medicos),1), 14, E.pendiente, False),
            (lunes_prox + timedelta(2),  9,  0, 0, 15, E.confirmado, True),
            (lunes_prox + timedelta(2), 11, 30, 1, 16, E.pendiente,  False),
            (lunes_prox + timedelta(3), 10, 30, 2, 17, E.pendiente,  False),
            (lunes_prox + timedelta(3), 14,  0, 3 % max(len(medicos),1), 18, E.confirmado, True),
            # Semana pasada — 5 turnos (mayoría realizados, uno ausente)
            (lunes_pasado + timedelta(0), 10,  0, 0, 19 % len(pacientes), E.realizado, True),
            (lunes_pasado + timedelta(1), 11, 30, 1, 20 % len(pacientes), E.realizado, True),
            (lunes_pasado + timedelta(2), 15,  0, 2, 21 % len(pacientes), E.realizado, True),
            (lunes_pasado + timedelta(3), 16, 30, 0, 22 % len(pacientes), E.ausente,   True),
            (lunes_pasado + timedelta(4),  9, 30, 1, 23 % len(pacientes), E.realizado, True),
            # Cancelados dispersos
            (hoy + timedelta(2), 10, 30, 0, 24 % len(pacientes), E.cancelado, False),
            (lunes_prox + timedelta(4), 12, 0, 1, 0, E.cancelado, False),
            (lunes_pasado + timedelta(2), 17, 0, 2, 1, E.cancelado, False),
        ]

        creados = 0
        for dia, hh, mm, im, ip, estado, wa in plan_turnos:
            m = medicos[im % len(medicos)]
            p = pacientes[ip % len(pacientes)]
            db.add(models.Turno(
                paciente_id=p.id,
                medico_id=m.id,
                consultorio=_consul_de(m),
                fecha_hora_inicio=_at(dia, hh, mm),
                duracion_minutos=45,
                estado=estado,
                whatsapp_enviado=wa,
                observaciones=None,
            ))
            creados += 1
        db.commit()
        log.info("Seed demo: %d turnos cargados (hoy, mañana, semana, pasado).", creados)
    except Exception as e:  # noqa: BLE001
        db.rollback()
        log.error("Error en seed demo turnos: %s", e)
    finally:
        db.close()
