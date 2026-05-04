#!/data/data/com.termux/files/usr/bin/bash

# =============================================
# Match API - Inicio en Termux
# =============================================

BOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="$BOT_DIR/api.log"
PID_FILE="$BOT_DIR/api.pid"

cd "$BOT_DIR"

# Cargar variables de entorno desde .env
if [ -f ".env" ]; then
    export $(grep -v '^#' .env | xargs)
    echo "Variables de entorno cargadas."
else
    echo "ERROR: No se encontró .env"
    exit 1
fi

# Verificar si ya está corriendo
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "La API ya está corriendo (PID: $OLD_PID)"
        echo "Para detenerla: pkill -f 'python3 match_api.py'"
        exit 0
    fi
fi

# Puerto por defecto 5000
export PORT=${PORT:-5000}

echo "Iniciando Match API en puerto $PORT..."
nohup python3 match_api.py >> "$LOG_FILE" 2>&1 &
API_PID=$!
echo $API_PID > "$PID_FILE"
echo "API iniciada (PID: $API_PID)"
echo ""
echo "Endpoints disponibles:"
echo "  http://localhost:$PORT/health"
echo "  http://localhost:$PORT/matches/today"
echo "  http://localhost:$PORT/match/data?home=Arsenal&away=Chelsea&sport_key=soccer_epl"
echo "  http://localhost:$PORT/match/pick?home=Arsenal&away=Chelsea&sport_key=soccer_epl"
echo ""
echo "Ver logs: tail -f $LOG_FILE"
