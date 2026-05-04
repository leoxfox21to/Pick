#!/data/data/com.termux/files/usr/bin/bash

# =============================================
# Bot de Picks Deportivos - Inicio en Termux
# =============================================

BOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="$BOT_DIR/bot.log"
PID_FILE="$BOT_DIR/bot.pid"

cd "$BOT_DIR"

# Cargar variables de entorno desde .env
if [ -f ".env" ]; then
    export $(grep -v '^#' .env | xargs)
    echo "Variables de entorno cargadas."
else
    echo "ERROR: No se encontró .env. Copia .env.example a .env y llena tus keys."
    exit 1
fi

# Instalar dependencias con las versiones correctas
echo "Instalando dependencias..."
pip install -r requirements.txt --quiet
echo "Dependencias listas."

# Verificar si ya está corriendo
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "El bot ya está corriendo (PID: $OLD_PID)"
        echo "Para detenerlo: bash stop.sh"
        exit 0
    fi
fi

# Activar wakelock para que Android no mate el proceso
echo "Activando wakelock (mantiene el proceso vivo)..."
termux-wake-lock 2>/dev/null && echo "Wakelock activado." || echo "Wakelock no disponible (instala Termux:API si quieres)"

echo "Iniciando bot en segundo plano..."
nohup python3 main.py >> "$LOG_FILE" 2>&1 &
BOT_PID=$!
echo $BOT_PID > "$PID_FILE"
echo "Bot iniciado (PID: $BOT_PID)"
echo ""
echo "Comandos útiles:"
echo "  Ver logs en vivo : tail -f $LOG_FILE"
echo "  Ver estado       : bash status.sh"
echo "  Detener          : bash stop.sh"
