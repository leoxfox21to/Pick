"""
match_api.py — API propia del bot de picks deportivos.

Endpoints:
  GET  /health                          → estado del servidor
  GET  /matches/today                   → partidos del día con cuotas
  GET  /match/data?home=X&away=Y&sport_key=Z  → todos los datos de un partido
  GET  /match/pick?home=X&away=Y&sport_key=Z  → análisis completo con IA

Uso en Termux:
  python3 match_api.py            (puerto 5000 por defecto)
  PORT=8080 python3 match_api.py  (otro puerto)

Para Railway: se detecta automáticamente la variable PORT de Railway.
"""

import os
import asyncio
import logging
from datetime import datetime, timezone, timedelta

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from flask import Flask, jsonify, request, abort

# ── Módulos del bot ────────────────────────────────────────────────────────────
from football_api import (
    get_team_last_matches, get_head_to_head,
    get_team_standing, search_team_by_name,
)
from odds_api import (
    get_all_odds, find_odds_for_match,
    get_todays_events, get_odds_for_match_on_demand,
    get_team_form_from_scores,
)
from odds_tracker import save_odds_snapshot, get_odds_movement
from analyzer import (
    extract_team_stats, poisson_prediction, h2h_stats,
    calculate_value_bet, calculate_streak, days_since_last_match,
    calculate_confidence_score, halftime_stats, day_of_week_stats,
    night_vs_day_stats, post_cup_fatigue, half_season_stats,
    LEAGUE_AVG_GOALS,
)
from injuries_api import get_team_injuries
from ai_pick import generate_pick
from apifootball import (
    get_full_match_data as apifb_get_full_match_data,
    get_team_season_stats as apifb_season_stats,
    get_coach as apifb_get_coach,
    SPORT_KEY_TO_LEAGUE,
)
from weather import get_weather_for_team, format_weather
from db import (
    init_db, save_pick, parse_ai_pick,
    get_team_matches_from_cache, save_match_to_cache,
)
from data_aggregator import get_extended_match_data

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── Flask app ──────────────────────────────────────────────────────────────────
app = Flask(__name__)
init_db()

