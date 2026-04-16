@echo off
title MIO MEDIC — Sistema de Turnos
color 0A
echo.
echo  ==========================================
echo   MIO MEDIC - Sistema de Turnos
echo   Medicina Integral Oncologica y Estetica
echo  ==========================================
echo.

cd /d "%~dp0backend"

echo [1/3] Verificando entorno Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python no esta instalado.
    echo Por favor instalar desde https://python.org
    pause
    exit /b 1
)

echo [2/3] Instalando dependencias...
pip install -r ../requirements.txt --quiet

echo [3/3] Iniciando servidor...
echo.
echo  El sistema estara disponible en:
echo  >>> http://localhost:8000 <<<
echo.
echo  Para abrirlo en el navegador presiona cualquier tecla...
pause >nul
start http://localhost:8000

uvicorn main:app --host 0.0.0.0 --port 8000
pause
