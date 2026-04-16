#!/bin/bash
echo ""
echo "=========================================="
echo "  MIO MEDIC - Sistema de Turnos"
echo "  Medicina Integral Oncologica y Estetica"
echo "=========================================="
echo ""

cd "$(dirname "$0")/backend"

echo "[1/3] Instalando dependencias..."
pip install -r ../requirements.txt --quiet

echo "[2/3] Iniciando servidor..."
echo ""
echo "  El sistema estará disponible en:"
echo "  >>> http://localhost:8000 <<<"
echo ""

# Abrir navegador en segundo plano
sleep 2 && (open http://localhost:8000 2>/dev/null || xdg-open http://localhost:8000 2>/dev/null) &

uvicorn main:app --host 0.0.0.0 --port 8000
