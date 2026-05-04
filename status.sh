#!/data/data/com.termux/files/usr/bin/bash

BOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$BOT_DIR/bot.pid"
LOG_FILE="$BOT_DIR/bot.log"

if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        echo "✅ Bot CORRIENDO (PID: $PID)"
    else
        echo "❌ Bot DETENIDO (PID guardado: $PID pero no existe)"
    fi
else
    echo "❌ Bot NO está corriendo"
fi

echo ""
echo "--- Últimas 20 líneas del log ---"
tail -20 "$LOG_FILE" 2>/dev/null || echo "Sin logs aún."
