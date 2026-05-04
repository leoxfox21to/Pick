import os
import json
import time
import logging
import requests
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

BASE_URL = "https://v3.football.api-sports.io"

def _load_keys():
    keys = []
    for i in ["", "_2", "_3", "_4", "_5"]:
        k = os.environ.get(f"APIFOOTBALL_KEY{i}", "").strip()
        if k:
            keys.append(k)
    return keys

_key_index = 0

def _get_headers():
    global _key_index
    keys = _load_keys()
    if not keys:
        return {}
    key = keys[_key_index % len(keys)]
    _key_index = (_key_index + 1) % len(keys)
    return {"x-apisports-key": key}

def _has_key():
    return bool(_load_keys())

SPORT_KEY_TO_LEAGUE = {
    "soccer_epl": 39,
    "soccer_spain_la_liga": 140,
    "soccer_germany_bundesliga": 78,
    "soccer_italy_serie_a": 135,
    "soccer_france_ligue_one": 61,
    "soccer_uefa_champs_league": 2,
    "soccer_uefa_europa_league": 3,
    "soccer_uefa_europa_conference_league": 848,
    "soccer_england_efl_champ": 40,
    "soccer_england_league1": 41,
    "soccer_england_league2": 42,
    "soccer_portugal_primeira_liga": 94,
    "soccer_netherlands_eredivisie": 88,
    "soccer_brazil_campeonato": 71,
    "soccer_usa_mls": 253,
    "soccer_argentina_primera_division": 128,
    "soccer_mexico_ligamx": 262,
    "soccer_turkey_super_league": 203,
    "soccer_chile_campeonato": 265,
    "soccer_colombia_primera_a": 239,
    "soccer_ecuador_liga_pro": 268,
    "soccer_peru_primera_division": 281,
    "soccer_uruguay_primera_division": 278,
    "soccer_venezuela_primera": 257,
    "soccer_australia_aleague": 188,
    "soccer_austria_bundesliga": 218,
    "soccer_belgium_first_div": 144,
    "soccer_denmark_superliga": 119,
    "soccer_greece_super_league": 197,
    "soccer_norway_eliteserien": 103,
    "soccer_poland_ekstraklasa": 106,
    "soccer_scotland_premiership": 179,
    "soccer_spain_segunda_division": 141,
    "soccer_sweden_allsvenskan": 113,
    "soccer_switzerland_superleague": 207,
    "soccer_germany_bundesliga2": 79,
    "soccer_italy_serie_b": 136,
    "soccer_france_ligue_two": 62,
    "soccer_conmebol_copa_libertadores": 13,
    "soccer_conmebol_copa_sudamericana": 11,
    "soccer_saudi_league": 307,
    "soccer_japan_j_league": 98,
    "soccer_korea_kleague1": 292,
    "soccer_russia_premier_league": 235,
}

SOUTHERN_LEAGUES = {71, 128, 265, 239, 268, 281, 278, 257, 188, 13, 11}

_TEAM_CACHE_FILE = "apifootball_team_cache.json"
_fixtures_cache = {}
_standings_cache = {}
_CACHE_TTL = 7200


def _current_season(league_id):
    now = datetime.now(timezone.utc)
    year = now.year
    if league_id in SOUTHERN_LEAGUES:
        return year
    return year if now.month >= 7 else year - 1


