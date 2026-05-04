"""
Estadísticas del árbitro designado para el partido.
Fuente: SofaScore (sin API key). Caché local de 24h por árbitro.
"""
import time
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

BASE_URL = "https://api.sofascore.com/api/v1"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://www.sofascore.com/",
    "Accept": "application/json",
}

_cache = {}
CACHE_TTL_REFEREE = 86400   # 24h para nombre de árbitro
CACHE_TTL_STATS   = 86400   # 24h para stats del árbitro


def _get(url, params=None, timeout=10):
    import requests
    try:
        resp = requests.get(url, headers=HEADERS, params=params, timeout=timeout)
        if resp.status_code == 200:
            return resp.json()
        logger.debug(f"Referee _get HTTP {resp.status_code}: {url}")
    except Exception as e:
        logger.debug(f"Referee _get error: {e}")
    return None


def _today_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def get_match_referee(home_name: str, away_name: str) -> dict | None:
    """
    Busca el árbitro designado para un partido buscándolo en los eventos
    programados de SofaScore para hoy.
    Devuelve: {name, nationality, id} o None.
    """
    cache_key = f"ref_match_{home_name.lower()}_{away_name.lower()}"
    c = _cache.get(cache_key)
    if c and time.time() - c["ts"] < CACHE_TTL_REFEREE:
        return c["v"]

    today = _today_str()
    data = _get(f"{BASE_URL}/sport/football/scheduled-events/{today}")
    if not data:
        return None

    from sofascore_api import _team_score

    best_ref = None
    best_score = 0.0

    for ev in data.get("events", []):
        ht = ev.get("homeTeam", {}).get("name", "")
        at = ev.get("awayTeam", {}).get("name", "")
        sh = _team_score(home_name, ht)
        sa = _team_score(away_name, at)
        score = sh * sa
        if score > best_score:
            best_score = score
            if score >= 0.4:
                ref = ev.get("referee", {})
                if ref and ref.get("name"):
                    best_ref = {
                        "name": ref.get("name", ""),
                        "nationality": ref.get("country", {}).get("name", ""),
                        "id": ref.get("id"),
                    }

    _cache[cache_key] = {"v": best_ref, "ts": time.time()}
    if best_ref:
        logger.info(f"Árbitro encontrado para {home_name} vs {away_name}: {best_ref['name']}")
    return best_ref


def get_referee_stats(referee_id: int | None, referee_name: str = "") -> dict | None:
    """
    Calcula estadísticas del árbitro a partir de sus últimos 20 eventos en SofaScore.
    Devuelve: {cards_per_game, penalties_per_game, home_win_rate, sample_size} o None.
    """
    if not referee_id and not referee_name:
        return None

    cache_key = f"ref_stats_{referee_id or referee_name}"
    c = _cache.get(cache_key)
    if c and time.time() - c["ts"] < CACHE_TTL_STATS:
        return c["v"]

    # Obtener eventos recientes del árbitro
    events = []
    if referee_id:
        for page in range(3):
            data = _get(f"{BASE_URL}/referee/{referee_id}/events/last/{page}")
            if not data:
                break
            evs = data.get("events", [])
            events.extend(evs)
            if len(events) >= 20 or len(evs) < 5:
                break

    if not events:
        return None

    total = 0
    yellow_total = 0
    red_total = 0
    penalties = 0
    home_wins = 0

    for ev in events:
        status_type = ev.get("status", {}).get("type", "")
        if status_type != "finished":
            continue

        hs = ev.get("homeScore", {})
        as_ = ev.get("awayScore", {})
        hg = hs.get("current")
        ag = as_.get("current")
        if hg is None or ag is None:
            continue

        total += 1
        if hg > ag:
            home_wins += 1

        # Tarjetas desde statistics si disponibles
        # SofaScore a veces incluye yellowCards en el evento
        y = ev.get("yellowCards", {})
        r = ev.get("redCards", {})
        yellow_total += (y.get("home", 0) or 0) + (y.get("away", 0) or 0)
        red_total    += (r.get("home", 0) or 0) + (r.get("away", 0) or 0)

    if total < 3:
        return None

    stats = {
        "sample_size": total,
        "cards_per_game": round((yellow_total + red_total * 2) / total, 2),
        "yellows_per_game": round(yellow_total / total, 2),
        "reds_per_game": round(red_total / total, 2),
        "home_win_rate": round(home_wins / total * 100, 1),
        "style": _referee_style(yellow_total / total if total else 0),
    }

    _cache[cache_key] = {"v": stats, "ts": time.time()}
    logger.info(f"Stats árbitro {referee_name or referee_id}: {stats}")
    return stats


def _referee_style(yellows_per_game: float) -> str:
    if yellows_per_game >= 5.0:
        return "🟨 Árbitro muy permisivo (muchas tarjetas)"
    if yellows_per_game >= 3.5:
        return "📋 Árbitro estricto"
    if yellows_per_game <= 1.5:
        return "🤝 Árbitro muy permisivo (pocas tarjetas)"
    return "⚖️ Árbitro equilibrado"


def format_referee_for_telegram(ref: dict | None, stats: dict | None) -> str:
    """Bloque de texto para mostrar en el mensaje de Telegram."""
    if not ref:
        return ""
    lines = [f"\n👨‍⚖️ *Árbitro:* {ref['name']}"]
    if ref.get("nationality"):
        lines[0] += f" ({ref['nationality']})"
    if stats:
        lines.append(
            f"  {stats['style']} | 🟨 {stats['yellows_per_game']}/p | "
            f"🏠 Local gana {stats['home_win_rate']}% con este árbitro "
            f"({stats['sample_size']} partidos)"
        )
    return "\n".join(lines)


def format_referee_for_ai(ref: dict | None, stats: dict | None) -> str:
    """Versión compacta para el prompt de la IA."""
    if not ref:
        return "Sin datos de árbitro"
    base = f"{ref['name']}"
    if ref.get("nationality"):
        base += f" ({ref['nationality']})"
    if not stats:
        return base
    return (
        f"{base}\n"
        f"  - Estilo: {stats['style']}\n"
        f"  - 🟨 {stats['yellows_per_game']} amarillas/partido | "
        f"🔴 {stats['reds_per_game']} rojas/partido\n"
        f"  - Local gana {stats['home_win_rate']}% de sus partidos "
        f"(muestra: {stats['sample_size']} partidos)\n"
        f"  - IMPLICACIÓN: "
        + _ref_ai_implication(stats)
    )


def _ref_ai_implication(stats: dict) -> str:
    lines = []
    if stats["yellows_per_game"] >= 4.5:
        lines.append("árbitro permisivo con contacto físico → favorece juego duro")
    elif stats["yellows_per_game"] <= 1.8:
        lines.append("árbitro estricto → penaliza pressing agresivo")
    if stats["home_win_rate"] >= 58:
        lines.append("historial favorable al equipo local")
    elif stats["home_win_rate"] <= 42:
        lines.append("historial desfavorable al equipo local")
    return " | ".join(lines) if lines else "sin tendencia clara"
