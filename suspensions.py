"""
Riesgo de suspensión por tarjetas amarillas acumuladas.
Fuente: API-Football (requiere key). SofaScore como fallback.
Umbral estándar en la mayoría de ligas europeas: 5 amarillas = 1 partido de sanción.
"""
import os
import time
import logging
import requests

logger = logging.getLogger(__name__)

APIFB_KEYS = [k for i in ["1", "2", "3"]
              if (k := os.environ.get(f"API_FOOTBALL_KEY_{i}", "").strip())]
if not APIFB_KEYS:
    _fb = os.environ.get("API_FOOTBALL_KEY", "").strip()
    if _fb:
        APIFB_KEYS = [_fb]

_APIFB_URL = "https://v3.football.api-sports.io"
_key_idx = 0
_cache = {}
CACHE_TTL = 3600  # 1h

# Umbrales por liga (número de amarillas antes de suspensión)
SUSPENSION_THRESHOLD = {
    "soccer_epl": 5,
    "soccer_spain_la_liga": 5,
    "soccer_germany_bundesliga": 5,
    "soccer_italy_serie_a": 5,
    "soccer_france_ligue_one": 5,
    "soccer_uefa_champs_league": 3,
    "soccer_uefa_europa_league": 3,
    "soccer_uefa_europa_conference_league": 3,
    "soccer_england_efl_champ": 5,
    "soccer_portugal_primeira_liga": 5,
    "soccer_netherlands_eredivisie": 5,
    "soccer_brazil_campeonato": 3,
    "soccer_usa_mls": 5,
    "soccer_argentina_primera_division": 4,
    "soccer_mexico_ligamx": 5,
    "soccer_conmebol_copa_libertadores": 3,
    "soccer_conmebol_copa_sudamericana": 3,
}
DEFAULT_THRESHOLD = 5


def _apifb_key():
    global _key_idx
    if not APIFB_KEYS:
        return None
    key = APIFB_KEYS[_key_idx % len(APIFB_KEYS)]
    _key_idx = (_key_idx + 1) % len(APIFB_KEYS)
    return key


def _apifb_get(endpoint, params):
    key = _apifb_key()
    if not key:
        return None
    try:
        resp = requests.get(
            f"{_APIFB_URL}/{endpoint}",
            headers={"x-rapidapi-key": key, "x-rapidapi-host": "v3.football.api-sports.io"},
            params=params,
            timeout=10,
        )
        if resp.status_code == 200:
            return resp.json().get("response", [])
        logger.debug(f"API-Football {endpoint}: HTTP {resp.status_code}")
    except Exception as e:
        logger.debug(f"API-Football {endpoint} error: {e}")
    return None


def get_suspension_risks(team_id: int | None, team_name: str,
                         sport_key: str, season: int | None = None) -> list:
    """
    Devuelve lista de jugadores en riesgo de suspensión por acumulación de tarjetas.
    Formato: [{"name": str, "position": str, "yellows": int, "threshold": int, "risk": str}]
    """
    if not team_id or not APIFB_KEYS:
        return []

    cache_key = f"susp_{team_id}_{sport_key}"
    c = _cache.get(cache_key)
    if c and time.time() - c["ts"] < CACHE_TTL:
        return c["v"]

    if season is None:
        from datetime import datetime
        year = datetime.now().year
        season = year if datetime.now().month >= 7 else year - 1

    threshold = SUSPENSION_THRESHOLD.get(sport_key, DEFAULT_THRESHOLD)

    # Buscar el league_id correspondiente al sport_key
    from apifootball import SPORT_KEY_TO_LEAGUE
    league_id = SPORT_KEY_TO_LEAGUE.get(sport_key)
    if not league_id:
        return []

    players = _apifb_get("players", {
        "team": team_id,
        "league": league_id,
        "season": season,
    })

    if not players:
        return []

    risks = []
    for entry in players:
        player = entry.get("player", {})
        stats_list = entry.get("statistics", [])
        if not stats_list:
            continue
        stats = stats_list[0]
        cards = stats.get("cards", {})
        yellows = cards.get("yellow", 0) or 0
        # "yellowred" son dobles amarillas que ya resultaron en expulsión
        yellows_active = yellows % threshold  # amarillas que cuenta hacia próxima sanción

        if yellows_active >= threshold - 1:  # 1 amarilla más = suspensión
            name = player.get("name", "?")
            pos = stats.get("games", {}).get("position", "")
            risk_level = "🚨 SUSPENSIÓN en 1 tarjeta" if yellows_active >= threshold - 1 else "⚠️ En riesgo"
            risks.append({
                "name": name,
                "position": pos,
                "yellows": yellows,
                "yellows_active": yellows_active,
                "threshold": threshold,
                "risk": risk_level,
            })

    # Ordenar por mayor riesgo primero
    risks.sort(key=lambda x: x["yellows_active"], reverse=True)
    result = risks[:4]  # máximo 4 jugadores

    _cache[cache_key] = {"v": result, "ts": time.time()}
    if result:
        logger.info(f"Riesgos de suspensión {team_name}: {[r['name'] for r in result]}")
    return result


def format_suspensions(risks: list, team_name: str) -> str:
    """Bloque para Telegram."""
    if not risks:
        return ""
    lines = [f"\n🟨 *Riesgo de suspensión — {team_name}:*"]
    for r in risks:
        pos = f" ({r['position']})" if r.get("position") else ""
        lines.append(f"  {r['risk']}: {r['name']}{pos} ({r['yellows_active']}/{r['threshold']} amarillas)")
    return "\n".join(lines)


def format_suspensions_for_ai(home_risks: list, away_risks: list,
                               home_name: str, away_name: str) -> str:
    """Versión compacta para el prompt de la IA."""
    def _fmt(risks, name):
        if not risks:
            return f"{name}: Sin riesgo de suspensión"
        parts = [f"{r['name']} ({r['yellows_active']}/{r['threshold']} ⚠️)" for r in risks]
        return f"{name}: {', '.join(parts)}"

    return _fmt(home_risks, home_name) + "\n" + _fmt(away_risks, away_name)
