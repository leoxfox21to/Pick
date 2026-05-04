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
