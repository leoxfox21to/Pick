import os
import logging
import requests

logger = logging.getLogger(__name__)


def check_odds_api():
    key = os.environ.get("ODDS_API_KEY", "").strip()
    if not key:
        return {"ok": False, "error": "Sin clave configurada"}
    try:
        resp = requests.get(
            "https://api.the-odds-api.com/v4/sports",
            params={"apiKey": key},
            timeout=10,
        )
        remaining = resp.headers.get("x-requests-remaining", "?")
        used      = resp.headers.get("x-requests-used", "?")
        if resp.status_code == 200:
            return {"ok": True, "remaining": remaining, "used": used}
        elif resp.status_code == 401:
            return {"ok": False, "error": "Clave inválida"}
        else:
            return {"ok": False, "error": f"HTTP {resp.status_code}", "remaining": remaining, "used": used}
    except Exception as e:
        return {"ok": False, "error": str(e)[:80]}


def check_football_data():
    keys = []
    for i in ["1", "2", "3", "4", "5"]:
        k = os.environ.get(f"FOOTBALL_DATA_API_KEY_{i}", "").strip()
        if k:
            keys.append(k)
    fallback = os.environ.get("FOOTBALL_DATA_API_KEY", "").strip()
    if fallback and fallback not in keys:
        keys.append(fallback)

    if not keys:
        return {"ok": False, "error": "Sin clave configurada", "keys": 0}

    results = []
    for idx, key in enumerate(keys, 1):
        try:
            resp = requests.get(
                "https://api.football-data.org/v4/competitions",
                headers={"X-Auth-Token": key},
                timeout=10,
            )
            available = resp.headers.get("X-Requests-Available-Minute", "?")
            counter   = resp.headers.get("X-RequestCounter-Reset", "?")
            if resp.status_code == 200:
                results.append({
                    "key_num": idx,
                    "ok": True,
                    "available_minute": available,
                    "reset_in": counter,
                })
            elif resp.status_code == 429:
                results.append({"key_num": idx, "ok": False, "error": "Límite alcanzado (429)"})
            elif resp.status_code == 403:
                results.append({"key_num": idx, "ok": False, "error": "Clave inválida (403)"})
            else:
                results.append({"key_num": idx, "ok": False, "error": f"HTTP {resp.status_code}"})
        except Exception as e:
            results.append({"key_num": idx, "ok": False, "error": str(e)[:60]})

    return {"ok": True, "keys": len(keys), "details": results}


def check_apifootball():
    keys = []
    for i in ["", "_2", "_3", "_4", "_5"]:
        k = os.environ.get(f"APIFOOTBALL_KEY{i}", "").strip()
        if k:
            keys.append(k)

    if not keys:
        return {"ok": False, "error": "Sin clave configurada", "keys": 0}

    results = []
    for idx, key in enumerate(keys, 1):
        try:
            resp = requests.get(
                "https://v3.football.api-sports.io/status",
                headers={"x-apisports-key": key},
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json().get("response", {})
                if isinstance(data, list):
                    data = data[0] if data else {}
                req  = data.get("requests", {}) if isinstance(data, dict) else {}
                current   = req.get("current", "?")
                limit_day = req.get("limit_day", "?")
                remaining = (limit_day - current) if isinstance(limit_day, int) and isinstance(current, int) else "?"
                results.append({
                    "key_num": idx,
                    "ok": True,
                    "used": current,
                    "limit": limit_day,
                    "remaining": remaining,
                })
            elif resp.status_code == 403:
                results.append({"key_num": idx, "ok": False, "error": "Clave inválida (403)"})
            else:
                results.append({"key_num": idx, "ok": False, "error": f"HTTP {resp.status_code}"})
        except Exception as e:
            results.append({"key_num": idx, "ok": False, "error": str(e)[:60]})

    return {"ok": True, "keys": len(keys), "details": results}


def check_groq():
    keys = []
    for i in ["1", "2", "3"]:
        k = os.environ.get(f"GROQ_API_KEY_{i}", "").strip()
        if k:
            keys.append(k)
    fallback = os.environ.get("GROQ_API_KEY", "").strip()
    if fallback and fallback not in keys:
        keys.append(fallback)

    if not keys:
        return {"ok": False, "error": "Sin clave configurada", "keys": 0}

    results = []
    for idx, key in enumerate(keys, 1):
        try:
            resp = requests.get(
                "https://api.groq.com/openai/v1/models",
                headers={"Authorization": f"Bearer {key}"},
                timeout=10,
            )
            if resp.status_code == 200:
                results.append({"key_num": idx, "ok": True})
            elif resp.status_code == 401:
                results.append({"key_num": idx, "ok": False, "error": "Clave inválida (401)"})
            else:
                results.append({"key_num": idx, "ok": False, "error": f"HTTP {resp.status_code}"})
        except Exception as e:
            results.append({"key_num": idx, "ok": False, "error": str(e)[:60]})

    return {"ok": True, "keys": len(keys), "details": results}
