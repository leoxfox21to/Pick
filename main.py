import os
import re
import logging
import asyncio
from datetime import datetime, timezone, timedelta

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

async def _empty_dict(): return {}
async def _empty_list(): return []

from football_api import get_team_last_matches, get_head_to_head, get_team_standing, search_team_by_name
from odds_api import (get_all_odds, find_odds_for_match,
                      get_todays_events, get_tomorrows_events,
                      get_odds_for_match_on_demand, get_odds_by_event_id,
                      get_scores_for_sport, get_team_form_from_scores)
from analyzer import (extract_team_stats, poisson_prediction, h2h_stats,
                      calculate_value_bet, calculate_streak, days_since_last_match,
                      calculate_confidence_score, halftime_stats, day_of_week_stats,
                      night_vs_day_stats, post_cup_fatigue, half_season_stats,
                      get_xg_from_matches, get_xg_proxy_from_stats, odds_range_performance,
                      MIN_MATCHES_POISSON, market_vs_model_conflict,
                      kelly_criterion, get_league_efficiency_label)
from injuries_api import get_team_injuries, format_injuries
from ai_pick import generate_pick
from apifootball import (get_full_match_data as apifb_get_full_match_data,
                         get_team_season_stats as apifb_season_stats,
                         get_coach as apifb_get_coach,
                         SPORT_KEY_TO_LEAGUE)
from weather import get_weather_for_team, format_weather
from db import (init_db, save_pick, get_history, get_stats as db_get_stats,
                get_pending_picks, update_pick_result, parse_ai_pick,
                name_matches, determine_correct,
                subscribe, unsubscribe, is_subscribed, get_active_subscribers,
                mark_alert_sent, alert_already_sent,
                save_match_to_cache, get_team_matches_from_cache,
                get_stats_by_league, get_calibration_stats, save_closing_odds,
                place_auto_bet, resolve_auto_bets_for_pick,
                get_balance, get_bankroll_summary, get_bankroll_history,
                get_today_total_staked, get_rendimiento_stats)
from odds_tracker import save_odds_snapshot, get_odds_movement, get_closing_odds, calculate_clv, get_clv_label
from api_status import check_odds_api, check_football_data, check_apifootball, check_groq
from data_aggregator import get_extended_match_data
from referee import get_match_referee, get_referee_stats, format_referee_for_telegram
from suspensions import get_suspension_risks, format_suspensions

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CUBA_TZ = timezone(timedelta(hours=-4))

matches_cache  = {}
tomorrow_cache = {}
analyzed_matches = set()


def utc_to_cuba(utc_str):
    if not utc_str:
        return "?"
    try:
        dt = datetime.fromisoformat(utc_str.replace("Z", "+00:00"))
        return dt.astimezone(CUBA_TZ).strftime("%I:%M %p")
    except Exception:
        return utc_str[:16]


def _esc(text):
    """Escapa caracteres especiales HTML: &, <, > para usar en parse_mode=HTML."""
    if not text:
        return ""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def format_match_list(matches, title="HOY"):
    """Devuelve HTML (parse_mode='HTML') — inmune a guiones bajos y caracteres especiales."""
    if not matches:
        return (
            f"⏰ No hay partidos disponibles {title.lower()}.\n\n"
            "Revisa más tarde o verifica tu ODDS_API_KEY."
        )
    emoji = "🌅" if title == "MAÑANA" else "⚽"
    lines = [f"{emoji} <b>PARTIDOS {title}</b> 🇨🇺\n"]
    for i, m in enumerate(matches, 1):
        raw_home = m.get("homeTeam", {}).get("shortName") or m.get("homeTeam", {}).get("name", "?")
        raw_away = m.get("awayTeam", {}).get("shortName") or m.get("awayTeam", {}).get("name", "?")
        raw_comp = m.get("competition", {}).get("name") or ""
        home      = _esc(raw_home)
        away      = _esc(raw_away)
        comp      = _esc(raw_comp)
        time_cuba = utc_to_cuba(m.get("utcDate", ""))
        status    = m.get("status", "")
        done      = "✅ " if m.get("id") in analyzed_matches else ""
        if status in ("IN_PLAY", "PAUSED"):
            status_icon = "🔴 EN VIVO"
        else:
            status_icon = f"🕐 {time_cuba}"
        lines.append(f"<code>{i:2d}.</code> {done}<b>{home}</b> vs <b>{away}</b>")
        lines.append(f"    🏆 {comp} | {status_icon}")
        lines.append("")
    cmd_hint = "/pick" if title == "HOY" else "/pick_manana"
    lines.append(f"<i>{cmd_hint} &lt;número&gt; para analizar</i>")
    return "\n".join(lines)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *Bot de Picks Deportivos*\n\n"
        "⚽ /partidos — Partidos de HOY (45+ ligas)\n"
        "🌅 /manana — Partidos de MAÑANA\n"
        "🔍 /pick <n> — Análisis completo con IA\n"
        "🌅 /pick\\_manana <n> — Análisis partido de mañana\n"
        "🎯 /combinadas — Mejores 2-3 picks del día\n"
        "📊 /historial — Últimos picks con resultados\n"
        "📈 /stats — Estadísticas de aciertos globales\n"
        "📉 /rendimiento — ROI por tipo, confianza y liga\n"
        "🏆 /ligas — Aciertos por liga\n"
        "🤖 /bankroll — Balance autónomo ($90 inicial)\n"
        "🔔 /alertas — Activar/desactivar alertas automáticas\n\n"
        "_Ejemplo: /pick 3_",
        parse_mode="Markdown"
    )


async def _load_matches_day(update: Update, context: ContextTypes.DEFAULT_TYPE,
                             offset_days: int = 0):
    global matches_cache, tomorrow_cache
    title   = "HOY" if offset_days == 0 else "MAÑANA"
    msg     = await update.message.reply_text(f"⏳ Cargando partidos de {title.lower()}...")
    try:
        fetcher = get_todays_events if offset_days == 0 else get_tomorrows_events
        matches, all_odds = await asyncio.gather(
            asyncio.to_thread(fetcher),
            asyncio.to_thread(get_all_odds),
        )

        def _cuba_sort_key(m):
            utc_str = m.get("utcDate", "")
            if not utc_str:
                return "99:99"
            try:
                dt = datetime.fromisoformat(utc_str.replace("Z", "+00:00"))
                return dt.astimezone(CUBA_TZ).strftime("%H:%M")
            except Exception:
                return "99:99"

        matches.sort(key=_cuba_sort_key)

        if offset_days == 0:
            matches_cache  = {i + 1: m for i, m in enumerate(matches)}
        else:
            tomorrow_cache = {i + 1: m for i, m in enumerate(matches)}

        saved = 0
        for m in matches:
            home_name = m.get("homeTeam", {}).get("name", "")
            away_name = m.get("awayTeam", {}).get("name", "")
            match_id  = m.get("id")
            odds      = find_odds_for_match(home_name, away_name, all_odds)
            if odds:
                save_odds_snapshot(match_id, home_name, away_name, odds)
                saved += 1

        text = format_match_list(matches, title=title)
        if saved:
            text += f"\n<i>📸 Cuotas guardadas: {saved} partidos</i>"
        await msg.edit_text(text, parse_mode="HTML")

    except Exception as e:
        logger.error(f"Error getting matches ({title}): {e}", exc_info=True)
        await msg.edit_text(f"❌ Error al cargar los partidos.\n<code>{_esc(str(e)[:150])}</code>", parse_mode="HTML")


async def cmd_partidos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _load_matches_day(update, context, offset_days=0)


