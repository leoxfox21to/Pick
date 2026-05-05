import os
import re
import time
import logging
import unicodedata
import requests
from datetime import date, datetime, timedelta, timezone

logger = logging.getLogger(__name__)

BASE_URL = "https://api.the-odds-api.com/v4"

_odds_key_index = 0

def _load_odds_keys():
    keys = []
    for i in ["1", "2", "3", "4"]:
        k = os.environ.get(f"ODDS_API_KEY_{i}", "").strip()
        if k:
            keys.append(k)
    fallback = os.environ.get("ODDS_API_KEY", "").strip()
    if fallback and fallback not in keys:
        keys.append(fallback)
    return keys

def _next_odds_key():
    global _odds_key_index
    keys = _load_odds_keys()
    if not keys:
        return ""
    key = keys[_odds_key_index % len(keys)]
    _odds_key_index = (_odds_key_index + 1) % len(keys)
    return key

# Zona horaria Cuba (UTC-4) — se usa para determinar "hoy" y "mañana"
CUBA_TZ = timezone(timedelta(hours=-4))

SOCCER_SPORTS = [
    "soccer_epl",
    "soccer_spain_la_liga",
    "soccer_germany_bundesliga",
    "soccer_italy_serie_a",
    "soccer_france_ligue_one",
    "soccer_uefa_champs_league",
    "soccer_uefa_europa_league",
    "soccer_uefa_europa_conference_league",
    "soccer_efl_champ",
    "soccer_england_league1",
    "soccer_england_league2",
    "soccer_portugal_primeira_liga",
    "soccer_netherlands_eredivisie",
    "soccer_brazil_campeonato",
    "soccer_brazil_serie_b",
    "soccer_usa_mls",
    "soccer_argentina_primera_division",
    "soccer_mexico_ligamx",
    "soccer_turkey_super_league",
    "soccer_chile_campeonato",
    "soccer_colombia_primera_a",
    "soccer_ecuador_liga_pro",
    "soccer_peru_primera_division",
    "soccer_uruguay_primera_division",
    "soccer_venezuela_primera",
    "soccer_australia_aleague",
    "soccer_austria_bundesliga",
    "soccer_belgium_first_div",
    "soccer_denmark_superliga",
    "soccer_greece_super_league",
    "soccer_norway_eliteserien",
    "soccer_poland_ekstraklasa",
    "soccer_spl",
    "soccer_spain_segunda_division",
    "soccer_sweden_allsvenskan",
    "soccer_sweden_superettan",
    "soccer_switzerland_superleague",
    "soccer_germany_bundesliga2",
    "soccer_germany_liga3",
    "soccer_italy_serie_b",
    "soccer_france_ligue_two",
    "soccer_conmebol_copa_libertadores",
    "soccer_conmebol_copa_sudamericana",
    "soccer_saudi_arabia_pro_league",
    "soccer_japan_j_league",
    "soccer_korea_kleague1",
    "soccer_russia_premier_league",
    "soccer_finland_veikkausliiga",
    "soccer_league_of_ireland",
    "soccer_china_superleague",
    "soccer_fa_cup",
    "soccer_france_coupe_de_france",
    "soccer_italy_coppa_italia",
    "soccer_germany_dfb_pokal",
]

