import requests
import logging

logger = logging.getLogger(__name__)

TEAM_CITY = {
    "chelsea": "London", "arsenal": "London", "tottenham": "London",
    "west ham": "London", "crystal palace": "London", "fulham": "London",
    "brentford": "London", "nottingham forest": "Nottingham",
    "manchester city": "Manchester", "manchester united": "Manchester",
    "liverpool": "Liverpool", "everton": "Liverpool",
    "newcastle": "Newcastle upon Tyne", "aston villa": "Birmingham",
    "wolverhampton": "Wolverhampton", "leicester": "Leicester",
    "leeds": "Leeds", "brighton": "Brighton", "southampton": "Southampton",
    "ipswich": "Ipswich", "bournemouth": "Bournemouth",
    "sheffield united": "Sheffield", "sheffield wednesday": "Sheffield",
    "burnley": "Burnley", "luton": "Luton", "sunderland": "Sunderland",
    "real madrid": "Madrid", "atletico madrid": "Madrid", "getafe": "Madrid",
    "rayo vallecano": "Madrid", "leganes": "Madrid",
    "barcelona": "Barcelona", "espanyol": "Barcelona",
    "sevilla": "Sevilla", "betis": "Sevilla", "real betis": "Sevilla",
    "valencia": "Valencia", "villarreal": "Villarreal",
    "athletic": "Bilbao", "athletic club": "Bilbao",
    "real sociedad": "San Sebastian", "osasuna": "Pamplona",
    "mallorca": "Palma", "celta vigo": "Vigo",
    "alaves": "Vitoria-Gasteiz", "cadiz": "Cadiz", "granada": "Granada",
    "almeria": "Almeria", "girona": "Girona", "las palmas": "Las Palmas",
    "bayern": "Munich", "fc bayern": "Munich",
    "borussia dortmund": "Dortmund", "dortmund": "Dortmund",
    "rb leipzig": "Leipzig", "bayer leverkusen": "Leverkusen",
    "eintracht frankfurt": "Frankfurt", "wolfsburg": "Wolfsburg",
    "borussia monchengladbach": "Monchengladbach",
    "schalke": "Gelsenkirchen", "hertha": "Berlin", "union berlin": "Berlin",
    "juventus": "Turin", "torino": "Turin",
    "inter": "Milan", "ac milan": "Milan", "milan": "Milan",
    "roma": "Rome", "lazio": "Rome",
    "napoli": "Naples", "fiorentina": "Florence",
    "atalanta": "Bergamo", "bologna": "Bologna",
    "sassuolo": "Reggio Emilia", "udinese": "Udine",
    "verona": "Verona", "cagliari": "Cagliari",
    "psg": "Paris", "paris saint-germain": "Paris", "paris sg": "Paris",
    "marseille": "Marseille", "lyon": "Lyon", "monaco": "Monaco",
    "nice": "Nice", "lille": "Lille", "rennes": "Rennes",
    "porto": "Porto", "benfica": "Lisbon", "sporting": "Lisbon",
    "ajax": "Amsterdam", "psv": "Eindhoven", "feyenoord": "Rotterdam",
    "celtic": "Glasgow", "rangers": "Glasgow",
    "anderlecht": "Brussels", "club brugge": "Bruges",
    "galatasaray": "Istanbul", "fenerbahce": "Istanbul", "besiktas": "Istanbul",
    "zenit": "Saint Petersburg", "cska moscow": "Moscow", "spartak": "Moscow",
    "river plate": "Buenos Aires", "boca juniors": "Buenos Aires",
    "racing club": "Buenos Aires", "independiente": "Buenos Aires",
    "flamengo": "Rio de Janeiro", "fluminense": "Rio de Janeiro",
    "corinthians": "Sao Paulo", "palmeiras": "Sao Paulo", "sao paulo": "Sao Paulo",
    "santos": "Santos", "atletico mineiro": "Belo Horizonte",
    "america": "Mexico City", "cruz azul": "Mexico City",
    "chivas": "Guadalajara",
    "tigres": "Monterrey", "monterrey": "Monterrey",
}

