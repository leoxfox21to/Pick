"""
Agregador de datos de fútbol.
Consulta TODAS las fuentes en paralelo y devuelve el mejor historial disponible.

Prioridad:
  1. football-data.org  (ya manejado en main.py antes de llamar aquí)
  2. API-Football        (ya manejado en main.py antes de llamar aquí)
  3. SofaScore          (nueva — cobertura mundial, sin key)
  4. ESPN               (nueva — cobertura mundial, sin key)
  5. Cache local DB     (acumulación propia del bot)
"""

import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)

MIN_MATCHES_THRESHOLD = 5   # mínimo para considerar una fuente "suficiente"


async def get_extended_match_data(
    home_name: str,
    away_name: str,
    sport_key: str = "",
    last: int = 20,
) -> dict:
    """
    Consulta SofaScore + ESPN + cache local en paralelo.
    Devuelve el mejor historial disponible para home y away.

    Retorna:
    {
        "home_matches": [...],
        "away_matches": [...],
        "h2h": [...],
        "home_id": int|None,   (SofaScore/ESPN ID)
        "away_id": int|None,
        "source": "sofascore"|"espn"|"local_cache"|"none",
    }
    """
    from sofascore_api import get_team_matches as ss_get, get_h2h as ss_h2h
    from espn_api import get_team_matches as espn_get
    from db import get_team_matches_from_cache

    # Consultar todas las fuentes en paralelo
    ss_home, ss_away, espn_home, espn_away, cache_home, cache_away = await asyncio.gather(
        asyncio.to_thread(ss_get, home_name, last),
        asyncio.to_thread(ss_get, away_name, last),
        asyncio.to_thread(espn_get, home_name, sport_key, last),
        asyncio.to_thread(espn_get, away_name, sport_key, last),
        asyncio.to_thread(get_team_matches_from_cache, home_name, last),
        asyncio.to_thread(get_team_matches_from_cache, away_name, last),
    )

    ss_home_id, ss_home_matches = ss_home
    ss_away_id, ss_away_matches = ss_away
    espn_home_id, espn_home_matches = espn_home
    espn_away_id, espn_away_matches = espn_away

    # Elegir la mejor fuente para CADA equipo
    def best(ss_id, ss_matches, espn_id, espn_matches, cache_matches):
        if len(ss_matches) >= MIN_MATCHES_THRESHOLD:
            return ss_id, ss_matches, "sofascore"
        if len(espn_matches) >= MIN_MATCHES_THRESHOLD:
            return espn_id, espn_matches, "espn"
        if len(cache_matches) >= MIN_MATCHES_THRESHOLD:
            return None, cache_matches, "local_cache"
        # Tomar la que más tenga aunque no llegue al umbral
        combined = max(
            [("sofascore", ss_id, ss_matches), ("espn", espn_id, espn_matches), ("local_cache", None, cache_matches)],
            key=lambda x: len(x[2])
        )
        return combined[1], combined[2], combined[0]

    home_id, home_matches, home_src = best(ss_home_id, ss_home_matches, espn_home_id, espn_home_matches, cache_home)
    away_id, away_matches, away_src = best(ss_away_id, ss_away_matches, espn_away_id, espn_away_matches, cache_away)

    # H2H desde SofaScore si tenemos ambos IDs
    h2h = []
    if ss_home_id and ss_away_id:
        try:
            h2h = await asyncio.to_thread(ss_h2h, ss_home_id, ss_away_id, 10)
        except Exception as e:
            logger.debug(f"H2H SofaScore error: {e}")

    source = home_src if len(home_matches) >= len(away_matches) else away_src

    logger.info(
        f"Aggregator result: {home_name}={len(home_matches)}({home_src}) "
        f"{away_name}={len(away_matches)}({away_src}) h2h={len(h2h)}"
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