async def cmd_manana(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _load_matches_day(update, context, offset_days=1)


async def _cmd_pick_from_cache(update: Update, context: ContextTypes.DEFAULT_TYPE,
                                cache: dict, label: str):
    global analyzed_matches
    if not context.args:
        cmd_carga = "/partidos" if label == "pick" else "/manana"
        await update.message.reply_text(
            f"⚠️ Usa: /{label} <número>\nEjemplo: /{label} 1\n\n"
            f"Primero ejecuta {cmd_carga}"
        )
        return

    try:
        num = int(context.args[0])
    except ValueError:
        await update.message.reply_text(f"⚠️ El número debe ser entero. Ej: /{label} 1")
        return

    if not cache:
        cmd_carga = "/partidos" if label == "pick" else "/manana"
        await update.message.reply_text(f"⚠️ Primero ejecuta {cmd_carga}.")
        return

    match = cache.get(num)
    if not match:
        max_num = max(cache.keys()) if cache else 0
        await update.message.reply_text(f"⚠️ No existe #{num}. Hay {max_num} partidos disponibles.")
        return

    match_id         = match.get("id")
    home_team        = match.get("homeTeam", {})
    away_team        = match.get("awayTeam", {})
    home_name        = home_team.get("name", "Local")
    away_name        = away_team.get("name", "Visitante")
    home_id          = home_team.get("id")
    away_id          = away_team.get("id")
    competition      = match.get("competition", {}).get("name") or "Desconocida"
    competition_code = match.get("competition", {}).get("code") or ""
    sport_key        = match.get("_sport_key")
    odds_event_id    = match.get("_event_id")
    time_cuba        = utc_to_cuba(match.get("utcDate", ""))
    is_odds_source   = match.get("_source") == "odds"

    if match_id in analyzed_matches:
        await update.message.reply_text(
            f"✅ Partido #{num} ya fue analizado.\nUsa /partidos o /manana para ver la lista."
        )
        return

    msg = await update.message.reply_text(
        f"🔍 Analizando *{home_name}* vs *{away_name}*...\n\n"
        "🔎 Buscando equipos...",
        parse_mode="Markdown"
    )

    try:
        if is_odds_source and (not home_id or not away_id):
            home_id, away_id = await asyncio.gather(
                asyncio.to_thread(search_team_by_name, home_name, sport_key),
                asyncio.to_thread(search_team_by_name, away_name, sport_key),
            )

        has_historical  = bool(home_id and away_id)
        stats_limited   = not has_historical
        home_stats_scores, away_stats_scores, home_rest_scores, away_rest_scores = {}, {}, None, None
        home_matches, away_matches, h2h_raw = [], [], []
        home_stand, away_stand = {}, {}
        home_injuries, away_injuries = [], []

        if stats_limited:
            await msg.edit_text(
                f"🔍 Analizando *{home_name}* vs *{away_name}*...\n\n"
                "📡 Sin ID en BD — consultando API-Football / SofaScore...",
                parse_mode="Markdown"
            )
            home_matches, away_matches, h2h_raw, home_stand, away_stand, apifb_hid, apifb_aid = await asyncio.to_thread(
                apifb_get_full_match_data, home_name, away_name, sport_key or ""
            )
            if home_matches or away_matches:
                stats_limited = False
                home_id = apifb_hid or home_id
                away_id = apifb_aid or away_id
            else:
                agg = await get_extended_match_data(home_name, away_name, sport_key or "")
                if len(agg["home_matches"]) >= MIN_MATCHES_POISSON or len(agg["away_matches"]) >= MIN_MATCHES_POISSON:
                    home_matches = agg["home_matches"]
                    away_matches = agg["away_matches"]
                    h2h_raw      = agg["h2h"]
                    stats_limited = False
                    if agg["home_id"]: home_id = agg["home_id"]
                    if agg["away_id"]: away_id = agg["away_id"]
                else:
                    h2h_raw = []
                    if sport_key:
                        home_stats_scores, away_stats_scores, home_rest_scores, away_rest_scores = await asyncio.to_thread(
                            get_team_form_from_scores, sport_key, home_name, away_name
                        )

        elif is_odds_source:
            home_matches, away_matches, home_injuries, away_injuries = await asyncio.gather(
                asyncio.to_thread(get_team_last_matches, home_id, 50),
                asyncio.to_thread(get_team_last_matches, away_id, 50),
                asyncio.to_thread(get_team_injuries, home_id),
                asyncio.to_thread(get_team_injuries, away_id),
            )
        else:
            home_matches, away_matches, h2h_raw, home_stand, away_stand, home_injuries, away_injuries = await asyncio.gather(
                asyncio.to_thread(get_team_last_matches, home_id, 50),
                asyncio.to_thread(get_team_last_matches, away_id, 50),
                asyncio.to_thread(get_head_to_head, match_id, 10),
                asyncio.to_thread(get_team_standing, competition_code, home_id),
                asyncio.to_thread(get_team_standing, competition_code, away_id),
                asyncio.to_thread(get_team_injuries, home_id),
                asyncio.to_thread(get_team_injuries, away_id),
            )

        # ── Datos adicionales en paralelo ────────────────────────────
        league_id_for_extras = (
            match.get("competition", {}).get("id")
            or SPORT_KEY_TO_LEAGUE.get(sport_key or "")
        )
        (weather_data,
         ref_info,
         home_season_stats, away_season_stats,
         home_coach_info, away_coach_info,
         home_susp, away_susp) = await asyncio.gather(
            asyncio.to_thread(get_weather_for_team, home_name),
            asyncio.to_thread(get_match_referee, home_name, away_name),
            asyncio.to_thread(apifb_season_stats, home_id, league_id_for_extras) if (home_id and league_id_for_extras) else _empty_dict(),
            asyncio.to_thread(apifb_season_stats, away_id, league_id_for_extras) if (away_id and league_id_for_extras) else _empty_dict(),
            asyncio.to_thread(apifb_get_coach, home_id) if home_id else _empty_dict(),
            asyncio.to_thread(apifb_get_coach, away_id) if away_id else _empty_dict(),
            asyncio.to_thread(get_suspension_risks, home_id, home_name, sport_key or "") if home_id else _empty_list(),
            asyncio.to_thread(get_suspension_risks, away_id, away_name, sport_key or "") if away_id else _empty_list(),
        )

        # Stats de árbitro (depende de ref_info)
        ref_stats = None
        if ref_info and ref_info.get("id"):
            ref_stats = await asyncio.to_thread(
                get_referee_stats, ref_info.get("id"), ref_info.get("name", "")
            )

        # ── Analíticas del historial ─────────────────────────────────
        eff_hid, eff_aid = home_id, away_id
        home_ht    = halftime_stats(home_matches, eff_hid)    if home_matches and eff_hid else {}
        away_ht    = halftime_stats(away_matches, eff_aid)    if away_matches and eff_aid else {}
        home_cup   = post_cup_fatigue(home_matches, eff_hid)  if home_matches and eff_hid else None
        away_cup   = post_cup_fatigue(away_matches, eff_aid)  if away_matches and eff_aid else None
        home_hs    = half_season_stats(home_matches, eff_hid) if home_matches and eff_hid else {}
        away_hs    = half_season_stats(away_matches, eff_aid) if away_matches and eff_aid else {}
        home_dow   = day_of_week_stats(home_matches, eff_hid) if home_matches and eff_hid else {}
        away_dow   = day_of_week_stats(away_matches, eff_aid) if away_matches and eff_aid else {}
        home_night = night_vs_day_stats(home_matches, eff_hid) if home_matches and eff_hid else {}
        away_night = night_vs_day_stats(away_matches, eff_aid) if away_matches and eff_aid else {}

        match_dow = None
        try:
            dt_m      = datetime.fromisoformat(match.get("utcDate", "").replace("Z", "+00:00"))
            match_dow = dt_m.astimezone(CUBA_TZ).strftime("%a")
        except Exception:
            pass

        home_stats = extract_team_stats(home_matches, home_id) if home_matches and home_id else {}
        away_stats = extract_team_stats(away_matches, away_id) if away_matches and away_id else {}

        home_total = home_stats.get("total_matches", 0)
        away_total = away_stats.get("total_matches", 0)

        # FIX: usar MIN_MATCHES_POISSON (8) en lugar de 5
        if not stats_limited and (home_total < MIN_MATCHES_POISSON or away_total < MIN_MATCHES_POISSON):
            stats_limited = True
            logger.warning(f"Poisson bloqueado (<{MIN_MATCHES_POISSON} partidos): {home_name}={home_total}, {away_name}={away_total}")
            if sport_key:
                home_stats_scores, away_stats_scores, home_rest_scores, away_rest_scores = await asyncio.to_thread(
                    get_team_form_from_scores, sport_key, home_name, away_name
                )

        if stats_limited:
            if not home_stats and home_stats_scores:
                home_stats = home_stats_scores
            if not away_stats and away_stats_scores:
                away_stats = away_stats_scores

        # ── xG desde partidos SofaScore, con proxy como fallback ────
        home_xg = get_xg_from_matches(home_matches, home_id) if home_matches and home_id else {}
        away_xg = get_xg_from_matches(away_matches, away_id) if away_matches and away_id else {}
        if not home_xg and home_stats:
            home_xg = get_xg_proxy_from_stats(home_stats, sport_key)
        if not away_xg and away_stats:
            away_xg = get_xg_proxy_from_stats(away_stats, sport_key)

        # ── Cuotas ───────────────────────────────────────────────────
        all_odds = await asyncio.to_thread(get_all_odds)
        odds = {}
        if odds_event_id and sport_key:
            odds = await asyncio.to_thread(get_odds_by_event_id, sport_key, odds_event_id) or {}
        if not odds:
            odds = find_odds_for_match(home_name, away_name, all_odds) or {}
        if not odds and sport_key:
            odds = await asyncio.to_thread(get_odds_for_match_on_demand, sport_key, home_name, away_name) or {}

        # ── Rendimiento según cuota actual ───────────────────────────
        home_odds_range = odds_range_performance(home_matches, home_id, odds.get("home_win"), True)  if home_matches and home_id else {}
        away_odds_range = odds_range_performance(away_matches, away_id, odds.get("away_win"), False) if away_matches and away_id else {}

        h2h = h2h_stats(h2h_raw, home_id, away_id) if h2h_raw else {}
        odds_movement = get_odds_movement(match_id, odds) if odds else None
        home_streak   = calculate_streak(home_stats.get("results", [])) if home_stats else "Sin datos"
        away_streak   = calculate_streak(away_stats.get("results", [])) if away_stats else "Sin datos"

        home_days_rest = home_rest_scores if stats_limited else (days_since_last_match(home_matches) if home_matches else None)
        away_days_rest = away_rest_scores if stats_limited else (days_since_last_match(away_matches) if away_matches else None)

        has_scores_stats = bool(
            home_stats.get("total_matches", 0) >= MIN_MATCHES_POISSON and
            away_stats.get("total_matches", 0) >= MIN_MATCHES_POISSON
        )

        # ── Poisson ──────────────────────────────────────────────────
        if stats_limited and has_scores_stats:
            poisson_data = poisson_prediction(
                home_stats.get("avg_home_scored", home_stats.get("avg_scored", 1.2)),
                away_stats.get("avg_away_scored", away_stats.get("avg_scored", 1.0)),
                home_stats.get("avg_home_conceded", home_stats.get("avg_conceded", 1.2)),
                away_stats.get("avg_away_conceded", away_stats.get("avg_conceded", 1.2)),
                league_key=sport_key,
            )
            stats_source_note = f"Stats parciales ({home_total + away_total} partidos)"
        elif stats_limited:
            imp_h  = round(1 / odds["home_win"] * 100, 1) if odds.get("home_win") else 33.0
            imp_d  = round(1 / odds["draw"]     * 100, 1) if odds.get("draw")     else 33.0
            imp_a  = round(1 / odds["away_win"] * 100, 1) if odds.get("away_win") else 34.0
            tot_i  = imp_h + imp_d + imp_a
            nh = imp_h / tot_i * 100 if tot_i else 33
            nd = imp_d / tot_i * 100 if tot_i else 33
            na = imp_a / tot_i * 100 if tot_i else 34
            poisson_data = {
                "lambda_home": round(nh / 33, 2), "lambda_away": round(na / 33, 2),
                "prob_home_win": round(nh, 1), "prob_draw": round(nd, 1), "prob_away_win": round(na, 1),
                "prob_over25": 50.0, "prob_btts": 50.0,
                "most_likely_score": "N/A", "top_scores": [],
            }
            stats_source_note = "⚠️ Solo cuotas (sin historial suficiente)"
        else:
            poisson_data = poisson_prediction(
                home_stats.get("avg_home_scored", home_stats.get("avg_scored", 1.2)),
                away_stats.get("avg_away_scored", away_stats.get("avg_scored", 1.0)),
                home_stats.get("avg_home_conceded", home_stats.get("avg_conceded", 1.2)),
                away_stats.get("avg_away_conceded", away_stats.get("avg_conceded", 1.2)),
                league_key=sport_key,
            )
            stats_source_note = ""

        ai_home_stats = {} if (stats_limited and not has_scores_stats) else home_stats
        ai_away_stats = {} if (stats_limited and not has_scores_stats) else away_stats

        confidence_score = calculate_confidence_score(
            ai_home_stats, ai_away_stats, poisson_data, h2h, odds, home_stand, away_stand,
            league_key=sport_key
        )

        status_note = stats_source_note if stats_limited else f"✅ Confianza: {confidence_score.get('confidence',0)}%"
        await msg.edit_text(
            f"🔍 Analizando *{home_name}* vs *{away_name}*...\n\n"
            f"{status_note}\n🤖 Generando pick con IA...",
            parse_mode="Markdown"
        )

        ai_result = await asyncio.to_thread(
            generate_pick,
            home_name, away_name,
            ai_home_stats, ai_away_stats,
            poisson_data, h2h, odds, competition,
            home_stand, away_stand,
            home_streak, away_streak,
            odds_movement,
            home_injuries, away_injuries,
            home_days_rest, away_days_rest,
            confidence_score,
            home_season_stats, away_season_stats,
            weather_data,
            home_ht, away_ht,
            home_dow, away_dow,
            home_night, away_night,
            home_cup, away_cup,
            home_hs, away_hs,
            home_coach_info, away_coach_info,
            match_dow,
            # Nuevos parámetros
            home_xg, away_xg,
            home_odds_range, away_odds_range,
            ref_info, ref_stats,
            home_susp, away_susp,
            league_key=sport_key,
        )

        parsed = parse_ai_pick(ai_result, home_name, away_name)
        pick_id = save_pick(
            home_team=home_name, away_team=away_name, competition=competition,
            pick_main=parsed.get("pick_main"), pick_secondary=parsed.get("pick_secondary"),
            confidence=parsed.get("confidence"), odds_recommended=parsed.get("odds_recommended"),
            home_odds=odds.get("home_win"), draw_odds=odds.get("draw"), away_odds=odds.get("away_win"),
            sport_key=sport_key, odds_event_id=odds_event_id, match_date=match.get("utcDate", "")[:10],
        )
        # Guardar CLV inicial si tenemos odds_event_id (para comparar al cierre)
        if pick_id and odds_event_id:
            closing = get_closing_odds(odds_event_id)
            if closing and closing.get("home_win"):
                save_closing_odds(pick_id, closing.get("home_win"), closing.get("draw"), closing.get("away_win"))

        # ── Auto-bet bankroll autónomo ───────────────────────────────
        auto_bet_info = None
        if pick_id and parsed.get("confidence", 0) and (parsed.get("confidence", 0) >= 58):
            _leader = confidence_score.get("leader", "") if confidence_score else ""
            if _leader == "home":   _bet_odds = odds.get("home_win"); _kelly_auto = kelly_h
            elif _leader == "draw": _bet_odds = odds.get("draw");     _kelly_auto = kelly_d
            elif _leader == "away": _bet_odds = odds.get("away_win"); _kelly_auto = kelly_a
            else:                    _bet_odds = parsed.get("odds_recommended"); _kelly_auto = None
            if not _kelly_auto:
                _kelly_auto = kelly_criterion(parsed.get("confidence", 0), _bet_odds)
            if _kelly_auto and _bet_odds and _bet_odds > 1.0:
                _pick_label = parsed.get("pick_main") or f"Pick principal"
                auto_bet_info = place_auto_bet(
                    pick_id=pick_id,
                    match_desc=f"{home_name} vs {away_name}",
                    pick_label=_pick_label,
                    odds=_bet_odds,
                    kelly_pct=_kelly_auto,
                )

        # ── Strings para el mensaje ──────────────────────────────────
        value_home = calculate_value_bet(poisson_data["prob_home_win"], odds.get("home_win"))
        value_away = calculate_value_bet(poisson_data["prob_away_win"], odds.get("away_win"))
        value_draw = calculate_value_bet(poisson_data["prob_draw"],     odds.get("draw"))
        value_over = calculate_value_bet(poisson_data["prob_over25"],   odds.get("over_25"))
        best_value = max(
            [("Victoria " + home_name, value_home), ("Empate", value_draw),
             ("Victoria " + away_name, value_away), ("Over 2.5", value_over)],
            key=lambda x: x[1] if x[1] is not None else -999
        )

        def stand_line(name, s):
            if not s or not s.get("position"):
                return f"  {name}: Sin datos de tabla"
            return (f"  {name}: #{s['position']}/{s.get('total_teams','?')} | "
                    f"{s.get('points','?')}pts | GD{s.get('goal_diff',0):+d}")

        def rest_icon(days):
            if days is None:      return "❓"
            if days <= 2:         return f"⚠️ {days}d (fatiga)"
            if days <= 4:         return f"🟡 {days}d"
            return f"✅ {days}d"

        tabla_str = ""
        if home_stand or away_stand:
            tabla_str = (f"\n📋 *Tabla de posiciones:*\n"
                         f"{stand_line(home_name, home_stand)}\n{stand_line(away_name, away_stand)}")

        if stats_limited and not has_scores_stats:
            racha_str = fatigue_str = form_str = defense_str = ""
        else:
            racha_str = (f"\n⚡ *Racha actual:*\n  {home_name}: {home_streak}\n  {away_name}: {away_streak}")
            fatigue_str = (
                f"\n😴 *Descanso:*\n  {home_name}: {rest_icon(home_days_rest)}\n  {away_name}: {rest_icon(away_days_rest)}"
            ) if (home_days_rest is not None or away_days_rest is not None) else ""
            form_str = (
                f"\n📈 *Forma (general | local/visit.):*\n"
                f"  {home_name}: {home_stats.get('form_5','N/A')} | Casa: {home_stats.get('home_form_5','N/A')} | ELO {home_stats.get('elo','?')}\n"
                f"  {away_name}: {away_stats.get('form_5','N/A')} | Fuera: {away_stats.get('away_form_5','N/A')} | ELO {away_stats.get('elo','?')}"
            )
            defense_str = (
                f"\n🧱 *Defensa:*\n"
                f"  {home_name}: {home_stats.get('clean_sheets_rate',0)*100:.0f}% portería a 0\n"
                f"  {away_name}: {away_stats.get('clean_sheets_rate',0)*100:.0f}% portería a 0"
            )

        # xG
        xg_str = ""
        if home_xg or away_xg:
            lines_xg = ["\n📐 *xG (Expected Goals):*"]
            if home_xg:
                lines_xg.append(
                    f"  {home_name}: xG {home_xg['avg_xg_scored']}/p vs {home_xg.get('avg_goals','?')} goles reales"
                )
                if home_xg.get("over_performer"):
                    lines_xg.append(f"  ⚠️ {home_xg['over_performer']}")
            if away_xg:
                lines_xg.append(
                    f"  {away_name}: xG {away_xg['avg_xg_scored']}/p vs {away_xg.get('avg_goals','?')} goles reales"
                )
                if away_xg.get("over_performer"):
                    lines_xg.append(f"  ⚠️ {away_xg['over_performer']}")
            xg_str = "\n".join(lines_xg)

        # Rendimiento como favorito/underdog
        role_str = ""
        if home_odds_range.get("label") or away_odds_range.get("label"):
            lines_role = ["\n🎰 *Rendimiento según cuota actual:*"]
            if home_odds_range.get("label"):
                lines_role.append(f"  {home_name}: {home_odds_range['label']}")
            if away_odds_range.get("label"):
                lines_role.append(f"  {away_name}: {away_odds_range['label']}")
            role_str = "\n".join(lines_role)

        # Árbitro
        referee_str = format_referee_for_telegram(ref_info, ref_stats)

        # Suspensiones
        susp_str = format_suspensions(home_susp, home_name) + format_suspensions(away_susp, away_name)

        poisson_label = "📊 *Probabilidades (cuotas):*" if stats_limited else "📐 *Modelo Poisson:*"
        top_scores_line = ""
        if poisson_data.get("top_scores"):
            top_scores_line = "\n  🎯 " + " | ".join(
                f"{s}({p:.1f}%)" for s, p in poisson_data["top_scores"][:3]
            )
        poisson_str = (
            f"\n{poisson_label}\n"
            f"  🏠 {home_name}: {poisson_data['prob_home_win']}%\n"
            f"  🤝 Empate: {poisson_data['prob_draw']}%\n"
            f"  ✈️ {away_name}: {poisson_data['prob_away_win']}%\n"
            f"  ⚽ Over 2.5: {poisson_data['prob_over25']}%"
            f"{top_scores_line}"
        )

        conf_leader_map = {"home": f"🏠 {home_name}", "draw": "🤝 Empate", "away": f"✈️ {away_name}"}
        conf_leader = conf_leader_map.get(confidence_score.get("leader", ""), "?")
        confidence_str = (
            f"\n🧠 *{'Señal de mercado' if stats_limited else 'Confianza combinada'}:*\n"
            f"  → {conf_leader} | {confidence_score.get('confidence', 0)}%"
            f"{' (solo cuotas)' if stats_limited else ''}\n"
            f"  {home_name} {confidence_score.get('home',0):.0f}% | "
            f"X {confidence_score.get('draw',0):.0f}% | "
            f"{away_name} {confidence_score.get('away',0):.0f}%"
        )

        h2h_str = ""
        if h2h:
            h2h_str = (
                f"\n⚔️ *H2H últimos {h2h.get('total',0)} partidos:*\n"
                f"  {home_name} {h2h.get('home_wins',0)} | X {h2h.get('draws',0)} | {away_name} {h2h.get('away_wins',0)}"
            )

        def _o(val):
            return str(round(float(val), 2)) if val is not None else "-"

        odds_str = ""
        if odds.get("home_win"):
            odds_str = (
                f"\n💰 *Cuotas:*\n"
                f"  1: {_o(odds.get('home_win'))} | X: {_o(odds.get('draw'))} | 2: {_o(odds.get('away_win'))}\n"
                f"  Over 2.5: {_o(odds.get('over_25'))} | BTTS: {_o(odds.get('btts_yes'))}"
            )

        movement_str = ""
        if odds_movement and odds_movement.get("movements"):
            alert = "🚨 " if odds_movement.get("alert") else ""
            movement_str = (
                f"\n📊 *Movimiento de cuotas {alert}:*\n"
                + "\n".join(odds_movement["movements"])
            )

        value_str = ""
        _prob_thresholds = {
            "Victoria " + home_name: poisson_data.get("prob_home_win", 0),
            "Empate":                poisson_data.get("prob_draw", 0),
            "Victoria " + away_name: poisson_data.get("prob_away_win", 0),
            "Over 2.5":              poisson_data.get("prob_over25", 0),
        }
        if (best_value[1] and best_value[1] >= 7.0
                and _prob_thresholds.get(best_value[0], 0) >= 25):
            value_str = f"\n💎 *Valor detectado:* {best_value[0]} (+{best_value[1]:.1f}% EV)"

        # Kelly Criterion
        kelly_str = ""
        kelly_h = kelly_criterion(poisson_data.get("prob_home_win", 0), odds.get("home_win"))
        kelly_d = kelly_criterion(poisson_data.get("prob_draw", 0),     odds.get("draw"))
        kelly_a = kelly_criterion(poisson_data.get("prob_away_win", 0), odds.get("away_win"))
        k_parts = []
        if kelly_h: k_parts.append(f"{home_name}: {kelly_h}% bankroll")
        if kelly_d: k_parts.append(f"Empate: {kelly_d}% bankroll")
        if kelly_a: k_parts.append(f"{away_name}: {kelly_a}% bankroll")
        if k_parts:
            kelly_str = "\n💸 *Kelly (tamaño apuesta):* " + " | ".join(k_parts)

        # Conflicto mercado vs modelo
        conflict_str = ""
        conflicts = market_vs_model_conflict(poisson_data, odds, sport_key)
        if conflicts:
            c_lines = []
            for c in conflicts:
                c_lines.append(
                    f"  {c['severity']} {c['outcome']}: modelo {c['model']}% vs mercado {c['market']}% "
                    f"(Δ{c['diff']}%) → confiar en {c['trust']}"
                )
            conflict_str = "\n🔀 *Modelo vs Mercado:*\n" + "\n".join(c_lines)

        # Eficiencia del mercado
        eff_label = get_league_efficiency_label(sport_key)
        efficiency_str = f"\n📡 *{eff_label}*" if sport_key else ""

        injuries_str = format_injuries(home_injuries, home_name) + format_injuries(away_injuries, away_name)
        weather_str  = format_weather(weather_data)

        coach_str = ""
        if home_coach_info or away_coach_info:
            def _cl(name, c):
                if not c or not c.get("name"):
                    return f"  {name}: Sin datos"
                since = f" desde {c['start']}" if c.get("start") else ""
                return f"  {name}: {c['name']}{since}"
            coach_str = f"\n👔 *Entrenadores:*\n{_cl(home_name,home_coach_info)}\n{_cl(away_name,away_coach_info)}"

        cup_str = ""
        cup_lines = []
        if home_cup: cup_lines.append(f"  {home_name}: {home_cup.get('warning','')}")
        if away_cup: cup_lines.append(f"  {away_name}: {away_cup.get('warning','')}")
        if cup_lines:
            cup_str = "\n⚠️ *Desgaste reciente (copa):*\n" + "\n".join(cup_lines)

        ht_str = ""
        ht_lines = []
        for name, ht in [(home_name, home_ht), (away_name, away_ht)]:
            if ht and ht.get("games", 0) >= 3:
                wl = ""
                if ht.get("win_when_leading_ht_pct") is not None:
                    wl = f" | Gana {ht['win_when_leading_ht_pct']}% yendo arriba al descanso"
                    if ht.get("collapse_risk"): wl += " ⚠️"
                ht_lines.append(
                    f"  {name}: 1T {ht['avg_1h_scored']} GF/{ht['avg_1h_conceded']} GC | "
                    f"2T {ht['avg_2h_scored']} GF/{ht['avg_2h_conceded']} GC{wl}"
                )
        if ht_lines:
            ht_str = "\n⏱️ *Por mitad (promedio):*\n" + "\n".join(ht_lines)

        limited_note = f"\n⚠️ _{stats_source_note}_" if stats_limited else ""

        final_text = (
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"⚽ *{home_name}* vs *{away_name}*\n"
            f"🏆 {competition} | 🕐 {time_cuba}"
            f"{limited_note}\n"
            f"━━━━━━━━━━━━━━━━━━━━"
            f"{tabla_str}"
            f"{coach_str}"
            f"{racha_str}"
            f"{fatigue_str}"
            f"{cup_str}"
            f"{referee_str}"
            f"{susp_str}"
            f"{injuries_str}"
            f"{weather_str}"
            f"{xg_str}"
            f"{role_str}"
            f"{ht_str}"
            f"{poisson_str}"
            f"{confidence_str}"
            f"{form_str}"
            f"{defense_str}"
            f"{h2h_str}"
            f"{odds_str}"
            f"{movement_str}"
            f"{efficiency_str}"
            f"{conflict_str}"
            f"{kelly_str}"
            f"{value_str}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🤖 *ANÁLISIS IA:*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{ai_result}"
        )
        if auto_bet_info:
            _bal = get_balance()
            final_text += (
                f"\n\n━━━━━━━━━━━━━━━━━━━━\n"
                f"🤖 *AUTO-BET REGISTRADO:*\n"
                f"💵 Apuesta: *${auto_bet_info['stake']:.2f}* @ {auto_bet_info['odds']}\n"
                f"🎯 Pick: {auto_bet_info['pick_label']}\n"
                f"💰 Ganancia potencial: *${auto_bet_info['potential_win']:.2f}*\n"
                f"🏦 Balance actual: *${_bal:.2f}*"
            )

        analyzed_matches.add(match_id)
        await msg.edit_text(final_text, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Error analyzing match: {e}", exc_info=True)
        await msg.edit_text(f"❌ Error al analizar.\n`{str(e)[:200]}`", parse_mode="Markdown")


async def cmd_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _cmd_pick_from_cache(update, context, cache=matches_cache, label="pick")


async def cmd_pick_manana(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _cmd_pick_from_cache(update, context, cache=tomorrow_cache, label="pick_manana")


async def cmd_historial(update: Update, context: ContextTypes.DEFAULT_TYPE):
    picks = get_history(15)
    if not picks:
        await update.message.reply_text(
            "📊 *Historial de picks*\n\n_Aún no hay picks guardados._",
            parse_mode="Markdown"
        )
        return
    lines = ["📊 *HISTORIAL DE PICKS* (últimos 15)\n"]
    for p in picks:
        date_str = (p.get("created_at") or "")[:10]
        home     = p.get("home_team", "?")
        away     = p.get("away_team", "?")
        pick     = p.get("pick_main") or "Sin pick"
        conf     = p.get("confidence")
        conf_str = f" ({conf}%)" if conf else ""
        correct  = p.get("pick_correct")
        rh, ra   = p.get("result_home"), p.get("result_away")
        if correct == 1:   result_icon, result_str = "✅", f" → {rh}-{ra}"
        elif correct == 0: result_icon, result_str = "❌", f" → {rh}-{ra}"
        else:              result_icon, result_str = "⏳", ""
        lines.append(f"{result_icon} `{date_str}` *{home}* vs *{away}*")
        lines.append(f"   Pick: _{pick}_{conf_str}{result_str}")
        lines.append("")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stats = db_get_stats()
    if not stats or stats.get("total", 0) == 0:
        await update.message.reply_text("📈 *Estadísticas*\n\n_Aún no hay picks guardados._", parse_mode="Markdown")
        return
    acc      = stats.get("accuracy", 0)
    hc_acc   = stats.get("hc_accuracy", 0)
    clv_avg  = stats.get("clv_avg")
    acc_icon = "🔥" if acc >= 65 else "📊" if acc >= 50 else "📉"
    hc_icon  = "🔥" if hc_acc >= 70 else "📊" if hc_acc >= 55 else "📉"

    clv_line = ""
    if clv_avg is not None:
        clv_icon = "📈" if clv_avg > 1 else "➡️" if clv_avg >= -1 else "⚠️"
        clv_line = f"\n{clv_icon} *CLV promedio: {clv_avg:+.2f}%* ({'picks inteligentes' if clv_avg > 0 else 'mejorar selección'})"

    text = (
        f"📈 *ESTADÍSTICAS DE PICKS*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 Total picks: {stats['total']}\n"
        f"✅ Acertados: {stats['correct']}\n"
        f"❌ Fallados: {stats['wrong']}\n"
        f"⏳ Pendientes: {stats['pending']}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{acc_icon} *Acierto general: {acc}%*\n"
        f"{hc_icon} *Alta confianza (≥70%): {hc_acc}%* ({stats['hc_correct']}/{stats['hc_total']})"
        f"{clv_line}\n"
        f"━━━━━━━━━━━━━━━━━━━━"
    )

    # Calibración de confianza
    calib = get_calibration_stats()
    if calib:
        calib_lines = ["\n\n🎯 *CALIBRACIÓN (confianza declarada vs acierto real):*"]
        for row in calib:
            bucket   = row.get("bucket", "?")
            real_acc = row.get("real_accuracy")
            total_b  = row.get("total", 0)
            correct_b = row.get("correct", 0)
            if real_acc is None:
                continue
            bucket_mid = int(bucket.split("-")[0].replace("%+", "").replace("%", ""))
            diff = real_acc - bucket_mid
            cal_icon = "✅" if abs(diff) <= 8 else ("📈" if diff > 0 else "📉")
            calib_lines.append(
                f"  {cal_icon} {bucket}: declaré ~{bucket_mid}% → acerté *{real_acc}%* ({correct_b}/{total_b})"
            )
        if len(calib_lines) > 1:
            calib_lines.append("_✅=bien calibrado | 📈=subestimado | 📉=sobreconfiado_")
            text += "\n".join(calib_lines)

    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_ligas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Estadísticas de aciertos por liga."""
    leagues = get_stats_by_league(min_picks=3)
    if not leagues:
        await update.message.reply_text(
            "🏆 *Aciertos por liga*\n\n"
            "_Aún no hay suficientes picks resueltos por liga (mínimo 3)._",
            parse_mode="Markdown"
        )
        return

    lines = ["🏆 *ACIERTOS POR LIGA* (mín. 3 picks resueltos)\n"]
    for row in leagues:
        acc  = row.get("accuracy") or 0
        icon = "🔥" if acc >= 65 else "✅" if acc >= 50 else "⚠️" if acc >= 40 else "❌"
        comp = row.get("competition") or row.get("sport_key", "?")
        total   = row.get("total", 0)
        correct = row.get("correct", 0)
        pending = row.get("pending", 0)
        lines.append(f"{icon} *{comp}*")
        lines.append(f"   {correct}/{total - pending} acertados → *{acc}%*")
        lines.append("")

    lines.append("_Las ligas con más picks son más confiables._")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def _check_results_once(app=None):
    pending = get_pending_picks()
    if not pending:
        return
    logger.info(f"Verificando resultados: {len(pending)} picks pendientes")
    by_sport = {}
    for p in pending:
        sk = p.get("sport_key")
        if sk:
            by_sport.setdefault(sk, []).append(p)

    for sport_key, picks in by_sport.items():
        try:
            scores = await asyncio.to_thread(get_scores_for_sport, sport_key, 2)
            for ev in scores:
                if not ev.get("completed"):
                    continue
                scores_list = ev.get("scores") or []
                if len(scores_list) < 2:
                    continue
                home_api = ev.get("home_team", "")
                away_api = ev.get("away_team", "")
                for pick in picks:
                    if pick.get("pick_correct") is not None:
                        continue
                    if not (name_matches(pick["home_team"], home_api) and name_matches(pick["away_team"], away_api)):
                        continue
                    home_score = away_score = None
                    for s in scores_list:
                        if name_matches(s.get("name", ""), home_api):
                            try: home_score = int(s["score"])
                            except: pass
                        elif name_matches(s.get("name", ""), away_api):
                            try: away_score = int(s["score"])
                            except: pass
                    if home_score is not None and away_score is not None:
                        correct = determine_correct(pick.get("pick_main",""), pick["home_team"], pick["away_team"], home_score, away_score)
                        if correct is not None:
                            update_pick_result(pick["id"], home_score, away_score, correct)
                            logger.info(f"{'✅' if correct else '❌'} {pick['home_team']} {home_score}-{away_score} {pick['away_team']}")
                        save_match_to_cache(
                            home_team=pick["home_team"], away_team=pick["away_team"],
                            home_score=home_score, away_score=away_score,
                            match_date=pick.get("match_date",""), competition=pick.get("competition",""),
                            sport_key=sport_key, source="odds_api_scores",
                        )
        except Exception as e:
            logger.error(f"Error verificando resultados {sport_key}: {e}")


async def results_loop(app=None):
    await asyncio.sleep(300)
    while True:
        try:
            await _check_results_once(app)
        except Exception as e:
            logger.error(f"Error en results_loop: {e}")
        await asyncio.sleep(7200)


def _evaluate_match_signal(event, all_odds):
    home_name   = event.get("homeTeam", {}).get("name", "")
    away_name   = event.get("awayTeam", {}).get("name", "")
    sport_key   = event.get("_sport_key", "")
    competition = event.get("competition", {}).get("name", "")
    time_cuba   = utc_to_cuba(event.get("utcDate", ""))
    match_num   = event.get("_num", "?")
    odds = find_odds_for_match(home_name, away_name, all_odds) or {}
    signals = []
    for label, odds_val, threshold in [
        (f"Victoria {home_name}", odds.get("home_win"), 0.62),
        (f"Victoria {away_name}", odds.get("away_win"), 0.62),
        ("Over 2.5 goles",        odds.get("over_25"),  0.68),
        ("Under 2.5 goles",       odds.get("under_25"), 0.68),
        ("BTTS No",               odds.get("btts_no"),  0.68),
    ]:
        if odds_val and odds_val > 1.01:
            imp = 1 / odds_val
            if imp >= threshold:
                signals.append({"label": label, "odds": odds_val, "prob": round(imp * 100, 1), "strength": imp})
    if not signals:
        return None
    best = max(signals, key=lambda s: s["strength"])
    prob = best["prob"]
    return {
        "home": home_name, "away": away_name, "competition": competition,
        "time": time_cuba, "match_num": match_num, "signal": best,
        "strength_bar": "🔥🔥🔥" if prob >= 75 else "🔥🔥" if prob >= 68 else "🔥",
        "event_id": event.get("id", ""),
    }


async def scan_and_alert(app):
    subscribers = get_active_subscribers()
    if not subscribers:
        return
    try:
        events, all_odds = await asyncio.gather(
            asyncio.to_thread(get_todays_events),
            asyncio.to_thread(get_all_odds),
        )
    except Exception as e:
        logger.error(f"Error en scan_and_alert: {e}")
        return
    for num, event in enumerate(events, 1):
        event["_num"] = num
    found = []
    for event in events:
        event_id = event.get("id", "")
        if alert_already_sent(event_id):
            continue
        if event.get("status", "") in ("IN_PLAY", "PAUSED", "FINISHED"):
            continue
        result = _evaluate_match_signal(event, all_odds)
        if result:
            found.append(result)
            mark_alert_sent(event_id)
    if not found:
        logger.info("Escaneo: sin señales nuevas.")
        return
    for alert in found:
        sig  = alert["signal"]
        text = (
            f"🚨 *SEÑAL DE ALTA CONFIANZA*\n━━━━━━━━━━━━━━━━━━━━\n"
            f"⚽ *{alert['home']}* vs *{alert['away']}*\n"
            f"🏆 {alert['competition']} | 🕐 {alert['time']}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{alert['strength_bar']} *{sig['label']}*\n"
            f"📊 Prob. mercado: *{sig['prob']}%* | 💰 Cuota: *{sig['odds']}*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"_/pick {alert['match_num']} para análisis completo_\n"
            f"_/alertas para desactivar_"
        )
        for chat_id in subscribers:
            try:
                await app.bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown")
            except Exception as e:
                logger.warning(f"Error alerta a {chat_id}: {e}")


async def alerts_loop(app):
    await asyncio.sleep(60)
    while True:
        try:
            now_cuba = datetime.now(CUBA_TZ)
            if 6 <= now_cuba.hour <= 23:
                await scan_and_alert(app)
            else:
                logger.info("Fuera de horario de alertas (6am-11pm Cuba)")
        except Exception as e:
            logger.error(f"Error en alerts_loop: {e}")
        await asyncio.sleep(10800)


async def cmd_alertas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if is_subscribed(chat_id):
        unsubscribe(chat_id)
        await update.message.reply_text(
            "🔕 *Alertas desactivadas.*\n_Usa /alertas de nuevo para reactivarlas._",
            parse_mode="Markdown"
        )
    else:
        subscribe(chat_id)
        await update.message.reply_text(
            "🔔 *¡Alertas activadas!*\n\n"
            "Te aviso cuando detecte señal fuerte (prob. mercado ≥62%).\n"
            "Escaneo cada 3h entre 6am y 11pm Cuba.\n\n"
            "_Usa /alertas de nuevo para desactivarlas._",
            parse_mode="Markdown"
        )


async def post_init(app):
    asyncio.create_task(results_loop(app))
    asyncio.create_task(alerts_loop(app))
    asyncio.create_task(line_movement_loop(app))
    asyncio.create_task(daily_bankroll_report_loop(app))
    logger.info("Loops de resultados, alertas, movimiento de línea y bankroll iniciados.")


async def cmd_api(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("🔄 Verificando APIs...")

    odds, fd, apifb, groq = await asyncio.gather(
        asyncio.to_thread(check_odds_api),
        asyncio.to_thread(check_football_data),
        asyncio.to_thread(check_apifootball),
        asyncio.to_thread(check_groq),
    )

    lines = ["📡 <b>ESTADO DE APIs</b>\n"]

    # ── The Odds API ──────────────────────────────────────────────────
    lines.append("🎲 <b>The Odds API</b>")
    if not odds.get("ok") and odds.get("keys", 0) == 0:
        lines.append(f"  ❌ {odds.get('error', 'Sin clave configurada')}")
    else:
        lines.append(f"  🔑 {odds.get('keys', 0)} clave(s)")
        for d in odds.get("details", []):
            if d.get("ok"):
                lines.append(f"  ✅ Key {d['key_num']}: Usadas: {d.get('used','?')} | Restantes: {d.get('remaining','?')}{d.get('bar','')}")
            else:
                lines.append(f"  ❌ Key {d['key_num']}: {d.get('error','?')}")
    lines.append("")

    # ── football-data.org ─────────────────────────────────────────────
    lines.append("⚽ <b>football-data.org</b>")
    if not fd.get("ok") and fd.get("keys", 0) == 0:
        lines.append("  ❌ Sin clave configurada")
    else:
        lines.append(f"  🔑 {fd.get('keys', 0)} clave(s)")
        for d in fd.get("details", []):
            if d.get("ok"):
                lines.append(
                    f"  ✅ Key {d['key_num']}: {d.get('available_minute','?')} req/min disponibles"
                )
            else:
                lines.append(f"  ❌ Key {d['key_num']}: {d.get('error','?')}")
    lines.append("")

    # ── API-Football ──────────────────────────────────────────────────
    lines.append("🏟️ <b>API-Football (api-sports.io)</b>")
    if not apifb.get("ok") and apifb.get("keys", 0) == 0:
        lines.append("  ❌ Sin clave configurada")
    else:
        lines.append(f"  🔑 {apifb.get('keys', 0)} clave(s)")
        for d in apifb.get("details", []):
            if d.get("ok"):
                remaining = d.get("remaining", "?")
                bar = ""
                if isinstance(remaining, int) and isinstance(d.get("limit"), int):
                    pct = remaining / d["limit"]
                    bar = " 🟢" if pct > 0.3 else " 🟡" if pct > 0.1 else " 🔴"
                lines.append(
                    f"  ✅ Key {d['key_num']}: {d.get('used','?')}/{d.get('limit','?')} usadas | "
                    f"Restantes: {remaining}{bar}"
                )
            else:
                lines.append(f"  ❌ Key {d['key_num']}: {d.get('error','?')}")
    lines.append("")

    # ── Groq ──────────────────────────────────────────────────────────
    lines.append("🤖 <b>Groq (IA / LLaMA)</b>")
    if not groq.get("ok") and groq.get("keys", 0) == 0:
        lines.append("  ❌ Sin clave configurada")
    else:
        lines.append(f"  🔑 {groq.get('keys', 0)} clave(s)")
        for d in groq.get("details", []):
            if d.get("ok"):
                lines.append(f"  ✅ Key {d['key_num']}: Activa")
            else:
                lines.append(f"  ❌ Key {d['key_num']}: {d.get('error','?')}")

    await msg.edit_text("\n".join(lines), parse_mode="HTML")


def main():
    if not TELEGRAM_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN no encontrado.")
        return
    init_db()
    app = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .post_init(post_init)
        .build()
    )
    app.add_handler(CommandHandler("start",       cmd_start))
    app.add_handler(CommandHandler("partidos",    cmd_partidos))
    app.add_handler(CommandHandler("manana",      cmd_manana))
    app.add_handler(CommandHandler("pick",        cmd_pick))
    app.add_handler(CommandHandler("pick_manana", cmd_pick_manana))
    app.add_handler(CommandHandler("historial",   cmd_historial))
    app.add_handler(CommandHandler("stats",       cmd_stats))
    app.add_handler(CommandHandler("ligas",       cmd_ligas))
    app.add_handler(CommandHandler("alertas",     cmd_alertas))
    app.add_handler(CommandHandler("combinadas",  cmd_combinadas))
    app.add_handler(CommandHandler("rendimiento", cmd_rendimiento))
    app.add_handler(CommandHandler("bankroll",    cmd_bankroll))
    app.add_handler(CommandHandler("api",         cmd_api))
    logger.info("Bot iniciado con todos los módulos activos.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)




# ══════════════════════════════════════════════════════════════════════════
# NOTIFICACIÓN AUTO-BET RESUELTO
# ══════════════════════════════════════════════════════════════════════════

async def _notify_bet_resolved(app, pick: dict, resolved_bets: list,
                                home_score: int, away_score: int):
    """Envía notificación a todos los suscriptores cuando una apuesta auto se resuelve."""
    subscribers = get_active_subscribers()
    if not subscribers:
        return
    balance = get_balance()
    for bet in resolved_bets:
        icon    = "✅ GANADA" if bet["status"] == "won" else "❌ PERDIDA"
        pnl     = bet["pnl"]
        pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
        text = (
            f"🤖 *AUTO-BET {icon}*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"⚽ *{pick['home_team']}* {home_score}-{away_score} *{pick['away_team']}*\n"
            f"🎯 Pick: _{bet['pick_label']}_\n"
            f"💰 Apuesta: ${bet['stake']:.2f} @ {bet['odds']}\n"
            f"📊 Resultado: *{pnl_str}*\n"
            f"🏦 Balance: *${balance:.2f}*\n"
            f"━━━━━━━━━━━━━━━━━━━━"
        )
        for chat_id in subscribers:
            try:
                await app.bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown")
            except Exception as e:
                logger.warning(f"Error notif bet_resolved a {chat_id}: {e}")


# ══════════════════════════════════════════════════════════════════════════
# COMANDO /combinadas — Mejores 2-3 picks del día
# ══════════════════════════════════════════════════════════════════════════

async def cmd_combinadas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Detecta los 2-3 partidos del día con mayor señal y arma una combinada."""
    cache = matches_cache if matches_cache else tomorrow_cache
    if not cache:
        await update.message.reply_text(
            "⚠️ Primero ejecuta /partidos (o /manana) para cargar la lista."
        )
        return

    msg = await update.message.reply_text("🎯 Buscando mejores señales del día...")
    try:
        all_odds = await asyncio.to_thread(get_all_odds)
        candidates = []
        for num, event in cache.items():
            home_name   = event.get("homeTeam", {}).get("name", "")
            away_name   = event.get("awayTeam", {}).get("name", "")
            competition = event.get("competition", {}).get("name", "?")
            time_cuba   = utc_to_cuba(event.get("utcDate", ""))
            sport_key   = event.get("_sport_key", "")
            odds = find_odds_for_match(home_name, away_name, all_odds) or {}
            if not odds:
                continue
            best = None
            for label, odds_val in [
                (f"Victoria {home_name}", odds.get("home_win")),
                (f"Victoria {away_name}", odds.get("away_win")),
                (f"Over 2.5 goles",       odds.get("over_25")),
                (f"Under 2.5 goles",      odds.get("under_25")),
            ]:
                if not odds_val or odds_val <= 1.0:
                    continue
                imp = 1.0 / odds_val
                kelly_pct = kelly_criterion(imp * 100, odds_val)
                if kelly_pct and kelly_pct > 0 and imp >= 0.58:
                    if not best or imp > best["imp"]:
                        best = {
                            "label": label, "odds": odds_val,
                            "imp": imp, "kelly": kelly_pct,
                        }
            if best:
                candidates.append({
                    "num": num, "home": home_name, "away": away_name,
                    "competition": competition, "time": time_cuba,
                    "pick": best,
                })

        if not candidates:
            await msg.edit_text(
                "📭 No se encontraron señales con ventaja matemática positiva hoy.\n"
                "_Intenta de nuevo más tarde cuando actualicen las cuotas._",
                parse_mode="Markdown"
            )
            return

        # Ordenar por Kelly descendente y tomar top 3
        candidates.sort(key=lambda x: x["pick"]["kelly"], reverse=True)
        top = candidates[:3]

        # Probabilidad combinada (producto de probabilidades implícitas)
        combined_prob = 1.0
        for c in top:
            combined_prob *= c["pick"]["imp"]
        combined_odds = round(1.0 / combined_prob, 2) if combined_prob > 0 else 0
        combined_pct  = round(combined_prob * 100, 1)

        balance = get_balance()
        # Kelly para la combinada (mucho más conservador — 10% del Kelly individual)
        kelly_combined = kelly_criterion(combined_pct, combined_odds, fraction=0.10)
        stake_combined = round(balance * kelly_combined / 100, 2) if kelly_combined else 0

        lines = [f"🎯 *COMBINADA DEL DÍA* ({len(top)} picks)\n"]
        for i, c in enumerate(top, 1):
            pk = c["pick"]
            lines.append(
                f"*{i}. {c['home']}* vs *{c['away']}*\n"
                f"   🏆 {c['competition']} | 🕐 {c['time']}\n"
                f"   ✅ *{pk['label']}* @ {pk['odds']:.2f}\n"
                f"   📊 Prob: {round(pk['imp']*100,1)}% | Kelly: {pk['kelly']}% bankroll\n"
                f"   _/pick {c['num']} para análisis completo_\n"
            )

        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"🎰 *Cuota combinada: {combined_odds}*")
        lines.append(f"📊 *Prob. combinada: {combined_pct}%*")
        lines.append(f"🏦 Balance actual: ${balance:.2f}")
        if stake_combined >= 1.0:
            lines.append(f"💸 *Apuesta sugerida: ${stake_combined:.2f}* (Kelly conservador)")
        else:
            lines.append("⚠️ _Combinada con bajo Kelly — considera picks individuales_")
        lines.append("\n_⚠️ Las combinadas multiplican el riesgo. El bot prefiere picks individuales._")

        await msg.edit_text("\n".join(lines), parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Error en cmd_combinadas: {e}", exc_info=True)
        await msg.edit_text(f"❌ Error buscando combinadas.\n`{str(e)[:150]}`", parse_mode="Markdown")


# ══════════════════════════════════════════════════════════════════════════
# COMANDO /rendimiento — ROI desglosado
# ══════════════════════════════════════════════════════════════════════════

async def cmd_rendimiento(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stats   = db_get_stats()
    rend    = get_rendimiento_stats()
    summary = get_bankroll_summary()

    if not stats or stats.get("total", 0) == 0:
        await update.message.reply_text(
            "📉 *Rendimiento*\n\n_Aún no hay suficientes picks resueltos._",
            parse_mode="Markdown"
        )
        return

    lines = ["📉 *RENDIMIENTO DETALLADO*\n━━━━━━━━━━━━━━━━━━━━"]

    # Resumen global
    acc = stats.get("accuracy", 0)
    acc_icon = "🔥" if acc >= 65 else "📊" if acc >= 50 else "📉"
    lines.append(f"{acc_icon} *Acierto global: {acc}%* ({stats['correct']}/{stats['resolved']})")
    lines.append(f"⏳ Pendientes: {stats.get('pending', 0)}\n")

    # Por tipo de pick
    if rend.get("by_type"):
        lines.append("🔢 *Por tipo de pick:*")
        for row in rend["by_type"]:
            acc_t = row.get("acc") or 0
            icon = "✅" if acc_t >= 55 else "⚠️" if acc_t >= 40 else "❌"
            lines.append(
                f"  {icon} {row['tipo']}: {row.get('wins',0)}/{row.get('total',0)} → *{acc_t}%*"
            )
        lines.append("")

    # Por confianza
    if rend.get("by_conf"):
        lines.append("🎯 *Por nivel de confianza:*")
        for row in rend["by_conf"]:
            acc_c  = row.get("acc") or 0
            bucket = row.get("bucket", "?")
            try:
                mid  = int(bucket.split("-")[0].replace("%+","").replace("%",""))
                diff = acc_c - mid
                cal  = "✅" if abs(diff) <= 8 else ("📈" if diff > 0 else "📉")
            except Exception:
                cal = "📊"
            lines.append(
                f"  {cal} {bucket}: {row.get('wins',0)}/{row.get('total',0)} → *{acc_c}%*"
            )
        lines.append("")

    # Top ligas
    if rend.get("top_leagues"):
        lines.append("🏆 *Top ligas (mín. 5 picks):*")
        for row in rend["top_leagues"]:
            acc_l = row.get("acc") or 0
            comp  = row.get("competition", "?")[:30]
            lines.append(f"  🔥 {comp}: {row.get('wins',0)}/{row.get('total',0)} → *{acc_l}%*")
        lines.append("")

    # Bankroll autónomo
    if summary.get("total_bets", 0) > 0:
        p_icon = "📈" if summary.get("profit", 0) >= 0 else "📉"
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"🤖 *Bankroll autónomo:*")
        lines.append(f"  🏦 Balance: ${summary['balance']:.2f} (inicial ${summary['initial']:.2f})")
        lines.append(
            f"  {p_icon} P&L: ${summary['profit']:+.2f} | ROI: {summary['roi']:+.1f}%"
        )
        lines.append(
            f"  📊 {summary['won']}G / {summary['lost']}P / {summary['pending']}⏳ "
            f"({summary.get('win_rate',0):.0f}% ganados)"
        )

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ══════════════════════════════════════════════════════════════════════════
# COMANDO /bankroll — Balance autónomo
# ══════════════════════════════════════════════════════════════════════════

async def cmd_bankroll(update: Update, context: ContextTypes.DEFAULT_TYPE):
    summary = get_bankroll_summary()
    history = get_bankroll_history(10)
    balance = summary.get("balance", 90.0)
    profit  = summary.get("profit", 0.0)
    roi     = summary.get("roi", 0.0)

    p_icon  = "📈" if profit >= 0 else "📉"
    lines   = [
        f"🤖 *BANKROLL AUTÓNOMO*",
        f"━━━━━━━━━━━━━━━━━━━━",
        f"🏦 *Balance: ${balance:.2f}*",
        f"💰 Balance inicial: ${summary.get('initial', 90.0):.2f}",
        f"{p_icon} P&L total: *${profit:+.2f}* | ROI: *{roi:+.1f}%*",
        f"━━━━━━━━━━━━━━━━━━━━",
        f"📊 Apuestas: {summary.get('won',0)}✅ {summary.get('lost',0)}❌ {summary.get('pending',0)}⏳",
        f"💵 Total apostado: ${summary.get('total_staked', 0):.2f}",
        f"━━━━━━━━━━━━━━━━━━━━",
    ]

    if history:
        lines.append("📋 *Últimas 10 apuestas:*")
        for bet in history:
            status = bet.get("status", "pending")
            icon   = "✅" if status == "won" else "❌" if status == "lost" else "⏳"
            pnl    = bet.get("pnl")
            pnl_str = f" (${pnl:+.2f})" if pnl is not None else ""
            date_s = (bet.get("created_at") or "")[:10]
            lines.append(
                f"{icon} `{date_s}` _{bet.get('pick_label','?')[:35]}_\n"
                f"   ${bet.get('stake',0):.2f} @ {bet.get('odds',0):.2f}{pnl_str}"
            )
    else:
        lines.append("_Todavía no hay apuestas registradas._\n_Las apuestas se registran automáticamente con cada /pick._")

    lines.append(f"\n_Reglas: máx 12% por apuesta | máx 20% por día_")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ══════════════════════════════════════════════════════════════════════════
# LOOP: MOVIMIENTO DE LÍNEA — alerta si cuota cambia >10%
# ══════════════════════════════════════════════════════════════════════════

async def _check_line_movements(app):
    """Compara cuotas actuales con snapshots guardados. Alerta si cambian >10%."""
    cache = matches_cache
    if not cache:
        return
    subscribers = get_active_subscribers()
    if not subscribers:
        return
    try:
        all_odds = await asyncio.to_thread(get_all_odds)
    except Exception as e:
        logger.warning(f"_check_line_movements: error fetching odds: {e}")
        return

    alerts_sent = []
    for num, event in cache.items():
        home_name = event.get("homeTeam", {}).get("name", "")
        away_name = event.get("awayTeam", {}).get("name", "")
        match_id  = event.get("id")
        status    = event.get("status", "")
        if status in ("FINISHED", "IN_PLAY", "PAUSED"):
            continue
        current_odds = find_odds_for_match(home_name, away_name, all_odds) or {}
        if not current_odds:
            continue
        movement = get_odds_movement(match_id, current_odds)
        if movement and movement.get("alert"):
            time_cuba   = utc_to_cuba(event.get("utcDate", ""))
            competition = event.get("competition", {}).get("name", "?")
            text = (
                f"📊 *MOVIMIENTO DE CUOTA SIGNIFICATIVO*\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"⚽ *{home_name}* vs *{away_name}*\n"
                f"🏆 {competition} | 🕐 {time_cuba}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
            )
            for mv in movement["movements"]:
                text += f"{mv}\n"
            text += f"━━━━━━━━━━━━━━━━━━━━\n"
            text += f"_/pick {num} para análisis completo_"
            alerts_sent.append(f"{home_name} vs {away_name}")
            # Save new snapshot
            save_odds_snapshot(match_id, home_name, away_name, current_odds)
            for chat_id in subscribers:
                try:
                    await app.bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown")
                except Exception as e:
                    logger.warning(f"Error línea móvil a {chat_id}: {e}")
    if alerts_sent:
        logger.info(f"Alertas movimiento línea enviadas: {alerts_sent}")


async def line_movement_loop(app):
    await asyncio.sleep(180)
    while True:
        try:
            now_cuba = datetime.now(CUBA_TZ)
            if 7 <= now_cuba.hour <= 23:
                await _check_line_movements(app)
            else:
                logger.info("line_movement_loop: fuera de horario.")
        except Exception as e:
            logger.error(f"Error en line_movement_loop: {e}")
        await asyncio.sleep(7200)


# ══════════════════════════════════════════════════════════════════════════
# LOOP: REPORTE DIARIO BANKROLL a las 23:00 Cuba
# ══════════════════════════════════════════════════════════════════════════

async def _send_daily_bankroll_report(app):
    subscribers = get_active_subscribers()
    if not subscribers:
        return
    summary = get_bankroll_summary()
    if summary.get("total_bets", 0) == 0:
        return

    profit = summary.get("profit", 0.0)
    roi    = summary.get("roi", 0.0)
    p_icon = "📈" if profit >= 0 else "📉"
    today_str = datetime.now(CUBA_TZ).strftime("%d/%m/%Y")

    text = (
        f"🤖 *REPORTE DIARIO — {today_str}*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🏦 *Balance: ${summary['balance']:.2f}*\n"
        f"{p_icon} P&L total: ${profit:+.2f} | ROI: {roi:+.1f}%\n"
        f"📊 {summary['won']}✅ {summary['lost']}❌ {summary['pending']}⏳\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
    )
    # Today's bets
    import zoneinfo
    cuba = zoneinfo.ZoneInfo("America/Havana")
    today_date = datetime.now(cuba).date().isoformat()
    history = get_bankroll_history(30)
    today_bets = [b for b in history if (b.get("created_at") or "")[:10] == today_date]
    if today_bets:
        text += "*Apuestas de hoy:*\n"
        for bet in today_bets:
            icon = "✅" if bet["status"] == "won" else "❌" if bet["status"] == "lost" else "⏳"
            pnl  = bet.get("pnl")
            pnl_str = f" ${pnl:+.2f}" if pnl is not None else ""
            text += f"  {icon} _{bet.get('pick_label','?')[:35]}_ ${bet.get('stake',0):.2f}{pnl_str}\n"
    else:
        text += "_Sin apuestas hoy._\n"

    for chat_id in subscribers:
        try:
            await app.bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown")
        except Exception as e:
            logger.warning(f"Error reporte diario a {chat_id}: {e}")


async def daily_bankroll_report_loop(app):
    while True:
        now_cuba = datetime.now(CUBA_TZ)
        target   = now_cuba.replace(hour=23, minute=0, second=0, microsecond=0)
        if now_cuba >= target:
            target += timedelta(days=1)
        wait_secs = (target - now_cuba).total_seconds()
        logger.info(f"daily_bankroll_report_loop: próximo reporte en {wait_secs/3600:.1f}h")
        await asyncio.sleep(wait_secs)
        try:
            await _send_daily_bankroll_report(app)
        except Exception as e:
            logger.error(f"Error en daily_bankroll_report_loop: {e}")


if __name__ == "__main__":
    main()
