import os
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker

# Fallback: SQLite en miomedic.db con ruta ABSOLUTA.
# PyInstaller: la DB va junto al .exe, no dentro de _internal.
import sys
if getattr(sys, 'frozen', False):
    _DB_DIR = Path(sys.executable).resolve().parent
else:
    _DB_DIR = Path(__file__).resolve().parent
_DEFAULT_SQLITE = f"sqlite:///{(_DB_DIR / 'miomedic.db').as_posix()}"

# Permite override con DATABASE_URL (útil para Render / Postgres).
SQLALCHEMY_DATABASE_URL = os.getenv("DATABASE_URL", _DEFAULT_SQLITE)

# SQLite necesita check_same_thread=False porque compartimos sesiones
# entre el worker de FastAPI y el scheduler de WhatsApp.
connect_args = {}
if SQLALCHEMY_DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args=connect_args,
    pool_pre_ping=True,
)

# WAL + foreign keys ON para SQLite — más concurrencia, integridad referencial real.
if SQLALCHEMY_DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _sqlite_pragma(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
