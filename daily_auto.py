"""
daily_auto.py — Envío automático de picks diarios por Telegram.
Se ejecuta via GitHub Actions sin intervención del usuario.
"""
import os
import asyncio
import logging
import requests
from datetime import datetime, timezone, timedelta

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
CUBA_TZ = timezone(timedelta(hours=-4))


def send_telegram(text: str, parse_mode: str = "Markdown") -> bool:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logger.error("Faltan TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        resp = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }, timeout=15)
        if resp.status_code == 200:
            logger.info("Mensaje enviado a Telegram OK")
            return True
        else:
            logger.error(f"Telegram error {resp.status_code}: {resp.text[:200]}")
            return False
    except Exception as e:
        logger.error(f"Error enviando a Telegram: {e}")
        return False


def utc_to_cuba(utc_str: str) -> str:
    if not utc_str:
        return "?"
    try:
        dt = datetime.fromisoformat(utc_str.replace("Z", "+00:00"))
        return dt.astimezone(CUBA_TZ).strftime("%I:%M %p")
    except Exception:
        return utc_str[:16]


async def run():
    from odds_api import get_todays_events, get_all_odds, find_odds_for_match
    from analyzer import kelly_criterion

    now_cuba = datetime.now(CUBA_TZ)
    fecha = now_cuba.strftime("%d/%m/%Y")
    logger.info(f"Iniciando picks automáticos del {fecha}")

    # Cabecera del mensaje
    send_telegram(
        f"🤖 *PICKS AUTOMÁTICOS DEL DÍA*\n"
        f"📅 {fecha} | 🇨🇺 Hora Cuba\n"
        f"━━━━━━━━━━━━━━━━━━━━"
    )

    try:
        events = await asyncio.to_thread(get_todays_events)
        all_odds = await asyncio.to_thread(get_all_odds)
    except Exception as e:
        send_telegram(f"❌ Error obteniendo datos: `{str(e)[:200]}`")
        return

    if not events:
        send_telegram("⚠️ No se encontraron partidos hoy. Las APIs pueden estar sin datos aún.")
        return

    logger.info(f"Encontrados {len(events)} partidos")

    candidates = []
    for num, event in enumerate(events, 1):
        home_name = event.get("homeTeam", {}).get("name", "") or event.get("home_team", "")
        away_name = event.get("awayTeam", {}).get("name", "") or event.get("away_team", "")
        competition = event.get("competition", {}).get("name", "") or event.get("sport_title", "?")
        time_cuba = utc_to_cuba(event.get("utcDate", "") or event.get("commence_time", ""))

        odds = find_odds_for_match(home_name, away_name, all_odds) or {}
        if not odds:
            continue

        best = None
        for label, odds_val in [
            (f"Victoria {home_name}", odds.get("home_win")),
            (f"Victoria {away_name}", odds.get("away_win")),
            ("Over 2.5 goles",        odds.get("over_25")),
            ("Under 2.5 goles",       odds.get("under_25")),
        ]:
            if not odds_val or odds_val <= 1.0:
                continue
            imp = 1.0 / odds_val
            kelly_pct = kelly_criterion(imp * 100, odds_val)
            if kelly_pct and kelly_pct > 0 and imp >= 0.55:
                if not best or imp > best["imp"]:
                    best = {
                        "label": label, "odds": odds_val,
                        "imp": imp, "kelly": kelly_pct,
                    }

        if best:
            candidates.append({
                "num": num,
                "home": home_name,
                "away": away_name,
                "competition": competition,
                "time": time_cuba,
                "pick": best,
            })

    if not candidates:
        send_telegram(
            "📭 *Sin señales con ventaja matemática hoy.*\n"
            "_Las cuotas no muestran valor positivo en ningún partido._"
        )
        return

    # Ordenar por Kelly y tomar top 5
    candidates.sort(key=lambda x: x["pick"]["kelly"], reverse=True)
    top = candidates[:5]

    # Enviar cada pick individual
    for i, c in enumerate(top, 1):
        pk = c["pick"]
        stars = "⭐" * min(5, max(1, int(pk["kelly"] / 2)))
        text = (
            f"⚽ *Pick #{i}* {stars}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"*{c['home']}* vs *{c['away']}*\n"
            f"🏆 {c['competition']} | 🕐 {c['time']}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ *{pk['label']}*\n"
            f"💰 Cuota: *{pk['odds']:.2f}*\n"
            f"📊 Prob. implícita: *{round(pk['imp']*100, 1)}%*\n"
            f"📐 Kelly: *{pk['kelly']}%* del bankroll\n"
        )
        send_telegram(text)
        await asyncio.sleep(0.5)

    # Combinada con top 3
    if len(top) >= 2:
        top3 = top[:3]
        combined_prob = 1.0
        for c in top3:
            combined_prob *= c["pick"]["imp"]
        combined_odds = round(1.0 / combined_prob, 2) if combined_prob > 0 else 0
        combined_pct = round(combined_prob * 100, 1)

        lines = [f"🎯 *COMBINADA SUGERIDA ({len(top3)} picks)*\n"]
        for i, c in enumerate(top3, 1):
            pk = c["pick"]
            lines.append(f"*{i}.* {c['home']} vs {c['away']} — *{pk['label']}* @ {pk['odds']:.2f}")
        lines.append(f"\n🎰 *Cuota combinada: {combined_odds}*")
        lines.append(f"📊 *Prob. combinada: {combined_pct}%*")
        lines.append("\n_⚠️ Las combinadas multiplican el riesgo._")
        send_telegram("\n".join(lines))

    send_telegram(
        f"✅ *Análisis completado — {len(top)} picks enviados*\n"
        f"_Para análisis profundo usa el bot con /pick <n>_"
    )
    logger.info("Picks automáticos enviados correctamente.")


if __name__ == "__main__":
    asyncio.run(run())