# Estadios cubiertos o con césped sintético conocido
# True = cubierto/cerrado (clima irrelevante), "synthetic" = sintético exterior
STADIUM_TYPE = {
    "toronto fc": "covered",
    "minnesota united": "covered",
    "atlanta united": "covered",
    "inter miami": "natural",
    "vancouver whitecaps": "covered",
    "fc dallas": "natural",
    "az alkmaar": "synthetic",
    "fc groningen": "synthetic",
    "heracles": "synthetic",
    "nec nijmegen": "synthetic",
    "zulte waregem": "synthetic",
    "genk": "synthetic",
}


def get_weather_for_team(home_team_name: str) -> dict | None:
    """Obtiene el clima actual de la ciudad del equipo local via wttr.in (sin API key)."""
    name_lower = home_team_name.lower().strip()

    # Comprobar si es estadio cubierto
    for key, stype in STADIUM_TYPE.items():
        if key in name_lower:
            if stype == "covered":
                return {
                    "city": home_team_name,
                    "covered": True,
                    "temp_c": "?",
                    "feels_like": "?",
                    "desc": "Estadio cubierto — clima no aplica",
                    "wind_kmph": 0,
                    "humidity": "?",
                    "rain_prob": 0,
                    "snow_prob": 0,
                    "turf": "Cubierto",
                }
            if stype == "synthetic":
                break  # Sigue obteniendo clima pero lo anotará como sintético

    city = None
    for key, c in TEAM_CITY.items():
        if key in name_lower or name_lower.startswith(key[:6]):
            city = c
            break
    if not city:
        words = home_team_name.split()
        city = words[0] if words else home_team_name

    # Tipo de césped
    turf = "Sintético" if any(k in name_lower for k in STADIUM_TYPE if STADIUM_TYPE[k] == "synthetic") else "Natural"

    try:
        url = f"https://wttr.in/{city}?format=j1"
        resp = requests.get(url, timeout=6)
        if resp.status_code == 200:
            data = resp.json()
            current = data["current_condition"][0]
            temp_c = current.get("temp_C", "?")
            desc = (current.get("weatherDesc") or [{}])[0].get("value", "despejado")
            wind_kmph = int(current.get("windspeedKmph", 0))
            humidity = current.get("humidity", "?")
            feels_like = current.get("FeelsLikeC", temp_c)
            wind_dir = current.get("winddir16Point", "")

            rain_prob = 0
            snow_prob = 0
            try:
                from datetime import datetime, timezone
                now_hour = datetime.now(timezone.utc).hour
                hourly = data["weather"][0]["hourly"]
                relevant = [
                    h for h in hourly
                    if abs(int(h.get("time", "0")) // 100 - now_hour) <= 6
                ] or hourly
                rain_prob = max(int(h.get("chanceofrain", 0)) for h in relevant)
                snow_prob = max(int(h.get("chanceofsnow", 0)) for h in relevant)
                if rain_prob >= 50 and "sunny" in desc.lower():
                    nearest = min(
                        hourly,
                        key=lambda h: abs(int(h.get("time", "0")) // 100 - now_hour)
                    )
                    fd = (nearest.get("weatherDesc") or [{}])[0].get("value", "")
                    if fd:
                        desc = fd
            except Exception:
                pass

            return {
                "city": city,
                "covered": False,
                "temp_c": int(temp_c) if str(temp_c).lstrip("-").isdigit() else temp_c,
                "feels_like": feels_like,
                "desc": desc,
                "wind_kmph": wind_kmph,
                "wind_dir": wind_dir,
                "humidity": humidity,
                "rain_prob": rain_prob,
                "snow_prob": snow_prob,
                "turf": turf,
            }
    except Exception as e:
        logger.debug(f"Weather error for {home_team_name}/{city}: {e}")
    return None


def format_weather(w: dict | None) -> str:
    """Formatea el bloque de clima para Telegram."""
    if not w:
        return ""
    if w.get("covered"):
        return f"\n🏟️ *Clima:* Estadio cubierto — sin impacto climático"

    rain = w.get("rain_prob", 0)
    snow = w.get("snow_prob", 0)
    wind = w.get("wind_kmph", 0)
    turf = w.get("turf", "Natural")

    if snow >= 30:
        icon = "❄️"
    elif rain >= 60:
        icon = "🌧️"
    elif rain >= 30:
        icon = "🌦️"
    else:
        icon = "☀️"

    notes = []
    if rain >= 60:
        notes.append("⚠️ _Lluvia intensa reduce goles y ritmo_")
    elif rain >= 30:
        notes.append("🌧️ _Posible lluvia — campo pesado_")
    if snow >= 30:
        notes.append("❄️ _Nieve — condiciones muy difíciles_")
    if wind >= 50:
        notes.append("💨 _Viento fuerte (≥50 km/h) afecta juego aéreo y centros_")
    elif wind >= 35:
        notes.append("🌬️ _Viento moderado — puede afectar centros largos_")
    try:
        if int(str(w.get("temp_c", 20)).lstrip("-")) <= 2:
            notes.append("🥶 _Frío extremo — menor ritmo de juego_")
    except Exception:
        pass
    if turf == "Sintético":
        notes.append("🟩 _Césped sintético — pelota más rápida_")
    note_str = "\n  " + "\n  ".join(notes) if notes else ""

    return (
        f"\n{icon} *Clima ({w['city']}):*\n"
        f"  🌡️ {w['temp_c']}°C (sens. {w['feels_like']}°C) | {w['desc']}\n"
        f"  💨 Viento: {wind} km/h | 🌧️ P.lluvia: {rain}% | 🟩 {turf}"
        f"{note_str}"
    )


def weather_for_ai(w: dict | None, home_team: str) -> str:
    """Versión para el prompt de IA."""
    if not w:
        return f"Sin datos de clima para {home_team}"
    if w.get("covered"):
        return f"Estadio cubierto — el clima no afecta este partido"

    rain = w.get("rain_prob", 0)
    wind = w.get("wind_kmph", 0)
    snow = w.get("snow_prob", 0)
    turf = w.get("turf", "Natural")

    impacts = []
    if rain >= 60:
        impacts.append("LLUVIA INTENSA → esperar menos goles, campo pesado, favorece equipo físico")
    elif rain >= 30:
        impacts.append("Posible lluvia → ventaja leve para equipos físicos")
    if wind >= 50:
        impacts.append(f"VIENTO FUERTE ({wind} km/h) → afecta juego aéreo, centros y tiros largos")
    elif wind >= 35:
        impacts.append(f"Viento moderado ({wind} km/h) → puede perturbar centros")
    if snow >= 30:
        impacts.append("NIEVE → partido muy imprevisible, tendencia a marcadores bajos")
    try:
        if int(str(w.get("temp_c", 20)).lstrip("-")) <= 2:
            impacts.append("Frío extremo → menor ritmo, menos presión alta")
    except Exception:
        pass
    if turf == "Sintético":
        impacts.append("Césped sintético → juego más rápido, más rebotes, equipos técnicos favorecidos")

    impact_str = " | ".join(impacts) if impacts else "Sin impacto climático significativo"

    return (
        f"Ciudad: {w['city']} | Temp: {w['temp_c']}°C (sens. {w.get('feels_like','?')}°C) | {w['desc']}\n"
        f"Lluvia: {rain}% | Nieve: {snow}% | Viento: {wind} km/h | Césped: {turf}\n"
        f"Impacto estimado: {impact_str}"
    )