# ── Helper: correr async desde Flask (sync) ────────────────────────────────────
def _run(coro):
    """Ejecuta una corrutina asyncio desde un contexto síncrono de Flask."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, coro)
                return future.result()
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


# ── /health ────────────────────────────────────────────────────────────────────
@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "time_utc": datetime.now(timezone.utc).isoformat(),
    })


# ── /matches/today ─────────────────────────────────────────────────────────────
@app.route("/matches/today")
def matches_today():
    """
    Devuelve los partidos del día con cuotas básicas.
    Query params opcionales:
      with_odds=1   → incluye cuotas en cada partido (más lento)
    """
    with_odds = request.args.get("with_odds", "0") == "1"

    try:
        events = get_todays_events()
        result = []

        all_odds_data = get_all_odds() if with_odds else []

        for i, m in enumerate(events, 1):
            home_name = m.get("homeTeam", {}).get("name", "")
            away_name = m.get("awayTeam", {}).get("name", "")
            entry = {
                "index": i,
                "match_id": m.get("id"),
                "home_team": home_name,
                "away_team": away_name,
                "competition": m.get("competition", {}).get("name", ""),
                "sport_key": m.get("_sport_key", ""),
                "utc_date": m.get("utcDate", ""),
                "status": m.get("status", ""),
                "source": m.get("_source", ""),
            }
            if with_odds and all_odds_data:
                odds = find_odds_for_match(home_name, away_name, all_odds_data)
                entry["odds"] = odds or {}
            result.append(entry)

        return jsonify({"count": len(result), "matches": result})

    except Exception as e:
        logger.error(f"/matches/today error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


# ── /match/data ────────────────────────────────────────────────────────────────
@app.route("/match/data")
def match_data():
    """
    Devuelve TODOS los datos crudos + procesados de un partido.

    Params requeridos:
      home       → nombre del equipo local   (ej: Arsenal)
      away       → nombre del equipo visitante (ej: Chelsea)

    Params opcionales:
      sport_key  → clave de liga (ej: soccer_epl)
      match_id   → ID del partido en football-data.org
      save_pick  → 1 para guardar el pick en la BD
    """
    home_name = request.args.get("home", "").strip()
    away_name  = request.args.get("away", "").strip()
    sport_key  = request.args.get("sport_key", "").strip() or None
    match_id   = request.args.get("match_id", "").strip() or None

    if not home_name or not away_name:
        return jsonify({"error": "Parámetros 'home' y 'away' son requeridos"}), 400

    try:
        data = _run(_build_match_data(home_name, away_name, sport_key, match_id))
        return jsonify(data)
    except Exception as e:
        logger.error(f"/match/data error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


# ── /match/pick ────────────────────────────────────────────────────────────────
@app.route("/match/pick")
def match_pick():
    """
    Devuelve el análisis completo con IA + todos los datos del partido.

    Params requeridos:
      home, away

    Params opcionales:
      sport_key, match_id
      save=1  → guarda el pick en la BD local
    """
    home_name  = request.args.get("home", "").strip()
    away_name  = request.args.get("away", "").strip()
    sport_key  = request.args.get("sport_key", "").strip() or None
    match_id   = request.args.get("match_id", "").strip() or None
    save       = request.args.get("save", "0") == "1"

    if not home_name or not away_name:
        return jsonify({"error": "Parámetros 'home' y 'away' son requeridos"}), 400

    try:
        data = _run(_build_match_data(home_name, away_name, sport_key, match_id))
        ai_text = _generate_ai_pick(data)
        data["ai_pick_raw"] = ai_text

        parsed = parse_ai_pick(ai_text, home_name, away_name)
        data["ai_pick"] = parsed

        if save and parsed.get("pick_main"):
            pick_id = save_pick(
                home_team=home_name,
                away_team=away_name,
                competition=data.get("competition", ""),
                pick_main=parsed.get("pick_main"),
                pick_secondary=parsed.get("pick_secondary"),
                confidence=parsed.get("confidence"),
                odds_recommended=parsed.get("odds_recommended"),
                home_odds=data.get("odds", {}).get("home_win"),
                draw_odds=data.get("odds", {}).get("draw"),
                away_odds=data.get("odds", {}).get("away_win"),
                sport_key=sport_key,
                odds_event_id=match_id,
            )
            data["saved_pick_id"] = pick_id

        return jsonify(data)

    except Exception as e:
        logger.error(f"/match/pick error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


# ── Lógica principal de agregación de datos ────────────────────────────────────
async def _build_match_data(home_name: str, away_name: str,
                             sport_key: str | None, match_id: str | None) -> dict:
    """
    Agrega TODAS las fuentes de datos para un partido y devuelve un dict completo.
    Este es el núcleo de la API.
    """
    logger.info(f"Construyendo datos: {home_name} vs {away_name} | sport_key={sport_key}")

    # ── 1. Buscar IDs de equipo ────────────────────────────────────────────────
    home_id, away_id = await asyncio.gather(
        asyncio.to_thread(search_team_by_name, home_name, sport_key),
        asyncio.to_thread(search_team_by_name, away_name, sport_key),
    )

    # ── 2. Obtener cuotas ─────────────────────────────────────────────────────
    all_odds_raw = await asyncio.to_thread(get_all_odds)
    odds = find_odds_for_match(home_name, away_name, all_odds_raw) or {}

    # También intentar cuotas on-demand si tenemos sport_key
    if not odds and sport_key:
        odds = await asyncio.to_thread(
            get_odds_for_match_on_demand, sport_key, home_name, away_name
        ) or {}

    # ── 3. Obtener historial de partidos ──────────────────────────────────────
    home_matches, away_matches, h2h_raw = [], [], []
    home_stand, away_stand = {}, {}
    stats_source = "none"

    # Prioridad 1: football-data.org (si tenemos IDs)
    if home_id and away_id:
        home_matches, away_matches = await asyncio.gather(
            asyncio.to_thread(get_team_last_matches, home_id, 50),
            asyncio.to_thread(get_team_last_matches, away_id, 50),
        )
        if match_id:
            h2h_raw = await asyncio.to_thread(get_head_to_head, match_id, 10)
        stats_source = "football_data"

    # Prioridad 2: API-Football
    if len(home_matches) < 5 or len(away_matches) < 5:
        apifb_result = await asyncio.to_thread(
            apifb_get_full_match_data, home_name, away_name, sport_key or ""
        )
        apifb_home, apifb_away, apifb_h2h, apifb_home_stand, apifb_away_stand, apifb_hid, apifb_aid = apifb_result
        if len(apifb_home) > len(home_matches):
            home_matches = apifb_home
            if apifb_hid:
                home_id = apifb_hid
        if len(apifb_away) > len(away_matches):
            away_matches = apifb_away
            if apifb_aid:
                away_id = apifb_aid
        if apifb_h2h and not h2h_raw:
            h2h_raw = apifb_h2h
        if apifb_home_stand:
            home_stand = apifb_home_stand
        if apifb_away_stand:
            away_stand = apifb_away_stand
        if apifb_home or apifb_away:
            stats_source = "apifootball"

    # Prioridad 3: SofaScore + ESPN + cache local
    if len(home_matches) < 5 or len(away_matches) < 5:
        agg = await get_extended_match_data(home_name, away_name, sport_key or "")
        if len(agg["home_matches"]) > len(home_matches):
            home_matches = agg["home_matches"]
        if len(agg["away_matches"]) > len(away_matches):
            away_matches = agg["away_matches"]
        if agg["h2h"] and not h2h_raw:
            h2h_raw = agg["h2h"]
        if agg["home_matches"] or agg["away_matches"]:
            stats_source = agg.get("source", "aggregator")

    # Prioridad 4: datos mínimos desde scores de The Odds API
    home_stats_lite, away_stats_lite = {}, {}
    if len(home_matches) < 3 and sport_key:
        home_stats_lite, away_stats_lite, home_rest_lite, away_rest_lite = await asyncio.to_thread(
            get_team_form_from_scores, sport_key, home_name, away_name
        )

    logger.info(
        f"Historial: home={len(home_matches)} away={len(away_matches)} "
        f"h2h={len(h2h_raw)} fuente={stats_source}"
    )

    # ── 4. Estadísticas de posiciones (standings) ─────────────────────────────
    league_id = SPORT_KEY_TO_LEAGUE.get(sport_key or "")
    if not home_stand and league_id and home_id:
        home_stand = await asyncio.to_thread(
            lambda: __import__('apifootball').get_team_standing(league_id, home_id)
        )
    if not away_stand and league_id and away_id:
        away_stand = await asyncio.to_thread(
            lambda: __import__('apifootball').get_team_standing(league_id, away_id)
        )

    # ── 5. Lesionados ─────────────────────────────────────────────────────────
    home_injuries, away_injuries = await asyncio.gather(
        asyncio.to_thread(get_team_injuries, home_id) if home_id else asyncio.sleep(0, result=[]),
        asyncio.to_thread(get_team_injuries, away_id) if away_id else asyncio.sleep(0, result=[]),
    )

    # ── 6. Clima ──────────────────────────────────────────────────────────────
    weather = await asyncio.to_thread(get_weather_for_team, home_name)

    # ── 7. Stats de temporada (API-Football) ──────────────────────────────────
    home_season_stats, away_season_stats = None, None
    if league_id:
        if home_id:
            home_season_stats = await asyncio.to_thread(apifb_season_stats, home_id, league_id)
        if away_id:
            away_season_stats = await asyncio.to_thread(apifb_season_stats, away_id, league_id)

    # ── 8. Entrenadores ───────────────────────────────────────────────────────
    home_coach, away_coach = None, None
    if home_id:
        home_coach = await asyncio.to_thread(apifb_get_coach, home_id)
    if away_id:
        away_coach = await asyncio.to_thread(apifb_get_coach, away_id)

    # ── 9. Procesar estadísticas ──────────────────────────────────────────────
    home_stats = extract_team_stats(home_matches, home_id) if home_matches and home_id else home_stats_lite or {}
    away_stats = extract_team_stats(away_matches, away_id) if away_matches and away_id else away_stats_lite or {}

    home_ht  = halftime_stats(home_matches, home_id) if home_matches and home_id else {}
    away_ht  = halftime_stats(away_matches, away_id) if away_matches and away_id else {}

    home_dow = day_of_week_stats(home_matches, home_id) if home_matches and home_id else {}
    away_dow = day_of_week_stats(away_matches, away_id) if away_matches and away_id else {}

    home_night = night_vs_day_stats(home_matches, home_id) if home_matches and home_id else {}
    away_night = night_vs_day_stats(away_matches, away_id) if away_matches and away_id else {}

    home_cup_fatigue = post_cup_fatigue(home_matches, home_id) if home_matches and home_id else None
    away_cup_fatigue = post_cup_fatigue(away_matches, away_id) if away_matches and away_id else None

    home_half_season = half_season_stats(home_matches, home_id) if home_matches and home_id else {}
    away_half_season = half_season_stats(away_matches, away_id) if away_matches and away_id else {}

    home_streak_str = calculate_streak(home_stats.get("results", []))
    away_streak_str = calculate_streak(away_stats.get("results", []))

    home_days_rest = days_since_last_match(home_matches) if home_matches else None
    away_days_rest = days_since_last_match(away_matches) if away_matches else None

    h2h = h2h_stats(h2h_raw, home_id, away_id) if h2h_raw else {}

    # ── 10. Poisson ───────────────────────────────────────────────────────────
    poisson_data = poisson_prediction(
        home_avg_score=home_stats.get("avg_home_scored", home_stats.get("avg_scored", 1.2)),
        away_avg_score=away_stats.get("avg_away_scored", away_stats.get("avg_scored", 1.0)),
        home_avg_concede=home_stats.get("avg_home_conceded", home_stats.get("avg_conceded", 1.1)),
        away_avg_concede=away_stats.get("avg_away_conceded", away_stats.get("avg_conceded", 1.2)),
        is_home=True,
        league_key=sport_key,
    )

    # ── 11. Score de confianza combinado ──────────────────────────────────────
    confidence_score = calculate_confidence_score(
        home_stats, away_stats, poisson_data,
        h2h, odds, home_stand, away_stand,
    )

    # ── 12. Value bet ─────────────────────────────────────────────────────────
    leader = confidence_score.get("leader", "draw")
    leader_prob = confidence_score.get(leader, 33)
    leader_odds = None
    if leader == "home":
        leader_odds = odds.get("home_win")
    elif leader == "draw":
        leader_odds = odds.get("draw")
    elif leader == "away":
        leader_odds = odds.get("away_win")
    value_bet = calculate_value_bet(leader_prob, leader_odds) if leader_odds else None

    # ── 13. Movimiento de cuotas ──────────────────────────────────────────────
    odds_movement = get_odds_movement(match_id or f"{home_name}_{away_name}", odds) if odds else None

    # ── 14. Día de la semana del partido ──────────────────────────────────────
    match_dow = None
    # (si el llamador pasa utcDate se podría calcular; se deja para que lo calcule main.py)

    return {
        # Identificación
        "home_team": home_name,
        "away_team": away_name,
        "competition": "",     # rellenar desde el llamador si disponible
        "sport_key": sport_key or "",
        "match_id": match_id or "",
        "home_id": home_id,
        "away_id": away_id,
        "stats_source": stats_source,

        # Estadísticas históricas procesadas
        "home_stats": home_stats,
        "away_stats": away_stats,
        "home_matches_count": len(home_matches),
        "away_matches_count": len(away_matches),

        # Cuotas
        "odds": odds,
        "odds_movement": odds_movement,

        # Tabla de posiciones
        "home_standing": home_stand,
        "away_standing": away_stand,

        # Lesionados
        "home_injuries": home_injuries or [],
        "away_injuries": away_injuries or [],

        # Clima
        "weather": weather,
        "weather_formatted": format_weather(weather),

        # Stats de temporada
        "home_season_stats": home_season_stats,
        "away_season_stats": away_season_stats,

        # Entrenadores
        "home_coach": home_coach,
        "away_coach": away_coach,

        # Stats avanzadas
        "home_ht": home_ht,
        "away_ht": away_ht,
        "home_dow": home_dow,
        "away_dow": away_dow,
        "home_night": home_night,
        "away_night": away_night,
        "home_cup_fatigue": home_cup_fatigue,
        "away_cup_fatigue": away_cup_fatigue,
        "home_half_season": home_half_season,
        "away_half_season": away_half_season,
        "home_streak": home_streak_str,
        "away_streak": away_streak_str,
        "home_days_rest": home_days_rest,
        "away_days_rest": away_days_rest,

        # H2H
        "h2h": h2h,

        # Poisson y confianza
        "poisson": poisson_data,
        "confidence_score": confidence_score,
        "value_bet": value_bet,

        # Meta
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "league_avg_used": LEAGUE_AVG_GOALS.get(sport_key or "", 1.35),
        "data_quality": {
            "has_home_stats": bool(home_stats),
            "has_away_stats": bool(away_stats),
            "has_odds": bool(odds),
            "has_h2h": bool(h2h),
            "has_standings": bool(home_stand or away_stand),
            "has_injuries": bool(home_injuries or away_injuries),
            "has_weather": bool(weather),
        },
    }


def _generate_ai_pick(data: dict) -> str:
    """Llama a generate_pick con todos los datos del dict."""
    return generate_pick(
        home_team=data["home_team"],
        away_team=data["away_team"],
        home_stats=data["home_stats"],
        away_stats=data["away_stats"],
        poisson_data=data["poisson"],
        h2h=data["h2h"],
        odds=data["odds"],
        competition=data.get("competition", ""),
        home_standing=data.get("home_standing"),
        away_standing=data.get("away_standing"),
        home_streak=data.get("home_streak", ""),
        away_streak=data.get("away_streak", ""),
        odds_movement=data.get("odds_movement"),
        home_injuries=data.get("home_injuries"),
        away_injuries=data.get("away_injuries"),
        home_days_rest=data.get("home_days_rest"),
        away_days_rest=data.get("away_days_rest"),
        confidence_score=data.get("confidence_score"),
        home_season_stats=data.get("home_season_stats"),
        away_season_stats=data.get("away_season_stats"),
        weather=data.get("weather"),
        home_ht=data.get("home_ht"),
        away_ht=data.get("away_ht"),
        home_dow=data.get("home_dow"),
        away_dow=data.get("away_dow"),
        home_night=data.get("home_night"),
        away_night=data.get("away_night"),
        home_cup_fatigue=data.get("home_cup_fatigue"),
        away_cup_fatigue=data.get("away_cup_fatigue"),
        home_half_season=data.get("home_half_season"),
        away_half_season=data.get("away_half_season"),
        home_coach=data.get("home_coach"),
        away_coach=data.get("away_coach"),
        match_dow=data.get("match_dow"),
    )


# ── Punto de entrada ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    logger.info(f"Iniciando Match API en puerto {port} (debug={debug})")
    app.run(host="0.0.0.0", port=port, debug=debug)