SPORT_DISPLAY_NAMES = {
    "soccer_epl": "Premier League",
    "soccer_spain_la_liga": "LaLiga",
    "soccer_germany_bundesliga": "Bundesliga",
    "soccer_italy_serie_a": "Serie A",
    "soccer_france_ligue_one": "Ligue 1",
    "soccer_uefa_champs_league": "Champions League",
    "soccer_uefa_europa_league": "Europa League",
    "soccer_uefa_europa_conference_league": "Conference League",
    "soccer_efl_champ": "Championship",
    "soccer_england_efl_champ": "Championship",
    "soccer_england_league1": "League One",
    "soccer_england_league2": "League Two",
    "soccer_portugal_primeira_liga": "Primeira Liga",
    "soccer_netherlands_eredivisie": "Eredivisie",
    "soccer_brazil_campeonato": "Brasileirao",
    "soccer_usa_mls": "MLS",
    "soccer_argentina_primera_division": "Primera División ARG",
    "soccer_mexico_ligamx": "Liga MX",
    "soccer_turkey_super_league": "Süper Lig",
    "soccer_chile_campeonato": "Primera División CHI",
    "soccer_colombia_primera_a": "Liga Colombiana",
    "soccer_ecuador_liga_pro": "Liga Pro Ecuador",
    "soccer_peru_primera_division": "Liga 1 Perú",
    "soccer_uruguay_primera_division": "Primera División URU",
    "soccer_venezuela_primera": "Primera División VEN",
    "soccer_australia_aleague": "A-League",
    "soccer_austria_bundesliga": "Bundesliga AUT",
    "soccer_belgium_first_div": "Pro League BEL",
    "soccer_denmark_superliga": "Superliga DEN",
    "soccer_greece_super_league": "Super League GRE",
    "soccer_norway_eliteserien": "Eliteserien",
    "soccer_poland_ekstraklasa": "Ekstraklasa",
    "soccer_spl": "Premiership SCO",
    "soccer_scotland_premiership": "Premiership SCO",
    "soccer_spain_segunda_division": "Segunda División",
    "soccer_sweden_allsvenskan": "Allsvenskan",
    "soccer_sweden_superettan": "Superettan SWE",
    "soccer_switzerland_superleague": "Super League SUI",
    "soccer_germany_bundesliga2": "2. Bundesliga",
    "soccer_germany_liga3": "3. Liga GER",
    "soccer_italy_serie_b": "Serie B",
    "soccer_france_ligue_two": "Ligue 2",
    "soccer_conmebol_copa_libertadores": "Copa Libertadores",
    "soccer_conmebol_copa_sudamericana": "Copa Sudamericana",
    "soccer_saudi_arabia_pro_league": "Saudi Pro League",
    "soccer_saudi_league": "Saudi Pro League",
    "soccer_japan_j_league": "J1 League",
    "soccer_korea_kleague1": "K League 1",
    "soccer_russia_premier_league": "Premier League RUS",
    "soccer_finland_veikkausliiga": "Veikkausliiga FIN",
    "soccer_league_of_ireland": "League of Ireland",
    "soccer_china_superleague": "Super League CHN",
    "soccer_brazil_serie_b": "Série B BRA",
    "soccer_fa_cup": "FA Cup",
    "soccer_france_coupe_de_france": "Coupe de France",
    "soccer_italy_coppa_italia": "Coppa Italia",
    "soccer_germany_dfb_pokal": "DFB-Pokal",
}

_odds_cache = {"data": None, "ts": 0}
_CACHE_TTL = 3600
_events_cache = {}      # key: offset_days → {"data": [...], "ts": float}


# ── Utilidades de fecha en hora Cuba ─────────────────────────────────────────

def _cuba_day_bounds_utc(offset_days: int = 0):
    """
    Devuelve (start_utc, end_utc) del día Cuba con desplazamiento `offset_days`.
    Cuba es UTC-4, por lo que medianoche Cuba = 04:00 UTC.
    """
    now_cuba = datetime.now(CUBA_TZ)
    cuba_date = now_cuba.date() + timedelta(days=offset_days)
    # Medianoche Cuba en UTC = 04:00 AM UTC del mismo día del calendario
    start_utc = datetime(cuba_date.year, cuba_date.month, cuba_date.day,
                         4, 0, 0, tzinfo=timezone.utc)
    end_utc = start_utc + timedelta(days=1)
    return start_utc, end_utc


def _is_on_cuba_day(utc_str: str, offset_days: int = 0) -> bool:
    """
    Devuelve True si el evento cae en el día Cuba desplazado `offset_days`.
    offset_days=0 → hoy en Cuba, offset_days=1 → mañana en Cuba.
    """
    if not utc_str:
        return False
    try:
        dt = datetime.fromisoformat(utc_str.replace("Z", "+00:00"))
        cuba_date = dt.astimezone(CUBA_TZ).date()
        target = datetime.now(CUBA_TZ).date() + timedelta(days=offset_days)
        return cuba_date == target
    except Exception:
        return False


