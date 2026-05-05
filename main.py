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
                      get_xg_from_matches, odds_range_performance,
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
                get_stats_by_league, get_calibration_stats, save_closing_odds)
from odds_tracker import save_odds_snapshot, get_odds_movement, get_closing_odds, calculate_clv, get_clv_label
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
    """Escapa caracteres especiales de Telegram Markdown en texto variable (_, *, `, [)."""
    if not text:
        return ""
    return (str(text)
            .replace("_", "\\_")
            .replace("*", "\\*")
            .replace("`", "\\`")
            .replace("[", "\\["))


def format_match_list(matches, title="HOY"):
    if not matches:
        return (
            f"⏰ No hay partidos disponibles {title.lower()}.\n\n"
            "Revisa más tarde o verifica tu ODDS_API_KEY."
        )
    emoji = "🌅" if title == "MAÑANA" else "⚽"
    lines = [f"{emoji} PARTIDOS {title} 🇨🇺\n"]
    for i, m in enumerate(matches, 1):
        raw_home = m.get("homeTeam", {}).get("shortName") or m.get("homeTeam", {}).get("name", "?")
        raw_away = m.get("awayTeam", {}).get("shortName") or m.get("awayTeam", {}).get("name", "?")
        raw_comp = m.get("competition", {}).get("name") or ""
        home     = _esc(raw_home)
        away     = _esc(raw_away)
        comp     = _esc(raw_comp)
        time_cuba = utc_to_cuba(m.get("utcDate", ""))
        status   = m.get("status", "")
        done     = "✅ " if m.get("id") in analyzed_matches else ""
        if status in ("IN_PLAY", "PAUSED"):
            status_icon = "🔴 EN VIVO"
        else:
            status_icon = f"🕐 {time_cuba}"
        lines.append(f"`{i:2d}.` {done}*{home}* vs *{away}*")
        lines.append(f"    🏆 {comp} | {status_icon}")
        lines.append("")
    cmd_hint = "/pick" if title == "HOY" else "/pick\\_manana"
    lines.append(f"_{cmd_hint} <número> para analizar_")
    return "\n".join(lines)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *Bot de Picks Deportivos*\n\n"
        "⚽ /partidos — Partidos de HOY (45+ ligas)\n"
        "🌅 /manana — Partidos de MAÑANA\n"
        "🔍 /pick <n> — Análisis completo con IA\n"
        "🌅 /pick\\_manana <n> — Análisis partido de mañana\n"
        "📊 /historial — Últimos picks con resultados\n"
        "📈 /stats — Estadísticas de aciertos globales\n"
        "🏆 /ligas — Aciertos por liga\n"
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
            text += f"\n_📸 Cuotas guardadas: {saved} partidos_"
        await msg.edit_text(text, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Error getting matches ({title}): {e}", exc_info=True)
        await msg.edit_text(f"❌ Error al cargar los partidos.\n`{str(e)[:150]}`", parse_mode="Markdown")


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

        # ── xG desde partidos SofaScore ──────────────────────────────
        home_xg = get_xg_from_matches(home_matches, home_id) if home_matches and home_id else {}
        away_xg = get_xg_from_matches(away_matches, away_id) if away_matches and away_id else {}

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


async def _check_results_once():
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


async def results_loop():
    await asyncio.sleep(300)
    while True:
        try:
            await _check_results_once()
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
    asyncio.create_task(results_loop())
    asyncio.create_task(alerts_loop(app))
    logger.info("Loops de resultados y alertas iniciados.")


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
    logger.info("Bot iniciado. /partidos /manana /pick /pick_manana /historial /stats /ligas /alertas")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
