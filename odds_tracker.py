import json
import os
import logging
from datetime import date, datetime

logger = logging.getLogger(__name__)

CACHE_FILE = os.path.join(os.path.dirname(__file__), "odds_cache.json")


def _load_cache():
    if not os.path.exists(CACHE_FILE):
        return {}
    try:
        with open(CACHE_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cache(data):
    try:
        with open(CACHE_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.error(f"Error saving odds cache: {e}")


def save_odds_snapshot(match_id, home_name, away_name, odds):
    if not odds or not odds.get("home_win"):
        return
    cache = _load_cache()
    today = date.today().isoformat()
    key = str(match_id)

    if key not in cache:
        cache[key] = {
            "date": today,
            "home": home_name,
            "away": away_name,
            "snapshots": [],
        }

    cache[key]["snapshots"].append({
        "time": datetime.utcnow().strftime("%H:%M"),
        "home_win": odds.get("home_win"),
        "draw": odds.get("draw"),
        "away_win": odds.get("away_win"),
        "over_25": odds.get("over_25"),
        "btts_yes": odds.get("btts_yes"),
    })

    old_keys = [k for k, v in cache.items() if v.get("date", today) < today]
    for k in old_keys:
        del cache[k]

    _save_cache(cache)


def get_odds_movement(match_id, current_odds):
    cache = _load_cache()
    key = str(match_id)
    entry = cache.get(key)

    if not entry or not entry.get("snapshots"):
        return None

    snapshots = entry["snapshots"]
    if len(snapshots) < 2:
        return None

    first = snapshots[0]
    result = {
        "time_first": first.get("time", "?"),
        "movements": [],
        "alert": False,
        "alert_msg": "",
    }

    markets = [
        ("home_win", "Local gana"),
        ("draw", "Empate"),
        ("away_win", "Visitante gana"),
        ("over_25", "Over 2.5"),
    ]

    alerts = []
    for field, label in markets:
        old_val = first.get(field)
        new_val = current_odds.get(field)
        if old_val and new_val and old_val > 0:
            pct_change = ((new_val - old_val) / old_val) * 100
            if abs(pct_change) >= 5:
                direction = "⬇️" if pct_change < 0 else "⬆️"
                result["movements"].append(
                    f"  {direction} {label}: {old_val:.2f} → {new_val:.2f} ({pct_change:+.1f}%)"
                )
                if abs(pct_change) >= 10:
                    alerts.append(f"{label} movió {pct_change:+.1f}%")

    if alerts:
        result["alert"] = True
        result["alert_msg"] = " | ".join(alerts)

    return result if result["movements"] else None


def get_closing_odds(match_id):
    """Obtiene la última snapshot de cuotas guardada para un partido (cuota de cierre).
    Útil para calcular el CLV después del partido."""
    cache = _load_cache()
    key = str(match_id)
    entry = cache.get(key)
    if not entry or not entry.get("snapshots"):
        return None
    last = entry["snapshots"][-1]
    return {
        "home_win": last.get("home_win"),
        "draw":     last.get("draw"),
        "away_win": last.get("away_win"),
        "time":     last.get("time"),
    }


def calculate_clv(open_odds, close_odds):
    """Calcula el Closing Line Value (CLV).
    CLV positivo = apostamos antes de que el mercado cerrara peor = pick inteligente.
    CLV negativo = el mercado mejoró después de nuestra apuesta = pick cuestionable.
    Retorna el CLV en % de valor esperado ganado/perdido."""
    if not open_odds or not close_odds or open_odds <= 1.0 or close_odds <= 1.0:
        return None
    # CLV = diferencia en probabilidad implícita
    # Si la cuota bajó (mercado más seguro), nosotros conseguimos mejor precio = CLV positivo
    clv = round((1 / close_odds - 1 / open_odds) * 100, 2)
    return clv


def get_clv_label(clv):
    """Etiqueta legible para el CLV."""
    if clv is None:
        return "Sin datos CLV"
    if clv > 3:
        return f"📈 CLV muy positivo (+{clv}%) — pick de alta calidad"
    elif clv > 0:
        return f"✅ CLV positivo (+{clv}%) — pick inteligente"
    elif clv >= -2:
        return f"➡️ CLV neutro ({clv}%) — pick aceptable"
    else:
        return f"⚠️ CLV negativo ({clv}%) — el mercado no avaló este pick"
