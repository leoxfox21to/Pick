import os
import requests
import logging
from datetime import date, timedelta

logger = logging.getLogger(__name__)

BASE_URL = "https://api.football-data.org/v4"

SPORT_KEY_TO_FD_CODE = {
    "soccer_epl": "PL",
    "soccer_spain_la_liga": "PD",
    "soccer_germany_bundesliga": "BL1",
    "soccer_italy_serie_a": "SA",
    "soccer_france_ligue_one": "FL1",
    "soccer_uefa_champs_league": "CL",
    "soccer_uefa_europa_league": "EL",
    "soccer_portugal_primeira_liga": "PPL",
    "soccer_netherlands_eredivisie": "DED",
    "soccer_brazil_campeonato": "BSA",
    "soccer_usa_mls": "MLS",
    "soccer_england_efl_champ": "ELC",
    "soccer_scotland_premiership": "PPL",
}

_comp_teams_cache = {}

def get_competition_teams_map(competition_code):
    if competition_code in _comp_teams_cache:
        return _comp_teams_cache[competition_code]
    try:
        url = f"{BASE_URL}/competitions/{competition_code}/teams"
        resp = _request_with_rotation(url, timeout=10)
        if resp and resp.status_code == 200:
            teams = resp.json().get("teams", [])
            team_map = {}
            for t in teams:
                tid = t.get("id")
                if not tid:
                    continue
                for key in ["name", "shortName"]:
                    val = t.get(key, "").lower().strip()
                    if val:
                        team_map[val] = tid
            _comp_teams_cache[competition_code] = team_map
            logger.info(f"Competition teams cached: {competition_code} → {len(team_map)} teams")
            return team_map
    except Exception as e:
        logger.error(f"get_competition_teams_map error: {e}")
    return {}

COMPETITIONS = [
    "PL",   # Premier League
    "PD",   # LaLiga
    "BL1",  # Bundesliga
    "SA",   # Serie A
    "FL1",  # Ligue 1
    "CL",   # Champions League
    "EL",   # Europa League
    "EC",   # Euro Championship
    "WC",   # World Cup
    "PPL",  # Primeira Liga
    "DED",  # Eredivisie
    "BSA",  # Brasileirao
    "MLS",  # MLS
]

_fd_key_index = 0


def _load_fd_keys():
    keys = []
    for i in ["1", "2", "3", "4", "5"]:
        k = os.environ.get(f"FOOTBALL_DATA_API_KEY_{i}", "").strip()
        if k:
            keys.append(k)
    fallback = os.environ.get("FOOTBALL_DATA_API_KEY", "").strip()
    if fallback and fallback not in keys:
        keys.append(fallback)
    return keys


def _next_fd_key(keys):
    global _fd_key_index
    key = keys[_fd_key_index % len(keys)]
    _fd_key_index = (_fd_key_index + 1) % len(keys)
    return key


def _get_headers():
    keys = _load_fd_keys()
    if not keys:
        logger.error("No hay ninguna FOOTBALL_DATA_API_KEY configurada.")
        return {"X-Auth-Token": ""}
    return {"X-Auth-Token": _next_fd_key(keys)}


def _request_with_rotation(url, params=None, timeout=15):
    keys = _load_fd_keys()
    if not keys:
        logger.error("No hay FOOTBALL_DATA_API_KEY configurada.")
        return None

    tried = set()
    for _ in range(len(keys)):
        key = _next_fd_key(keys)
        if key in tried:
            continue
        tried.add(key)

        headers = {"X-Auth-Token": key}
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=timeout)
            if resp.status_code == 200:
                return resp
            elif resp.status_code == 429:
                logger.warning(f"football-data 429 (límite), probando siguiente clave...")
                continue
            else:
                logger.warning(f"football-data {resp.status_code}: {resp.text[:200]}")
                return resp
        except requests.exceptions.Timeout:
            logger.warning("football-data timeout, probando siguiente clave...")
            continue
        except Exception as e:
            logger.error(f"football-data request error: {e}")
            return None

    logger.error("Todas las claves de football-data fallaron.")
    return None


ACTIVE_STATUSES = {"SCHEDULED", "TIMED", "IN_PLAY", "PAUSED"}


def get_todays_matches():
    today = date.today().isoformat()
    all_matches = []

    url = f"{BASE_URL}/matches"
    resp = _request_with_rotation(url, timeout=15)
    if resp and resp.status_code == 200:
        all_matches = resp.json().get("matches", [])
        logger.info(f"General endpoint returned {len(all_matches)} matches")
    else:
        logger.warning("No se pudieron obtener los partidos de hoy.")

    today_matches = [m for m in all_matches if m.get("utcDate", "").startswith(today)]
    logger.info(f"Matches for today ({today}): {len(today_matches)}")

    seen = set()
    unique = []
    for m in today_matches:
        status = m.get("status", "")
        if status not in ACTIVE_STATUSES:
            logger.info(f"Omitiendo partido con status '{status}': {m.get('homeTeam',{}).get('name','')} vs {m.get('awayTeam',{}).get('name','')}")
            continue
        mid = m.get("id")
        if mid and mid not in seen:
            home_id = m.get("homeTeam", {}).get("id")
            away_id = m.get("awayTeam", {}).get("id")
            if home_id and away_id:
                seen.add(mid)
                unique.append(m)

    unique.sort(key=lambda m: m.get("utcDate", ""))
    logger.info(f"Total active matches today: {len(unique)}")
    return unique


