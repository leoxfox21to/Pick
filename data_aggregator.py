"""
data_aggregator.py
Agrega Odds API Scores como 4ta fuente de datos historicos.

Prioridad:
  1. football-data.org  (manejado en main.py)
  2. API-Football        (manejado en main.py)
  3. SofaScore          (cobertura mundial, sin key)
  4. ESPN               (cobertura mundial, sin key)
  5. Odds API Scores    (NUEVO — historial desde scores recientes)
  6. Cache local DB     (acumulacion propia del bot)
"""

import asyncio
import logging

logger = logging.getLogger(__name__)

MIN_MATCHES_THRESHOLD = 5


async def _get_odds_scores_matches(sport_key: str, team_name: str, last: int) -> list:
    if not sport_key:
        return []
    try:
        from odds_api import get_match_history_from_scores
        return await asyncio.to_thread(get_match_history_from_scores, sport_key, team_name, last)
    except Exception as e:
        logger.debug(f"Odds scores fallback error para '{team_name}': {e}")
        return []


async def get_extended_match_data(
    home_name: str,
    away_name: str,
    sport_key: str = "",
    last: int = 20,
) -> dict:
    """
    Consulta SofaScore + ESPN + Odds Scores + Cache local en paralelo.
    Devuelve el mejor historial disponible para home y away.
    """
    from sofascore_api import get_team_matches as ss_get, get_h2h as ss_h2h
    from espn_api import get_team_matches as espn_get
    from db import get_team_matches_from_cache

    (
        ss_home, ss_away,
        espn_home, espn_away,
        cache_home, cache_away,
        odds_home, odds_away,
    ) = await asyncio.gather(
        asyncio.to_thread(ss_get, home_name, last),
        asyncio.to_thread(ss_get, away_name, last),
        asyncio.to_thread(espn_get, home_name, sport_key, last),
        asyncio.to_thread(espn_get, away_name, sport_key, last),
        asyncio.to_thread(get_team_matches_from_cache, home_name, last),
        asyncio.to_thread(get_team_matches_from_cache, away_name, last),
        _get_odds_scores_matches(sport_key, home_name, last),
        _get_odds_scores_matches(sport_key, away_name, last),
    )

    ss_home_id, ss_home_matches = ss_home
    ss_away_id, ss_away_matches = ss_away
    espn_home_id, espn_home_matches = espn_home
    espn_away_id, espn_away_matches = espn_away

    def best(ss_id, ss_m, espn_id, espn_m, cache_m, odds_m):
        candidates = [
            ("sofascore",   ss_id,   ss_m),
            ("espn",        espn_id, espn_m),
            ("odds_scores", None,    odds_m),
            ("local_cache", None,    cache_m),
        ]
        for src, sid, matches in candidates:
            if len(matches) >= MIN_MATCHES_THRESHOLD:
                return sid, matches, src
        best_entry = max(candidates, key=lambda x: len(x[2]))
        return best_entry[1], best_entry[2], best_entry[0]

    home_id, home_matches, home_src = best(
        ss_home_id, ss_home_matches,
        espn_home_id, espn_home_matches,
        cache_home, odds_home,
    )
    away_id, away_matches, away_src = best(
        ss_away_id, ss_away_matches,
        espn_away_id, espn_away_matches,
        cache_away, odds_away,
    )

    h2h = []
    if ss_home_id and ss_away_id:
        try:
            h2h = await asyncio.to_thread(ss_h2h, ss_home_id, ss_away_id, 10)
        except Exception as e:
            logger.debug(f"H2H SofaScore error: {e}")

    source = home_src if len(home_matches) >= len(away_matches) else away_src

    logger.info(
        f"Aggregator: {home_name}={len(home_matches)}({home_src}) | "
        f"{away_name}={len(away_matches)}({away_src}) | h2h={len(h2h)}"
    )

    return {
        "home_matches": home_matches,
        "away_matches": away_matches,
        "h2h": h2h,
        "home_id": home_id,
        "away_id": away_id,
        "source": source,
        "home_source": home_src,
        "away_source": away_src,
    }
