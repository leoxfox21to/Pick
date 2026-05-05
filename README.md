# ⚽ Pick Bot — Bot de Predicciones de Fútbol con IA

Bot de Telegram que analiza partidos de fútbol y genera predicciones completas usando Inteligencia Artificial (LLaMA 3.3-70b vía Groq). Combina datos de múltiples APIs para dar picks con análisis estadístico profundo y nivel de confianza.

---

## 🚀 Comandos del Bot

| Comando | Descripción |
|---|---|
| `/partidos` | Lista los partidos de HOY (solo API-Football) |
| `/manana` | Lista los partidos de MAÑANA |
| `/pick <n>` | Análisis completo con IA del partido número `n` de hoy |
| `/pick_manana <n>` | Análisis completo con IA del partido número `n` de mañana |
| `/combinadas` | Mejores 2-3 picks del día con mayor ventaja matemática (Kelly) |
| `/alertas` | Activa/desactiva alertas automáticas de valor en tiempo real |
| `/historial` | Historial de picks generados con resultados |
| `/stats` | Estadísticas globales de aciertos y alta confianza |
| `/ligas` | Aciertos desglosados por liga (mínimo 3 picks) |
| `/rendimiento` | ROI desglosado por tipo de pick, confianza y liga |
| `/bankroll` | Balance del sistema autónomo de bankroll |

**Ejemplo de uso:** `/pick 3` → analiza el partido número 3 de la lista de hoy.

---

## 🧠 ¿Cómo funciona el análisis?

Cuando usas `/pick <n>`, el bot recopila y procesa estos datos:

### Datos estadísticos
- **Últimos 10 partidos** de cada equipo (forma reciente)
- **Head-to-Head** — historial de enfrentamientos directos
- **Standing** — posición en la tabla y puntos
- **Estadísticas de temporada** — goles, victorias, derrotas (API-Football)
- **xG proxy** — goles esperados estimados por el historial

### Análisis avanzado
- **Modelo Poisson** — calcula probabilidades reales de goles para cada equipo
- **Kelly Criterion** — calcula el porcentaje óptimo de bankroll a apostar
- **Value Bet** — detecta si las cuotas del mercado subestiman al equipo
- **Movimiento de cuotas** — compara cuotas actuales vs apertura (señal de dinero inteligente)
- **Eficiencia del mercado** — evalúa qué tan eficiente es la liga para detectar valor
- **Conflicto modelo vs mercado** — cuando el modelo y las cuotas apuntan en direcciones opuestas

### Factores de contexto
- **Lesionados** — jugadores fuera de cada equipo
- **Suspensiones** — jugadores en riesgo por tarjetas
- **Árbitro** — estadísticas del árbitro designado (tarjetas, penales, tendencias)
- **Entrenadores** — nombre y tiempo en el cargo de cada DT
- **Fatiga post-copa** — si el equipo jugó copa entre semana
- **Estadísticas de medio tiempo** — rendimiento en el primer y segundo tiempo

### IA final
Todos los datos se envían a **LLaMA 3.3-70b** (vía Groq) que genera el pick con razonamiento completo, mercado recomendado y nivel de confianza del 1 al 10.

---

## 📡 APIs utilizadas

