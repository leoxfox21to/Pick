#!/data/data/com.termux/files/usr/bin/bash

BOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$BOT_DIR/bot.pid"

echo "Deteniendo todas las instancias del bot..."

# Matar TODAS las instancias de main.py que estén corriendo
pkill -f "python3 main.py" 2>/dev/null && echo "Instancias detenidas." || echo "No había instancias corriendo."

# Limpiar el PID file si existe
if [ -f "$PID_FILE" ]; then
    rm "$PID_FILE"
    echo "PID file eliminado."
fi

# Liberar wakelock
termux-wake-unlock 2>/dev/null
echo "Wakelock liberado."
