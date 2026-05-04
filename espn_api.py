import requests
import logging
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

BASE_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer"

ESPN_LEAGUES = {
    "soccer_epl": "eng.1",
    "soccer_spain_la_liga": "esp.1",
    "soccer_germany_bundesliga": "ger.1",
    "soccer_italy_serie_a": "ita.1",
    "soccer_france_ligue_one": "fra.1",
    "soccer_usa_mls": "usa.1",
    "soccer_conmebol_copa_libertadores": "conmebol.libertadores",
    "soccer_argentina_primera_division": "arg.1",
    "soccer_brazil_campeonato": "bra.1",
    "soccer_mexico_ligamx": "mex.1",
    "soccer_korea_kleague1": "kor.1",
    "soccer_japan_j_league": "jpn.1",
    "soccer_portugal_primeira_liga": "por.1",
    "soccer_netherlands_eredivisie": "ned.1",
    "soccer_turkey_super_league": "tur.1",
    "soccer_scotland_premiership": "sco.1",
    "soccer_belgium_first_div": "bel.1",
    "soccer_denmark_superliga": "den.1",
    "soccer_norway_eliteserien": "nor.1",
    "soccer_sweden_allsvenskan": "swe.1",
    "soccer_poland_ekstraklasa": "pol.1",
    "soccer_austria_bundesliga": "aut.1",
    "soccer_switzerland_superleague": "sui.1",
    "soccer_greece_super_league": "gre.1",
    "soccer_russia_premier_league": "rus.1",
    "soccer_saudi_league": "sau.1",
    "soccer_chile_campeonato": "chi.1",
    "soccer_colombia_primera_a": "col.1",
    "soccer_ecuador_liga_pro": "ecu.1",
    "soccer_peru_primera_division": "per.1",
    "soccer_uruguay_primera_division": "uru.1",
    "soccer_australia_aleague": "aus.1",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}

_cache = {}
CACHE_TTL = 3600
CACHE_TTL_TEAM = 86400


def _get(url, params=None):
    try:
        resp = requests.get(url, headers=HEADERS, params=params, timeout=10)
        if resp.status_code == 200:
            return resp.json()
        logger.debug(f"ESPN HTTP {resp.status_code}: {url}")
    except Exception as e:
        logger.debug(f"ESPN error {url}: {e}")
    return None


def search_team(name: str, league_slug: str = "all") -> int | None:
    """Busca el ID de un equipo en ESPN."""
    key = f"team_{name.lower()}_{league_slug}"
    c = _cache.get(key)
    if c and time.time() - c["ts"] < CACHE_TTL_TEAM:
        return c["v"]

    name_lower = name.lower().strip()
    data = _get(f"{BASE_URL}/{league_slug}/teams", params={"limit": 1000})
    if not data:
        return None

    best_id = None
    best_score = 0
    for sport in data.get("sports", []):
        for league in sport.get("leagues", []):
            for team_entry in league.get("teams", []):
                t = team_entry.get("team", {})
                tname = t.get("displayName", "").lower()
                tshort = t.get("abbreviation", "").lower()
                score = 0
                if name_lower == tname:
                    score = 100
                elif name_lower in tname or tname in name_lower:
                    score = 80
                elif name_lower.split()[0] in tname or tshort == name_lower[:3]:
                    score = 40
                if score > best_score:
                    best_score = score
                    try:
                        best_id = int(t.get("id"))
                    except Exception:
                        pass

    if best_id and best_score >= 40:
        _cache[key] = {"v": best_id, "ts": time.time()}
        logger.info(f"ESPN team '{name}' ({league_slug}) → ID {best_id}")
        return best_id
    return None


def _convert_event(ev, home_team_name="", away_team_name="") -> dict:
    """Convierte evento ESPN al formato estándar del bot."""
    competitions = ev.get("competitions", [{}])
    comp = competitions[0] if competitions else {}
    competitors = comp.get("competitors", [])

    home_c = next((c for c in competitors if c.get("homeAway") == "home"), {})
    away_c = next((c for c in competitors if c.get("homeAway") == "away"), {})

    def score_val(c):
        try:
            return int(c.get("score", ""))
        except Exception:
            return None

    date_str = ev.get("date", "")
    status = ev.get("status", {}).get("type", {}).get("name", "")
    is_finished = "Final" in status or "final" in status.lower()

    return {
        "id": ev.get("id"),
        "utcDate": date_str,
        "status": "FINISHED" if is_finished else "SCHEDULED",
        "homeTeam": {
            "id": home_c.get("team", {}).get("id"),
            "name": home_c.get("team", {}).get("displayName", home_team_name),
        },
        "awayTeam": {
            "id": away_c.get("team", {}).get("id"),
            "name": away_c.get("team", {}).get("displayName", away_team_name),
        },
        "score": {
            "fullTime": {"home": score_val(home_c), "away": score_val(away_c)},
            "halfTime": {"home": None, "away": None},
        },
        "competition": {
            "id": None,
            "name": ev.get("season", {}).get("slug", ""),
            "type": "",
        },
        "_source": "espn",
    }


def get_team_matches(team_name: str, sport_key: str = "", last: int = 20) -> tuple[int | None, list]:
    """Devuelve (team_id, lista_de_partidos) usando ESPN."""
    league_slug = ESPN_LEAGUES.get(sport_key, "all")
    team_id = search_team(team_name, league_slug)
    if not team_id:
        team_id = search_team(team_name, "all")
    if not team_id:
        return None, []

    cache_key = f"schedule_{team_id}_{league_slug}"
    c = _cache.get(cache_key)
    if c and time.time() - c["ts"] < CACHE_TTL:
        return team_id, c["v"]

    data = _get(f"{BASE_URL}/{league_slug}/teams/{team_id}/schedule")
    if not data:
        return team_id, []

    events = data.get("events", [])
    finished = [
        _convert_event(ev)
        for ev in events
        if "Final" in ev.get("status", {}).get("type", {}).get("name", "")
    ]
    # Ordenar por fecha descendente
    finished.sort(key=lambda e: e.get("utcDate", ""), reverse=True)
    result = finished[:last]
    _cache[cache_key] = {"v": result, "ts": time.time()}
    logger.info(f"ESPN '{team_name}' ({league_slug}) → {len(result)} partidos")
    return team_id, result