def _get_event_status(commence_time_str):
    if not commence_time_str:
        return "TIMED"
    try:
        dt = datetime.fromisoformat(commence_time_str.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        if dt > now + timedelta(minutes=5):
            return "TIMED"
        return "IN_PLAY"
    except Exception:
        return "TIMED"


# ── Función genérica para obtener eventos de un día ───────────────────────────

def _get_events_for_day(offset_days: int = 0) -> list:
    """
    Obtiene partidos de fútbol para el día Cuba + offset_days.
    offset_days=0 → hoy, offset_days=1 → mañana.
    Usa un caché independiente por día.
    """
    now = time.time()
    cached = _events_cache.get(offset_days)
    if cached and (now - cached["ts"]) < _CACHE_TTL:
        logger.info(f"Usando caché de eventos (día+{offset_days}): {len(cached['data'])} partidos")
        return cached["data"]

    start_utc, end_utc = _cuba_day_bounds_utc(offset_days)
    start_str = start_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    end_str   = end_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

    all_events = []

    for sport_key in SOCCER_SPORTS:
        try:
            url = f"{BASE_URL}/sports/{sport_key}/events"
            params = {
                "apiKey": _next_odds_key(),
                "dateFormat": "iso",
                "commenceTimeFrom": start_str,
                "commenceTimeTo": end_str,
            }
            resp = requests.get(url, params=params, timeout=10)
            if resp.status_code == 200:
                events = resp.json()
                for ev in events:
                    if not _is_on_cuba_day(ev.get("commence_time", ""), offset_days):
                        continue
                    status = _get_event_status(ev.get("commence_time", ""))
                    comp_name = SPORT_DISPLAY_NAMES.get(sport_key, ev.get("sport_title", sport_key))
                    all_events.append({
                        "id": f"odds_{ev['id']}",
                        "homeTeam": {
                            "name": ev.get("home_team", "Local"),
                            "shortName": ev.get("home_team", "Local"),
                            "id": None,
                        },
                        "awayTeam": {
                            "name": ev.get("away_team", "Visitante"),
                            "shortName": ev.get("away_team", "Visitante"),
                            "id": None,
                        },
                        "competition": {"name": comp_name, "code": sport_key},
                        "utcDate": ev.get("commence_time", ""),
                        "status": status,
                        "_source": "odds",
                        "_sport_key": sport_key,
                        "_event_id": ev["id"],
                    })
            elif resp.status_code == 422:
                pass
            elif resp.status_code == 401:
                logger.warning(f"ODDS_API_KEY inválida para {sport_key}, rotando...")
            else:
                logger.debug(f"odds events {sport_key}: {resp.status_code}")
        except Exception as e:
            logger.debug(f"Error events {sport_key}: {e}")
            continue

    all_events.sort(key=lambda m: m.get("utcDate", ""))
    _events_cache[offset_days] = {"data": all_events, "ts": now}
    label = "hoy" if offset_days == 0 else f"día+{offset_days}"
    logger.info(f"Eventos {label} desde The Odds API: {len(all_events)}")
    return all_events


def get_todays_events() -> list:
    """Partidos que juegan HOY en hora Cuba."""
    return _get_events_for_day(offset_days=0)


def get_tomorrows_events() -> list:
    """Partidos que juegan MAÑANA en hora Cuba."""
    return _get_events_for_day(offset_days=1)


def get_all_odds():
    now = time.time()
    if _odds_cache["data"] and (now - _odds_cache["ts"]) < _CACHE_TTL:
        logger.info(f"Usando caché de cuotas ({len(_odds_cache['data'])} eventos)")
        return _odds_cache["data"]

    all_odds = []
    for sport in SOCCER_SPORTS[:20]:
        try:
            url = f"{BASE_URL}/sports/{sport}/odds"
            params = {
                "apiKey": _next_odds_key(),
                "regions": "eu,uk",
                "markets": "h2h,totals,btts",
                "oddsFormat": "decimal",
                "dateFormat": "iso",
            }
            resp = requests.get(url, params=params, timeout=10)
            if resp.status_code == 200:
                games = resp.json()
                all_odds.extend(games)
            elif resp.status_code == 401:
                logger.warning("ODDS_API_KEY inválida, rotando a siguiente...")
        except Exception:
            continue

    _odds_cache["data"] = all_odds
    _odds_cache["ts"] = now
    return all_odds


def get_odds_for_match_on_demand(sport_key, home_team, away_team):
    try:
        url = f"{BASE_URL}/sports/{sport_key}/odds"
        params = {
            "apiKey": _next_odds_key(),
            "regions": "eu,uk",
            "markets": "h2h,totals,btts",
            "oddsFormat": "decimal",
            "dateFormat": "iso",
        }
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code == 200:
            games = resp.json()
            return find_odds_for_match(home_team, away_team, games)
    except Exception as e:
        logger.warning(f"Error obteniendo cuotas on-demand para {sport_key}: {e}")
    return None


def get_scores_for_sport(sport_key, days_from=1):
    try:
        url = f"{BASE_URL}/sports/{sport_key}/scores"
        params = {
            "apiKey": _next_odds_key(),
            "daysFrom": days_from,
            "dateFormat": "iso",
        }
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        logger.debug(f"Error scores {sport_key}: {e}")
    return []


_scores_cache = {}
_SCORES_TTL = 1800


def get_team_form_from_scores(sport_key, home_name, away_name):
    """
    Busca resultados recientes en The Odds API para ambos equipos.
    Usa daysFrom=14 para capturar al menos 2-3 partidos recientes en ligas
    que juegan cada semana (K League, J League, MLS, etc.)
    """
    now = time.time()
    cache_key = sport_key
    cached = _scores_cache.get(cache_key)
    if cached and (now - cached["ts"]) < _SCORES_TTL:
        scores = cached["data"]
    else:
        # FIX: daysFrom=14 en lugar de 3 para cubrir ligas semanales
        scores = get_scores_for_sport(sport_key, days_from=14)
        _scores_cache[cache_key] = {"data": scores, "ts": now}

    def build_stats(team_name):
        results = []
        goals_scored = []
        goals_conceded = []
        last_date = None
        for match in scores:
            completed = match.get("completed", False)
            if not completed:
                continue
            h = match.get("home_team", "")
            a = match.get("away_team", "")
            sc = match.get("scores")
            if not sc:
                continue
            sh = _name_similarity(team_name, h)
            sa = _name_similarity(team_name, a)
            is_home = sh >= 0.5
            is_away = sa >= 0.5
            if not is_home and not is_away:
                continue
            home_sc = next((s.get("score") for s in sc if s.get("name") == "home"), None)
            away_sc = next((s.get("score") for s in sc if s.get("name") == "away"), None)
            if home_sc is None or away_sc is None:
                try:
                    home_sc = int(sc[0].get("score", 0)) if sc else 0
                    away_sc = int(sc[1].get("score", 0)) if len(sc) > 1 else 0
                except Exception:
                    continue
            try:
                home_sc = int(home_sc)
                away_sc = int(away_sc)
            except Exception:
                continue

            gf = home_sc if is_home else away_sc
            ga = away_sc if is_home else home_sc
            goals_scored.append(gf)
            goals_conceded.append(ga)
            if gf > ga:
                results.append("W")
            elif gf == ga:
                results.append("D")
            else:
                results.append("L")

            dt_str = match.get("commence_time", "")
            if dt_str and last_date is None:
                try:
                    last_date = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
                except Exception:
                    pass

        days_rest = None
        if last_date:
            days_rest = (datetime.now(timezone.utc) - last_date).days

        total = len(results)
        if total == 0:
            return {}, None

        avg_s = sum(goals_scored) / total
        avg_c = sum(goals_conceded) / total
        form_str = "".join(results[:5]) if results else "N/A"
        form_pts = sum(3 if r == "W" else 1 if r == "D" else 0 for r in results[:5])
        elo = 1500
        for r in reversed(results):
            exp = 1 / (1 + 10 ** ((1500 - elo) / 400))
            act = 1 if r == "W" else 0.5 if r == "D" else 0
            elo += 32 * (act - exp)

        return {
            "total_matches": total,
            "wins": results.count("W"),
            "draws": results.count("D"),
            "losses": results.count("L"),
            "win_rate": results.count("W") / total,
            "avg_scored": round(avg_s, 2),
            "avg_conceded": round(avg_c, 2),
            "avg_home_scored": round(avg_s, 2),
            "avg_home_conceded": round(avg_c, 2),
            "avg_away_scored": round(avg_s, 2),
            "avg_away_conceded": round(avg_c, 2),
            "home_win_rate": results.count("W") / total,
            "away_win_rate": results.count("W") / total,
            "home_form_5": form_str,
            "home_form_pts": form_pts,
            "away_form_5": form_str,
            "away_form_pts": form_pts,
            "clean_sheets_rate": sum(1 for g in goals_conceded if g == 0) / total,
            "failed_to_score_rate": sum(1 for g in goals_scored if g == 0) / total,
            "form_5": form_str,
            "form_points_5": form_pts,
            "btts_rate": sum(1 for gf, ga in zip(goals_scored, goals_conceded) if gf > 0 and ga > 0) / total,
            "over25_rate": sum(1 for gf, ga in zip(goals_scored, goals_conceded) if gf + ga > 2.5) / total,
            "elo": round(elo, 1),
            "results": results,
            "_source": "scores_api",
        }, days_rest

    home_stats_lite, home_rest = build_stats(home_name)
    away_stats_lite, away_rest = build_stats(away_name)
    return home_stats_lite, away_stats_lite, home_rest, away_rest


def _normalize_name(name):
    name = unicodedata.normalize("NFD", name)
    name = "".join(c for c in name if unicodedata.category(c) != "Mn")
    name = name.lower()
    name = re.sub(
        r"\b(fc|cf|sc|ac|afc|bsc|fk|sk|club|de|del|the|united|city|athletic|atletico|real|"
        r"sporting|deportivo|atletismo|hyundai|motors|hotspur)\b",
        "", name
    )
    name = re.sub(r"[^a-z0-9 ]", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def _name_similarity(a, b):
    na = _normalize_name(a)
    nb = _normalize_name(b)
    if na == nb:
        return 1.0
    if na in nb or nb in na:
        return 0.9
    wa = set(na.split())
    wb = set(nb.split())
    wa = {w for w in wa if len(w) > 2}
    wb = {w for w in wb if len(w) > 2}
    if not wa or not wb:
        return 0.0
    overlap = len(wa & wb)
    return overlap / max(len(wa), len(wb))


def get_odds_by_event_id(sport_key, event_id):
    """Obtiene cuotas directamente por ID de evento — método más confiable."""
    keys = _load_odds_keys()
    if not keys or not event_id or not sport_key:
        return None
    try:
        url = f"{BASE_URL}/sports/{sport_key}/events/{event_id}/odds"
        params = {
            "apiKey": _next_odds_key(),
            "regions": "eu,uk",
            "markets": "h2h,totals,btts",
            "oddsFormat": "decimal",
            "dateFormat": "iso",
        }
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            logger.info(f"Cuotas por event_id OK: {event_id}")
            return parse_odds(data)
        else:
            logger.warning(f"get_odds_by_event_id {event_id}: status {resp.status_code}")
    except Exception as e:
        logger.error(f"get_odds_by_event_id error: {e}")
    return None


def find_odds_for_match(home_name, away_name, all_odds):
    best_game = None
    best_score = 0.0
    THRESHOLD = 0.5

    for game in all_odds:
        h = game.get("home_team", "")
        a = game.get("away_team", "")
        sh = _name_similarity(home_name, h)
        sa = _name_similarity(away_name, a)
        score = sh * sa
        if sh >= THRESHOLD and sa >= THRESHOLD and score > best_score:
            best_score = score
            best_game = game
        sh2 = _name_similarity(home_name, a)
        sa2 = _name_similarity(away_name, h)
        score2 = sh2 * sa2
        if sh2 >= THRESHOLD and sa2 >= THRESHOLD and score2 > best_score:
            best_score = score2
            best_game = game

    if best_game:
        logger.info(f"Cuotas encontradas: '{home_name}' vs '{away_name}' → score={best_score:.2f}")
        return parse_odds(best_game)
    logger.warning(f"Cuotas no encontradas: '{home_name}' vs '{away_name}'")
    return None


def parse_odds(game):
    result = {
        "home_win": None,
        "draw": None,
        "away_win": None,
        "over_25": None,
        "under_25": None,
        "btts_yes": None,
        "btts_no": None,
        "bookmaker": None,
    }

    bookmakers = game.get("bookmakers", [])
    if not bookmakers:
        return result

    bm = bookmakers[0]
    result["bookmaker"] = bm.get("title", "")

    for market in bm.get("markets", []):
        key = market.get("key", "")
        outcomes = market.get("outcomes", [])

        if key == "h2h":
            for o in outcomes:
                name = o.get("name", "").lower()
                price = o.get("price")
                home = game.get("home_team", "").lower()
                away = game.get("away_team", "").lower()
                if name in home or home.split()[0] in name:
                    result["home_win"] = price
                elif name == "draw":
                    result["draw"] = price
                elif name in away or away.split()[0] in name:
                    result["away_win"] = price

        elif key == "totals":
            for o in outcomes:
                name = o.get("name", "").lower()
                point = o.get("point", 0)
                price = o.get("price")
                if abs(point - 2.5) < 0.1:
                    if name == "over":
                        result["over_25"] = price
                    elif name == "under":
                        result["under_25"] = price

        elif key in ("btts", "both_teams_to_score"):
            for o in outcomes:
                name = o.get("name", "").lower()
                price = o.get("price")
                if name == "yes":
                    result["btts_yes"] = price
                elif name == "no":
                    result["btts_no"] = price

    return result
