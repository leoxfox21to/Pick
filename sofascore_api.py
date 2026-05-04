import requests
import logging
import time
import unicodedata
import re
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

BASE_URL = "https://api.sofascore.com/api/v1"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://www.sofascore.com/",
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
}

_cache = {}
CACHE_TTL      = 3600
CACHE_TTL_TEAM = 86400


def _get(url, params=None, timeout=10):
    try:
        resp = requests.get(url, headers=HEADERS, params=params, timeout=timeout)
        if resp.status_code == 200:
            return resp.json()
        logger.debug(f"SofaScore HTTP {resp.status_code}: {url}")
    except Exception as e:
        logger.debug(f"SofaScore request error {url}: {e}")
    return None


def _normalize(name: str) -> str:
    """Normaliza nombre: sin tildes, minúsculas, sin palabras genéricas."""
    name = unicodedata.normalize("NFD", name)
    name = "".join(c for c in name if unicodedata.category(c) != "Mn")
    name = name.lower()
    name = re.sub(
        r"\b(fc|cf|sc|ac|afc|bsc|fk|sk|club|de|del|the|united|city|athletic|atletico|"
        r"real|sporting|deportivo|hyundai|motors|hotspur|association|football|soccer|"
        r"calcio|futbol|sport|esporte|espor)\b",
        "", name
    )
    name = re.sub(r"[^a-z0-9 ]", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def _sig_words(name: str) -> set:
    return {w for w in _normalize(name).split() if len(w) >= 3}


def _team_score(query: str, candidate: str) -> float:
    """Score de similitud entre 0.0 y 1.0."""
    qn = _normalize(query)
    cn = _normalize(candidate)
    if qn == cn:
        return 1.0
    if qn in cn or cn in qn:
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
    """Genera variantes del nombre para buscar (completo, primera palabra, sin sufijos)."""
    variants = [name]
    words = name.split()
    if len(words) >= 2:
        variants.append(words[0])
    if len(words) >= 3:
        variants.append(" ".join(words[:2]))
    clean = re.sub(
        r"\b(FC|CF|SC|AC|AFC|BSC|FK|SK|Club|United|City|Athletic|Atletico|Real|Sporting|"
        r"Hyundai|Motors|Hotspur|Deportivo|Calcio)\b",
        "", name, flags=re.IGNORECASE
    ).strip()
    if clean and clean != name and clean not in variants:
        variants.append(clean)
    return variants


def search_team(name: str) -> int | None:
    """Busca un equipo en SofaScore probando múltiples variantes del nombre."""
    cache_key = f"team_{name.lower().strip()}"
    c = _cache.get(cache_key)
    if c and time.time() - c["ts"] < CACHE_TTL_TEAM:
        return c["v"]

    variants  = _generate_query_variants(name)
    best_id   = None
    best_score = 0.0
    MIN_SCORE = 0.45

    for query in variants:
        data = _get(f"{BASE_URL}/search/all", params={"q": query, "sport": "football"})
        if not data:
            continue
        for r in data.get("results", []):
            if r.get("type") != "team":
                continue
            entity    = r.get("entity", {})
            candidate = entity.get("name", "")
            score     = _team_score(name, candidate)
            short     = entity.get("shortName", "")
            if short:
                score = max(score, _team_score(name, short))
            if score > best_score:
                best_score = score
                best_id    = entity.get("id")
        if best_score >= 0.9:
            break

    if best_id and best_score >= MIN_SCORE:
        _cache[cache_key] = {"v": best_id, "ts": time.time()}
        logger.info(f"SofaScore team '{name}' → ID {best_id} (score={best_score:.2f})")
        return best_id

    logger.debug(f"SofaScore: no encontró equipo '{name}' (mejor score={best_score:.2f})")
    return None


def get_team_events(team_id: int, page: int = 0) -> list:
    key = f"events_{team_id}_{page}"
    c = _cache.get(key)
    if c and time.time() - c["ts"] < CACHE_TTL:
        return c["v"]
    data   = _get(f"{BASE_URL}/team/{team_id}/events/last/{page}")
    events = (data or {}).get("events", [])
    if events:
        _cache[key] = {"v": events, "ts": time.time()}
    return events


def _convert_event(ev) -> dict:
    """
    Convierte un evento SofaScore al formato estándar del bot.
    Incluye xG (homeScore.expected / awayScore.expected) cuando disponible.
    """
    home = ev.get("homeTeam", {})
    away = ev.get("awayTeam", {})
    hs   = ev.get("homeScore", {})
    as_  = ev.get("awayScore", {})

    ts = ev.get("startTimestamp")
    date_str = (
        datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if ts else ""
    )
    status_type = ev.get("status", {}).get("type", "")

    # xG: SofaScore lo almacena en homeScore.expected / awayScore.expected
    xg_home = hs.get("expected")
    xg_away = as_.get("expected")

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
            # xG almacenado para uso en get_xg_from_matches()
            "xg_home": xg_home,
            "xg_away": xg_away,
        },
        "competition": {
            "id":   ev.get("tournament", {}).get("uniqueTournament", {}).get("id"),
            "name": ev.get("tournament", {}).get("name", ""),
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
        finished = [
            _convert_event(e)
            for e in events
            if e.get("status", {}).get("type") == "finished"
        ]
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
        ht = ev.get("homeTeam", {}).get("id")
        at = ev.get("awayTeam", {}).get("id")
        if team2_id in (ht, at) and ev.get("status", {}).get("type") == "finished":
            h2h.append(_convert_event(ev))
        if len(h2h) >= last:
            break

    if h2h:
        _cache[key] = {"v": h2h, "ts": time.time()}
    return h2h