def get_team_last_matches(team_id, limit=50):
    if not team_id:
        logger.warning("get_team_last_matches called with None team_id")
        return []
    url = f"{BASE_URL}/teams/{team_id}/matches"
    params = {"status": "FINISHED", "limit": limit}
    resp = _request_with_rotation(url, params=params, timeout=15)
    if resp and resp.status_code == 200:
        matches = resp.json().get("matches", [])
        logger.info(f"Team {team_id}: got {len(matches)} finished matches")
        return matches
    return []


def get_head_to_head(match_id, limit=10):
    if not match_id:
        return []
    url = f"{BASE_URL}/matches/{match_id}/head2head"
    params = {"limit": limit}
    resp = _request_with_rotation(url, params=params, timeout=10)
    if resp and resp.status_code == 200:
        return resp.json().get("matches", [])
    return []


def get_standings(competition_code):
    url = f"{BASE_URL}/competitions/{competition_code}/standings"
    resp = _request_with_rotation(url, timeout=10)
    if resp and resp.status_code == 200:
        standings = resp.json().get("standings", [])
        for s in standings:
            if s.get("type") == "TOTAL":
                return s.get("table", [])
    return []


def search_team_by_name(name, sport_key=None):
    if not name:
        return None
    name_lower = name.lower().strip()
    name_words = set(name_lower.split())

    # FIX D: solo usar palabras de 4+ caracteres para evitar falsos positivos
    # con palabras cortas como "FC", "AC", "de", "the", etc.
    sig_name_words = {w for w in name_words if len(w) >= 4}

    def _match_in_map(team_map):
        # 1. Coincidencia exacta del nombre completo
        if name_lower in team_map:
            return team_map[name_lower]

        # 2. Coincidencia por substring (solo si el nombre tiene 5+ caracteres)
        if len(name_lower) >= 5:
            for t_name, tid in team_map.items():
                if name_lower in t_name or t_name in name_lower:
                    return tid

        # 3. Coincidencia por palabras significativas (≥4 caracteres)
        if sig_name_words:
            for t_name, tid in team_map.items():
                t_sig_words = {w for w in t_name.split() if len(w) >= 4}
                if not t_sig_words:
                    continue
                common = sig_name_words & t_sig_words
                # Requiere al menos 1 palabra significativa en común
                # Y que sea >= la mayoría de las palabras significativas del nombre buscado
                if len(common) >= max(1, len(sig_name_words) - 1) and len(common) >= 1:
                    return tid
        return None

    # Primero: buscar por competición (más confiable en el plan gratuito)
    if sport_key and sport_key in SPORT_KEY_TO_FD_CODE:
        comp_code = SPORT_KEY_TO_FD_CODE[sport_key]
        team_map = get_competition_teams_map(comp_code)
        if team_map:
            tid = _match_in_map(team_map)
            if tid:
                logger.info(f"Competition match '{name}' en {comp_code} → id={tid}")
                return tid

    # Fallback: buscar en todas las competiciones cacheadas
    for comp_code in ["PL", "PD", "BL1", "SA", "FL1", "CL", "EL", "PPL", "DED"]:
        if comp_code in _comp_teams_cache:
            tid = _match_in_map(_comp_teams_cache[comp_code])
            if tid:
                logger.info(f"Cache fallback '{name}' en {comp_code} → id={tid}")
                return tid

    # Último recurso: endpoint global /teams
    url = f"{BASE_URL}/teams"
    params = {"name": name, "limit": 10}
    resp = _request_with_rotation(url, params=params, timeout=10)
    if resp and resp.status_code == 200:
        teams = resp.json().get("teams", [])
        for t in teams:
            t_name = t.get("name", "").lower()
            t_short = t.get("shortName", "").lower()

            # Coincidencia exacta primero
            if name_lower == t_name or name_lower == t_short:
                logger.info(f"Global exact match: '{name}' → id={t['id']}")
                return t["id"]

            # Substring solo si el nombre tiene 5+ caracteres
            if len(name_lower) >= 5 and (name_lower in t_name or t_name in name_lower):
                logger.info(f"Global substring match: '{name}' → id={t['id']}")
                return t["id"]

            # Palabras significativas (≥4 chars)
            if sig_name_words:
                t_sig_words = {w for w in t_name.split() if len(w) >= 4}
                common = sig_name_words & t_sig_words
                if t_sig_words and len(common) >= max(1, len(sig_name_words) - 1):
                    logger.info(f"Global word match: '{name}' → id={t['id']}")
                    return t["id"]

    logger.warning(f"Equipo no encontrado en football-data.org: '{name}'")
    return None


def get_team_standing(competition_code, team_id):
    table = get_standings(competition_code)
    total = len(table)
    for row in table:
        if row.get("team", {}).get("id") == team_id:
            return {
                "position": row.get("position"),
                "points": row.get("points"),
                "played": row.get("playedGames"),
                "won": row.get("won"),
                "draw": row.get("draw"),
                "lost": row.get("lost"),
                "goals_for": row.get("goalsFor"),
                "goals_against": row.get("goalsAgainst"),
                "goal_diff": row.get("goalDifference"),
                "total_teams": total,
            }
    return {}
