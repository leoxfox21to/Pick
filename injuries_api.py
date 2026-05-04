import os
import requests
import logging

logger = logging.getLogger(__name__)

RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY", "")
BASE_URL = "https://free-api-live-football-data.p.rapidapi.com"
HEADERS = {
    "x-rapidapi-key": RAPIDAPI_KEY,
    "x-rapidapi-host": "free-api-live-football-data.p.rapidapi.com"
}


def get_team_squad(team_id):
    if not RAPIDAPI_KEY:
        return []
    try:
        url = f"{BASE_URL}/football-get-squad-by-team"
        params = {"teamid": team_id}
        resp = requests.get(url, headers=HEADERS, params=params, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            return data.get("response", {}).get("players", []) or []
        else:
            logger.warning(f"Squad API {team_id}: status {resp.status_code}")
    except Exception as e:
        logger.error(f"Squad API error: {e}")
    return []


def get_match_lineups(match_id):
    if not RAPIDAPI_KEY:
        return None
    try:
        url = f"{BASE_URL}/football-get-lineups-by-match"
        params = {"matchid": match_id}
        resp = requests.get(url, headers=HEADERS, params=params, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            return data.get("response", {})
        else:
            logger.warning(f"Lineups API {match_id}: status {resp.status_code}")
    except Exception as e:
        logger.error(f"Lineups API error: {e}")
    return None


def get_team_injuries(team_id):
    if not RAPIDAPI_KEY:
        return []
    try:
        url = f"{BASE_URL}/football-get-team-injuries"
        params = {"teamid": team_id}
        resp = requests.get(url, headers=HEADERS, params=params, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            injuries = data.get("response", {}).get("injuries", []) or []
            result = []
            for p in injuries:
                name = p.get("name") or p.get("player", {}).get("name", "?")
                reason = p.get("reason") or p.get("type", "Lesión")
                position = p.get("position") or p.get("player", {}).get("position", "")
                result.append({"name": name, "reason": reason, "position": position})
            return result
        else:
            logger.warning(f"Injuries API {team_id}: {resp.status_code} - {resp.text[:200]}")
    except Exception as e:
        logger.error(f"Injuries API error: {e}")
    return []


def format_injuries(injuries, team_name):
    if not injuries:
        return ""
    lines = [f"\n🏥 *Lesionados/Sancionados {team_name}:*"]
    for p in injuries[:5]:
        pos = f" ({p['position']})" if p.get("position") else ""
        lines.append(f"  ❌ {p['name']}{pos} — {p['reason']}")
    if len(injuries) > 5:
        lines.append(f"  _...y {len(injuries)-5} más_")
    return "\n".join(lines)
