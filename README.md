# ⚽ Pick Bot — Bot de Picks de Fútbol con IA

Bot de Telegram que genera análisis completos de partidos de fútbol usando Inteligencia Artificial (LLaMA 3.3 vía Groq). Combina estadísticas reales de múltiples APIs para dar picks precisos con nivel de confianza.

---

## 🚀 Comandos del Bot

| Comando | Descripción |
|---|---|
| `/partidos` | Lista los partidos de HOY (45+ ligas) |
| `/manana` | Lista los partidos de MAÑANA |
| `/pick <n>` | Análisis completo con IA del partido número `n` de hoy |
| `/pick_manana <n>` | Análisis completo con IA del partido número `n` de mañana |
| `/historial` | Historial de picks generados |
| `/stats` | Estadísticas globales de aciertos |
| `/ligas` | Aciertos desglosados por liga |
| `/alertas` | Activa alertas automáticas de valor |

**Ejemplo de uso:** `/pick 3` → analiza el partido número 3 de la lista de hoy.

---

## 📡 APIs que usa el bot

| API | Para qué sirve | Registro |
|---|---|---|
| **Telegram Bot API** | El bot en sí | [t.me/BotFather](https://t.me/BotFather) |
| **football-data.org** | Partidos, standings, H2H | [football-data.org](https://www.football-data.org/client/register) |
| **The Odds API** | Cuotas en tiempo real | [the-odds-api.com](https://the-odds-api.com) |
| **API-Football** (RapidAPI) | Estadísticas completas todas las ligas | [dashboard.api-football.com](https://dashboard.api-football.com) |
| **Groq** | IA / LLaMA 3.3-70b para generar el pick | [console.groq.com](https://console.groq.com) |
| **RapidAPI Football** | Lesiones y suspensiones *(opcional)* | [rapidapi.com](https://rapidapi.com/Creativesdev/api/free-api-live-football-data) |

> Todas tienen plan **gratuito** para empezar.

---

## ⚙️ Instalación

### Requisitos
- Python 3.10 o superior
- pip

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

Copia el archivo de ejemplo y edítalo:
```bash
cp .env.example .env
nano .env
```

Llena cada variable con tus keys:
```env
TELEGRAM_BOT_TOKEN=tu_token_de_botfather

FOOTBALL_DATA_API_KEY_1=tu_key_football_data
FOOTBALL_DATA_API_KEY_2=        # opcional — para rotar y no llegar al límite

GROQ_API_KEY_1=tu_key_groq
GROQ_API_KEY_2=                 # opcional

ODDS_API_KEY=tu_key_odds_api

APIFOOTBALL_KEY=tu_key_apifootball
APIFOOTBALL_KEY_2=              # opcional

RAPIDAPI_KEY=tu_key_rapidapi    # opcional — para lesiones/suspensiones
```

> El bot rota automáticamente entre múltiples keys para evitar límites de uso.

### 4. Inicia el bot
```bash
bash start.sh
```

Para detenerlo:
```bash
bash stop.sh
```

Para ver el estado:
```bash
bash status.sh
```

---

## 🗂️ Estructura del proyecto

```
Pick/
├── main.py              # Bot principal — comandos y lógica central
├── ai_pick.py           # Generación del pick con IA (Groq/LLaMA)
├── analyzer.py          # Análisis estadístico y modelo Poisson
├── db.py                # Base de datos SQLite (historial y stats)
├── match_api.py         # Obtención y caché de partidos del día
├── football_api.py      # API football-data.org
├── apifootball.py       # API-Football (todas las ligas)
├── odds_api.py          # The Odds API — cuotas en tiempo real
├── odds_tracker.py      # Seguimiento de movimiento de cuotas
├── injuries_api.py      # Lesionados y suspendidos
├── referee.py           # Historial de árbitros
├── sofascore_api.py     # Datos de SofaScore
├── espn_api.py          # Datos de ESPN
├── data_aggregator.py   # Combina datos de todas las fuentes
├── suspensions.py       # Jugadores suspendidos
├── weather.py           # Clima en la ciudad del partido
├── requirements.txt     # Dependencias Python
├── .env.example         # Plantilla de variables de entorno
├── start.sh             # Script para iniciar el bot
├── stop.sh              # Script para detener el bot
└── status.sh            # Script para ver el estado
```

---

## 🧠 ¿Cómo funciona el pick?

Cuando usas `/pick 3`, el bot:

1. **Recopila datos reales** — forma reciente, head-to-head, standings, cuotas, lesionados, árbitro, clima.
2. **Modelo Poisson** — calcula probabilidades de goles para cada equipo.
3. **Análisis de valor** — compara probabilidades reales vs cuotas del mercado.
4. **IA (LLaMA 3.3-70b)** — recibe todos los datos y genera el pick con razonamiento.
5. **Nivel de confianza** — califica el pick del 1 al 10 según la solidez de los datos.
6. **Guarda el resultado** — registra el pick en SQLite para calcular el historial de aciertos.

---

## 📊 Base de datos

El bot usa **SQLite** (sin instalación extra). El archivo `picks.db` se crea automáticamente al iniciar. Guarda:
- Picks generados con fecha y partido
- Resultado real (se actualiza automáticamente)
- Aciertos/fallos por liga

---

## 🐛 Problemas comunes

**El bot no responde:**
```bash
bash status.sh   # ¿está corriendo?
bash stop.sh && bash start.sh   # reinicia
```

**Error de API key:**
- Verifica que el archivo `.env` tiene todas las keys correctas.
- Revisa que no haya espacios antes o después del `=`.

**"No hay partidos":**
- football-data.org solo cubre las ligas principales (Premier League, La Liga, Serie A, etc.)
- The Odds API amplía la cobertura a 45+ ligas.

---

## 📄 Licencia

Uso personal. No redistribuir sin permiso del autor.
