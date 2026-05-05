"""
Módulo SofaScore — acceso sin API key.
Provee historial de partidos y H2H para equipos no cubiertos por football-data.org.
"""
import time
import logging
import requests
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

BASE_URL = "https://api.sofascore.com/api/v1"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://www.sofascore.com/",
    "Accept": "application/json",
}

_cache     = {}
CACHE_TTL  = 3600 * 6


def _get(url, params=None, timeout=10):
    try:
        resp = requests.get(url, headers=HEADERS, params=params, timeout=timeout)
        if resp.status_code == 200:
            return resp.json()
        logger.debug(f"SofaScore HTTP {resp.status_code}: {url}")
    except Exception as e:
        logger.debug(f"SofaScore error: {e}")
    return None


def _normalize(name: str) -> str:
    import unicodedata, re
    name = unicodedata.normalize("NFD", name)
    name = "".join(c for c in name if unicodedata.category(c) != "Mn")
    name = name.lower()
    name = re.sub(r"\b(fc|cf|sc|ac|afc|fk|sk|club|de|del|the|real|atletico|sporting|deportivo)\b", "", name)
    name = re.sub(r"[^a-z0-9 ]", " ", name)
    return re.sub(r"\s+", " ", name).strip()


def _sig_words(name: str) -> set:
    return {w for w in _normalize(name).split() if len(w) >= 3}


def _team_score(query: str, candidate: str) -> float:
    if not query or not candidate:
        return 0.0
    nq = _normalize(query)
    nc = _normalize(candidate)
    if nq == nc:
        return 1.0
    if nq in nc or nc in nq:
        return 0.9
    qw = _sig_words(query)
    cw = _sig_words(candidate)
    if not qw or not cw:
        return 0.0
    overlap = len(qw & cw)
    if overlap == 0:
        return 0.0
    return overlap / max(len(qw), len(cw))


def _generate_query_variants(name: str) -> list:
    variants = [name]
    for token in ["FC", "CF", "SC", "AC", "United", "City", "Athletic"]:
        if token.lower() not in name.lower():
            variants.append(f"{name} {token}")
        stripped = name.replace(token, "").strip()
        if stripped and stripped != name:
            variants.append(stripped)
    return list(dict.fromkeys(variants))[:4]


def search_team(name: str) -> int | None:
    cache_key = f"search_{name.lower()}"
    c = _cache.get(cache_key)
    if c and time.time() - c["ts"] < CACHE_TTL:
        return c["v"]

    best_id    = None
    best_score = 0.0

    for variant in _generate_query_variants(name):
        data = _get(f"{BASE_URL}/search/multi-search/{variant}")
        if not data:
            continue
        for r in data.get("results", []):
            if not isinstance(r, dict):
                continue
            if r.get("type") != "team":
                continue
            entity    = r.get("entity", {})
            if not isinstance(entity, dict):
                continue
            candidate = entity.get("name", "")
            short     = entity.get("shortName", "")
            score = max(_team_score(name, candidate), _team_score(name, short))
            if score > best_score:
                best_score = score
                best_id    = entity.get("id")

    if best_score >= 0.5:
        logger.info(f"SofaScore team '{name}' → id={best_id} (score={best_score:.2f})")
    else:
        best_id = None

    _cache[cache_key] = {"v": best_id, "ts": time.time()}
    return best_id


def get_team_events(team_id: int, page: int = 0) -> list:
    key = f"events_{team_id}_{page}"
    c = _cache.get(key)
    if c and time.time() - c["ts"] < CACHE_TTL:
        return c["v"]
    data   = _get(f"{BASE_URL}/team/{team_id}/events/last/{page}")
    events = (data or {}).get("events", []) if isinstance(data, dict) else []
    _cache[key] = {"v": events, "ts": time.time()}
    return events


def _safe_dict(obj) -> dict:
    """Devuelve obj si es dict, si no devuelve {}."""
    return obj if isinstance(obj, dict) else {}


def _convert_event(ev) -> dict:
    """
    Convierte un evento SofaScore al formato estándar del bot.
    Incluye xG (homeScore.expected / awayScore.expected) cuando disponible.
    """
    if not isinstance(ev, dict):
        return {}

    home = _safe_dict(ev.get("homeTeam", {}))
    away = _safe_dict(ev.get("awayTeam", {}))
    hs   = _safe_dict(ev.get("homeScore", {}))
    as_  = _safe_dict(ev.get("awayScore", {}))

    ts = ev.get("startTimestamp")
    date_str = (
        datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if ts else ""
    )
    status = _safe_dict(ev.get("status", {}))
    status_type = status.get("type", "") if isinstance(status, dict) else ""

    xg_home = hs.get("expected")
    xg_away = as_.get("expected")

    tournament = _safe_dict(ev.get("tournament", {}))
    unique_t   = _safe_dict(tournament.get("uniqueTournament", {}))

    return {
        "id":       ev.get("id"),
        "utcDate":  date_str,
        "status":   "FINISHED" if status_type == "finished" else "SCHEDULED",
        "homeTeam": {"id": home.get("id"), "name": home.get("name", "")},
        "awayTeam": {"id": away.get("id"), "name": away.get("name", "")},
        "score": {
            "fullTime": {
                "home": hs.get("current"),
                "away": as_.get("current"),
            },
            "halfTime": {
                "home": hs.get("period1"),
                "away": as_.get("period1"),
            },
            "xg_home": xg_home,
            "xg_away": xg_away,
        },
        "competition": {
            "id":   unique_t.get("id"),
            "name": tournament.get("name", ""),
            "type": "",
        },
        "_source": "sofascore",
    }


def get_team_matches(team_name: str, last: int = 20) -> tuple[int | None, list]:
    """Devuelve (team_id, lista_de_partidos) con historial reciente (5 páginas max)."""
    team_id = search_team(team_name)
    if not team_id:
        logger.debug(f"SofaScore: no encontró equipo '{team_name}'")
        return None, []

    all_finished = []
    for page in range(5):
        events = get_team_events(team_id, page)
        if not events:
            break
        finished = []
        for e in events:
            if not isinstance(e, dict):
                continue
            status = _safe_dict(e.get("status", {}))
            if status.get("type") == "finished":
                converted = _convert_event(e)
                if converted:
                    finished.append(converted)
        all_finished.extend(finished)
        if len(all_finished) >= last:
            break
        if len(events) < 8:
            break

    logger.info(f"SofaScore '{team_name}' (id={team_id}) → {len(all_finished)} partidos")
    return team_id, all_finished[:last]


def get_h2h(team1_id: int, team2_id: int, last: int = 10) -> list:
    """H2H entre dos equipos filtrando el historial de team1."""
    key = f"h2h_{min(team1_id, team2_id)}_{max(team1_id, team2_id)}"
    c = _cache.get(key)
    if c and time.time() - c["ts"] < CACHE_TTL:
        return c["v"]

    events = get_team_events(team1_id, 0)
    h2h    = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        home_team = _safe_dict(ev.get("homeTeam", {}))
        away_team = _safe_dict(ev.get("awayTeam", {}))
        ht = home_team.get("id")
        at = away_team.get("id")
        status = _safe_dict(ev.get("status", {}))
        if team2_id in (ht, at) and status.get("type") == "finished":
            converted = _convert_event(ev)
            if converted:
                h2h.append(converted)
        if len(h2h) >= last:
            break

    if h2h:
        _cache[key] = {"v": h2h, "ts": time.time()}
    return h2h