| API | Para qué sirve | Registro |
|---|---|---|
| **Telegram Bot API** | El bot en sí | [t.me/BotFather](https://t.me/BotFather) |
| **API-Football** (api-sports.io) | Partidos del día, estadísticas, lesiones, árbitros, alineaciones | [dashboard.api-football.com](https://dashboard.api-football.com) |
| **The Odds API** | Cuotas en tiempo real de 45+ ligas | [the-odds-api.com](https://the-odds-api.com) |
| **football-data.org** | Historial H2H, standings, forma reciente (ligas principales) | [football-data.org](https://www.football-data.org/client/register) |
| **Groq** | IA — LLaMA 3.3-70b para generar el pick | [console.groq.com](https://console.groq.com) |

> Todas tienen plan **gratuito** para empezar.

---

## 🤖 Sistema de Bankroll Autónomo

El bot mantiene un bankroll virtual (iniciando en $90) que:
- Registra automáticamente una apuesta por cada pick generado
- Usa **Kelly Criterion** para calcular el tamaño óptimo de cada apuesta
- Actualiza el balance cuando se resuelven los resultados
- Muestra resumen con `/bankroll`

---

## 🔔 Alertas Automáticas

Con `/alertas` activas, el bot escanea los partidos del día cada 3 horas (6am-11pm hora Cuba) y te envía señales cuando detecta:
- Probabilidad implícita de cuotas ≥ 55%
- Movimiento significativo de línea

---

## ⚙️ Instalación en Termux / Linux

### Requisitos
- Python 3.10 o superior

### 1. Clona el repositorio
```bash
git clone https://github.com/leoxfox21to/Pick.git
cd Pick
```

### 2. Instala las dependencias
```bash
pip install -r requirements.txt
```

### 3. Configura las API keys

Crea un archivo `.env` en la carpeta del proyecto:
```bash
nano .env
```

Agrega tus keys:
```env
TELEGRAM_BOT_TOKEN=tu_token_de_botfather

# API-Football (fuente principal de partidos)
APIFOOTBALL_KEY=tu_key_apifootball
APIFOOTBALL_KEY2=          # opcional — segunda key para rotar

# The Odds API (cuotas en tiempo real)
ODDS_API_KEY=tu_key_odds_api
ODDS_API_KEY_2=            # opcional

# football-data.org (historial H2H y standings)
FOOTBALL_DATA_API_KEY=tu_key_football_data
FOOTBALL_DATA_API_KEY_2=   # opcional

# Groq (IA / LLaMA 3.3-70b)
GROQ_API_KEY=tu_key_groq
GROQ_API_KEY_2=            # opcional
```

> El bot rota automáticamente entre múltiples keys para evitar llegar al límite de uso.

### 4. Inicia el bot
```bash
bash start.sh
```

Otros scripts útiles:
```bash
bash stop.sh     # Detiene el bot
bash status.sh   # Muestra si está corriendo
```

---

## 🗂️ Estructura del proyecto

```
Pick/
├── main.py              # Bot principal — comandos y lógica central
├── ai_pick.py           # Generación del pick con IA (Groq / LLaMA 3.3-70b)
├── analyzer.py          # Análisis estadístico, Poisson, Kelly, Value Bet
├── db.py                # Base de datos SQLite — historial, stats, bankroll
├── apifootball.py       # API-Football — partidos, stats, lesiones, árbitros
├── football_api.py      # football-data.org — H2H, standings, forma
├── odds_api.py          # The Odds API — cuotas en tiempo real
├── odds_tracker.py      # Seguimiento de movimiento de cuotas
├── match_api.py         # Caché de partidos del día
├── injuries_api.py      # Lesionados por equipo
├── referee.py           # Estadísticas de árbitros
├── suspensions.py       # Jugadores en riesgo de suspensión
├── data_aggregator.py   # Combina datos de todas las fuentes
├── requirements.txt     # Dependencias Python
├── start.sh             # Inicia el bot en segundo plano
├── stop.sh              # Detiene el bot
└── status.sh            # Muestra el estado del proceso
```

---

## 📊 Base de datos

El bot usa **SQLite** — sin instalación extra. El archivo `picks.db` se crea automáticamente. Guarda:
- Todos los picks generados con fecha, partido y resultado
- Bankroll y registro de apuestas virtuales
- Historial de cuotas para detectar movimiento de línea
- Caché de partidos para reducir llamadas a APIs

---

## 🐛 Problemas comunes

**El bot no responde:**
```bash
bash status.sh
bash stop.sh && bash start.sh
```

**Error de API key:**
- Verifica que el `.env` tenga las keys sin espacios antes/después del `=`
- Asegúrate de que las keys sean válidas en sus respectivos sitios

**"No hay partidos hoy":**
- API-Football puede tardar en actualizar el calendario del día
- Intenta de nuevo después de unos minutos

---

## 📄 Licencia

Uso personal. No redistribuir sin permiso del autor.
