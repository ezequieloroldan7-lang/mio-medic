"""
backup.py — Job de backup automático para SQLite.

Copia la base de datos a backups/miomedic-YYYYMMDD.db usando el API oficial
de SQLite (no shutil.copy), que es seguro aún con la app en uso y con WAL
activo. Aplica rotación por antigüedad (default 14 días).

Si DATABASE_URL no apunta a SQLite (ej. Postgres en Render Pro), el job se
saltea silenciosamente — el backup en ese caso lo hace el proveedor.
"""
import logging
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy.engine import Engine

log = logging.getLogger("miomedic.backup")

RETENTION_DAYS = int(os.getenv("BACKUP_RETENTION_DAYS", "14"))


def _sqlite_path(engine: Engine) -> Path | None:
    url = engine.url
    if (url.drivername or "").split("+")[0] != "sqlite":
        return None
    db = url.database  # ej: "./miomedic.db" o "/abs/path.db"
    if not db or db == ":memory:":
        return None
    p = Path(db)
    if not p.is_absolute():
        # SQLAlchemy resuelve relativo al CWD del proceso
        p = (Path.cwd() / db).resolve()
    return p


def _backups_dir(db_path: Path) -> Path:
    # Backup en render.yaml mapea /backend como disk; guardamos junto a la BD.
    d = db_path.parent / "backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _do_backup(db_path: Path, dest: Path) -> None:
    """Usa el backup API de SQLite (safe bajo carga, respeta WAL)."""
    # connect a la BD origen en read-only y a destino como nueva base
    src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(str(dest))
        try:
            with dst:
                src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def _rotate(backup_dir: Path, retention_days: int) -> int:
    cutoff = datetime.now() - timedelta(days=retention_days)
    removed = 0
    for f in backup_dir.glob("miomedic-*.db"):
        try:
            if datetime.fromtimestamp(f.stat().st_mtime) < cutoff:
                f.unlink()
                removed += 1
        except OSError as e:
            log.warning("No se pudo rotar %s: %s", f, e)
    return removed


def run_backup(engine: Engine) -> Path | None:
    """Ejecuta un backup. Devuelve el path del archivo creado o None si se saltea."""
    db_path = _sqlite_path(engine)
    if db_path is None or not db_path.exists():
        log.info("Backup skipped (no SQLite o archivo ausente).")
        return None

    bdir = _backups_dir(db_path)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = bdir / f"miomedic-{stamp}.db"
    try:
        _do_backup(db_path, dest)
    except (sqlite3.Error, OSError) as e:
        log.exception("Backup fallido: %s", e)
        # Si quedó un archivo parcial, borrarlo
        if dest.exists():
            try:
                dest.unlink()
            except OSError:
                pass
        return None

    removed = _rotate(bdir, RETENTION_DAYS)
    log.info(
        "Backup OK → %s (%.1f KB). Rotación: %d antiguos eliminados.",
        dest.name, dest.stat().st_size / 1024, removed,
    )
    return dest
