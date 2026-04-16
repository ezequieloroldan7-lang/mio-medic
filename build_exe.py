"""
build_exe.py — Empaqueta MIO MEDIC como un .exe portable.

Uso:
    1. pip install pyinstaller
    2. python build_exe.py

Genera:  dist/MioMedic/MioMedic.exe  (carpeta con todo incluido)

El .exe:
    - Levanta FastAPI + uvicorn en localhost:8000
    - Abre el navegador automáticamente
    - SQLite queda como archivo local (miomedic.db) en la misma carpeta
    - WhatsApp y Google Calendar funcionan si hay internet + credenciales
    - Al cerrar la ventana de consola, se apaga el servidor
"""
import subprocess
import sys
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"

# ── 1. Crear el script de entrada (launcher) ────────────────
launcher = BACKEND / "_launcher.py"
launcher.write_text(r'''
import os
import sys
import webbrowser
import threading
import time

# Asegurar que el CWD sea el directorio del ejecutable
if getattr(sys, "frozen", False):
    os.chdir(os.path.dirname(sys.executable))

# Mostrar banner
print()
print("  ==========================================")
print("   MIO MEDIC - Sistema de Turnos")
print("   Medicina Integral Oncologica y Estetica")
print("  ==========================================")
print()
print("  Servidor iniciando en http://localhost:8000")
print("  Presiona Ctrl+C para detener.")
print()

# Abrir navegador después de 2 segundos
def abrir_browser():
    time.sleep(2)
    webbrowser.open("http://localhost:8000")

threading.Thread(target=abrir_browser, daemon=True).start()

# Importar y ejecutar la app
# Necesitamos que las variables de entorno se carguen
env_path = os.path.join(os.path.dirname(sys.executable), ".env")
if os.path.exists(env_path):
    try:
        from dotenv import load_dotenv
        load_dotenv(env_path)
    except ImportError:
        pass

import uvicorn
from main import app

uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")
''', encoding="utf-8")

# ── 2. Ejecutar PyInstaller ─────────────────────────────────
# Incluimos:
#   - Todo el backend como modulos
#   - El frontend como data files
#   - El .env.example como template

cmd = [
    sys.executable, "-m", "PyInstaller",
    "--name", "MioMedic",
    "--console",                    # Ventana de consola visible (para ver logs)
    "--noconfirm",                  # No preguntar si sobreescribir
    "--clean",                      # Limpiar cache anterior

    # Agregar el frontend como carpeta de datos
    "--add-data", f"{FRONTEND};frontend",

    # Importaciones ocultas que PyInstaller no detecta solo
    "--hidden-import", "uvicorn.logging",
    "--hidden-import", "uvicorn.loops",
    "--hidden-import", "uvicorn.loops.auto",
    "--hidden-import", "uvicorn.protocols",
    "--hidden-import", "uvicorn.protocols.http",
    "--hidden-import", "uvicorn.protocols.http.auto",
    "--hidden-import", "uvicorn.protocols.websockets",
    "--hidden-import", "uvicorn.protocols.websockets.auto",
    "--hidden-import", "uvicorn.lifespan",
    "--hidden-import", "uvicorn.lifespan.on",
    "--hidden-import", "uvicorn.lifespan.off",
    "--hidden-import", "sqlalchemy.dialects.sqlite",
    "--hidden-import", "aiofiles",
    "--hidden-import", "multipart",
    "--hidden-import", "httpx",
    "--hidden-import", "apscheduler",
    "--hidden-import", "apscheduler.schedulers.asyncio",
    "--hidden-import", "apscheduler.triggers.interval",

    # Paths de búsqueda
    "--paths", str(BACKEND),

    # Archivo de entrada
    str(launcher),
]

print("=" * 60)
print("  Generando MioMedic.exe ...")
print("=" * 60)
print()
print("Comando:", " ".join(cmd))
print()

result = subprocess.run(cmd, cwd=str(ROOT))

# ── 3. Limpiar launcher temporal ─────────────────────────────
if launcher.exists():
    launcher.unlink()

if result.returncode == 0:
    print()
    print("=" * 60)
    print("  MioMedic.exe generado exitosamente!")
    print(f"  Ubicacion: {ROOT / 'dist' / 'MioMedic' / 'MioMedic.exe'}")
    print()
    print("  Para distribuir:")
    print("  1. Copiar toda la carpeta dist/MioMedic/")
    print("  2. Agregar el archivo .env con las credenciales")
    print("  3. Agregar google-credentials.json si usan GCal")
    print("  4. Doble clic en MioMedic.exe para iniciar")
    print()
    print("  La BD (miomedic.db) se crea automaticamente")
    print("  en la misma carpeta del .exe.")
    print("=" * 60)
else:
    print()
    print("ERROR: PyInstaller fallo. Ver errores arriba.")
    sys.exit(1)