def _load_team_cache():
    try:
        if os.path.exists(_TEAM_CACHE_FILE):
            with open(_TEAM_CACHE_FILE, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_team_cache(cache):
    try:
        with open(_TEAM_CACHE_FILE, "w") as f:
            json.dump(cache, f)
    except Exception:
        pass


_team_cache = _load_team_cache()


def _normalize(name):
    import re
    import unicodedata
    name = unicodedata.normalize("NFD", name)
    name = "".join(c for c in name if unicodedata.category(c) != "Mn")
    name = name.lower()
    name = re.sub(r"\b(fc|cf|sc|ac|afc|fk|sk|club|de|del|the|atletico|real|sporting|deportivo)\b", "", name)
    name = re.sub(r"[^a-z0-9 ]", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def _similarity(a, b):
    na, nb = _normalize(a), _normalize(b)
    if na == nb:
        return 1.0
    if na in nb or nb in na:
        return 0.9
    wa = {w for w in na.split() if len(w) > 2}
    wb = {w for w in nb.split() if len(w) > 2}
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / max(len(wa), len(wb))


def search_team(name, league_id=None):
    if not _has_key():
        return None
    cache_key = f"{name}|{league_id}"
    if cache_key in _team_cache:
        logger.info(f"Team cache hit: '{name}' → {_team_cache[cache_key]}")
        return _team_cache[cache_key]

    try:
        params = {"name": name}
        if league_id:
            params["league"] = league_id
            params["season"] = _current_season(league_id)
        resp = requests.get(f"{BASE_URL}/teams", headers=_get_headers(), params=params, timeout=10)
        if resp.status_code == 200:
            teams = resp.json().get("response", [])
            best_id = None
            best_score = 0.0
            for t in teams:
                t_name = t.get("team", {}).get("name", "")
                t_short = t.get("team", {}).get("code", "")
                score = max(_similarity(name, t_name), _similarity(name, t_short))
                if score > best_score:
                    best_score = score
                    best_id = t.get("team", {}).get("id")
            if best_id and best_score >= 0.4:
                logger.info(f"API-Football team: '{name}' → id={best_id} (score={best_score:.2f})")
                _team_cache[cache_key] = best_id
                _save_team_cache(_team_cache)
                return best_id
        else:
            logger.warning(f"API-Football teams search {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        logger.error(f"API-Football search_team error: {e}")
    return None


def get_team_fixtures(team_id, last=15):
    if not _has_key() or not team_id:
        return []
    cache_key = f"fix_{team_id}"
    cached = _fixtures_cache.get(cache_key)
    if cached and (time.time() - cached["ts"]) < _CACHE_TTL:
        return cached["data"]
    try:
        params = {"team": team_id, "last": last, "status": "FT"}
        resp = requests.get(f"{BASE_URL}/fixtures", headers=_get_headers(), params=params, timeout=10)
        if resp.status_code == 200:
            fixtures = resp.json().get("response", [])
            converted = [_convert_fixture(f, team_id) for f in fixtures]
            _fixtures_cache[cache_key] = {"data": converted, "ts": time.time()}
            logger.info(f"API-Football fixtures team={team_id}: {len(fixtures)} partidos")
            return converted
        else:
            logger.warning(f"API-Football fixtures {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        logger.error(f"API-Football fixtures error: {e}")
    return []


def get_h2h(team1_id, team2_id, last=10):
    if not _has_key() or not team1_id or not team2_id:
        return []
    cache_key = f"h2h_{min(team1_id,team2_id)}_{max(team1_id,team2_id)}"
    cached = _fixtures_cache.get(cache_key)
    if cached and (time.time() - cached["ts"]) < _CACHE_TTL:
        return cached["data"]
    try:
        params = {"h2h": f"{team1_id}-{team2_id}", "last": last, "status": "FT"}
        resp = requests.get(f"{BASE_URL}/fixtures/headtohead", headers=_get_headers(), params=params, timeout=10)
        if resp.status_code == 200:
            fixtures = resp.json().get("response", [])
            converted = [_convert_fixture(f, team1_id) for f in fixtures]
            _fixtures_cache[cache_key] = {"data": converted, "ts": time.time()}
            logger.info(f"API-Football H2H {team1_id} vs {team2_id}: {len(fixtures)} partidos")
            return converted
        else:
            logger.warning(f"API-Football H2H {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        logger.error(f"API-Football H2H error: {e}")
    return []


def get_standings(league_id, season=None):
    if not _has_key() or not league_id:
        return []
    if not season:
        season = _current_season(league_id)
    cache_key = f"stand_{league_id}_{season}"
    cached = _standings_cache.get(cache_key)
    if cached and (time.time() - cached["ts"]) < _CACHE_TTL * 3:
        return cached["data"]
    try:
        params = {"league": league_id, "season": season}
        resp = requests.get(f"{BASE_URL}/standings", headers=_get_headers(), params=params, timeout=10)
        if resp.status_code == 200:
            data = resp.json().get("response", [])
            standings = []
            for entry in data:
                for group in entry.get("league", {}).get("standings", []):
                    standings.extend(group)
            _standings_cache[cache_key] = {"data": standings, "ts": time.time()}
            logger.info(f"API-Football standings league={league_id} season={season}: {len(standings)} equipos")
            return standings
        else:
            logger.warning(f"API-Football standings {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        logger.error(f"API-Football standings error: {e}")
    return []


def get_team_standing(league_id, team_id, season=None):
    standings = get_standings(league_id, season)
    for s in standings:
        if s.get("team", {}).get("id") == team_id:
            total = len(standings)
            return {
                "position": s.get("rank"),
                "total_teams": total,
                "points": s.get("points"),
                "played": s.get("all", {}).get("played", 0),
                "wins": s.get("all", {}).get("win", 0),
                "draws": s.get("all", {}).get("draw", 0),
                "losses": s.get("all", {}).get("lose", 0),
                "goals_for": s.get("all", {}).get("goals", {}).get("for", 0),
                "goals_against": s.get("all", {}).get("goals", {}).get("against", 0),
                "goal_diff": s.get("goalsDiff", 0),
                "form": s.get("form", ""),
            }
    return {}


def _convert_fixture(f, perspective_team_id):
    home_id = f.get("teams", {}).get("home", {}).get("id")
    away_id = f.get("teams", {}).get("away", {}).get("id")
    home_goals = f.get("goals", {}).get("home")
    away_goals = f.get("goals", {}).get("away")
    date_str = f.get("fixture", {}).get("date", "")
    ht = f.get("score", {}).get("halftime", {})
    ht_home = ht.get("home")
    ht_away = ht.get("away")
    league = f.get("league", {})
    return {
        "id": f.get("fixture", {}).get("id"),
        "utcDate": date_str,
        "status": "FINISHED",
        "homeTeam": {"id": home_id, "name": f.get("teams", {}).get("home", {}).get("name", "")},
        "awayTeam": {"id": away_id, "name": f.get("teams", {}).get("away", {}).get("name", "")},
        "score": {
            "fullTime": {"home": home_goals, "away": away_goals},
            "halfTime": {"home": ht_home, "away": ht_away},
        },
        "competition": {
            "id": league.get("id"),
            "name": league.get("name", ""),
            "type": league.get("type", ""),
        },
    }


def get_team_season_stats(team_id, league_id, season=None):
    """Estadísticas completas de temporada: goles por mitad, penaltis, tarjetas."""
    if not _has_key() or not team_id or not league_id:
        return {}
    if not season:
        from datetime import date
        season = date.today().year if date.today().month >= 7 else date.today().year - 1
    cache_key = f"seasonstats_{team_id}_{league_id}_{season}"
    cached = _standings_cache.get(cache_key)
    if cached and (time.time() - cached["ts"]) < _CACHE_TTL * 6:
        return cached["data"]
    try:
        params = {"team": team_id, "season": season, "league": league_id}
        resp = requests.get(f"{BASE_URL}/teams/statistics", headers=_get_headers(), params=params, timeout=10)
        if resp.status_code == 200:
            d = resp.json().get("response", {})
            if not d:
                return {}
            played = d.get("fixtures", {}).get("played", {}).get("total") or 1
            gf = d.get("goals", {}).get("for", {})
            ga = d.get("goals", {}).get("against", {})

            def half_sum(gdict, minutes):
                return sum((gdict.get("minute", {}).get(m, {}) or {}).get("total") or 0 for m in minutes)

            first_h = ["0-15", "16-30", "31-45"]
            second_h = ["46-60", "61-75", "76-90", "91-105"]

            g1 = half_sum(gf, first_h)
            g2 = half_sum(gf, second_h)
            c1 = half_sum(ga, first_h)
            c2 = half_sum(ga, second_h)

            pen = d.get("penalty", {})
            cards = d.get("cards", {})
            yellows = sum(
                (cards.get("yellow", {}).get(m, {}) or {}).get("total") or 0
                for m in cards.get("yellow", {})
            )
            reds = sum(
                (cards.get("red", {}).get(m, {}) or {}).get("total") or 0
                for m in cards.get("red", {})
            )
            result = {
                "played": played,
                "avg_1h_scored": round(g1 / played, 2),
                "avg_2h_scored": round(g2 / played, 2),
                "avg_1h_conceded": round(c1 / played, 2),
                "avg_2h_conceded": round(c2 / played, 2),
                "stronger_half": "1T" if g1 >= g2 else "2T",
                "weaker_half_defense": "1T" if c1 >= c2 else "2T",
                "penalties_scored": (pen.get("scored", {}) or {}).get("total") or 0,
                "penalties_missed": (pen.get("missed", {}) or {}).get("total") or 0,
                "yellow_cards": yellows,
                "red_cards": reds,
                "yellow_per_game": round(yellows / played, 2),
                "red_per_game": round(reds / played, 2),
                "win_streak": (d.get("biggest", {}).get("streak", {}) or {}).get("wins") or 0,
                "lose_streak": (d.get("biggest", {}).get("streak", {}) or {}).get("loses") or 0,
                "form": d.get("form", ""),
            }
            _standings_cache[cache_key] = {"data": result, "ts": time.time()}
            logger.info(f"Season stats OK: team={team_id} league={league_id} played={played}")
            return result
        else:
            logger.warning(f"Season stats {team_id}: HTTP {resp.status_code}")
    except Exception as e:
        logger.error(f"get_team_season_stats error: {e}")
    return {}


def get_coach(team_id):
    """Información del entrenador actual del equipo."""
    if not _has_key() or not team_id:
        return {}
    cache_key = f"coach_{team_id}"
    cached = _standings_cache.get(cache_key)
    if cached and (time.time() - cached["ts"]) < _CACHE_TTL * 24:
        return cached["data"]
    try:
        resp = requests.get(f"{BASE_URL}/coachs", headers=_get_headers(), params={"team": team_id}, timeout=8)
        if resp.status_code == 200:
            coaches = resp.json().get("response", [])
            if coaches:
                c = coaches[0]
                result = {
                    "name": c.get("name", "?"),
                    "nationality": c.get("nationality", ""),
                    "age": c.get("age"),
                    "start": "",
                }
                for career in (c.get("career") or []):
                    if career.get("team", {}).get("id") == team_id and not career.get("end"):
                        result["start"] = (career.get("start") or "")[:7]
                        break
                _standings_cache[cache_key] = {"data": result, "ts": time.time()}
                return result
    except Exception as e:
        logger.debug(f"get_coach error team={team_id}: {e}")
    return {}


def get_full_match_data(home_name, away_name, sport_key):
    """
    Retorna (home_fixtures, away_fixtures, h2h_fixtures, home_standing, away_standing, home_team_id, away_team_id)
    usando API-Football. Busca equipos automáticamente por nombre.
    """
    if not _has_key():
        logger.warning("APIFOOTBALL_KEY no configurada")
        return [], [], [], {}, {}, None, None

    league_id = SPORT_KEY_TO_LEAGUE.get(sport_key)

    home_id = search_team(home_name, league_id)
    away_id = search_team(away_name, league_id)

    if not home_id and not away_id:
        logger.warning(f"No se encontraron IDs API-Football: {home_name}, {away_name}")
        return [], [], [], {}, {}, None, None

    home_fix, away_fix, h2h_fix = [], [], []
    home_stand, away_stand = {}, {}

    if home_id:
        home_fix = get_team_fixtures(home_id, last=15)
    if away_id:
        away_fix = get_team_fixtures(away_id, last=15)
    if home_id and away_id:
        h2h_fix = get_h2h(home_id, away_id, last=10)
    if league_id:
        if home_id:
            home_stand = get_team_standing(league_id, home_id)
        if away_id:
            away_stand = get_team_standing(league_id, away_id)

    return home_fix, away_fix, h2h_fix, home_stand, away_stand, home_id, away_id
