import sqlite3
import os
import re
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "picks_history.db")


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS picks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                match_date TEXT,
                home_team TEXT NOT NULL,
                away_team TEXT NOT NULL,
                competition TEXT,
                pick_main TEXT,
                pick_secondary TEXT,
                confidence INTEGER,
                odds_recommended REAL,
                home_odds REAL,
                draw_odds REAL,
                away_odds REAL,
                sport_key TEXT,
                odds_event_id TEXT,
                result_home INTEGER,
                result_away INTEGER,
                pick_correct INTEGER,
                checked_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS subscribers (
                chat_id INTEGER PRIMARY KEY,
                active INTEGER DEFAULT 1,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sent_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL,
                alert_date TEXT NOT NULL,
                UNIQUE(event_id, alert_date)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS match_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                home_team TEXT NOT NULL,
                away_team TEXT NOT NULL,
                home_score INTEGER,
                away_score INTEGER,
                ht_home INTEGER,
                ht_away INTEGER,
                match_date TEXT,
                competition TEXT,
                sport_key TEXT,
                source TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(home_team, away_team, match_date)
            )
        """)
        conn.commit()
    logger.info("DB inicializada correctamente")


def subscribe(chat_id):
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO subscribers (chat_id, active, created_at)
            VALUES (?, 1, ?)
            ON CONFLICT(chat_id) DO UPDATE SET active = 1
        """, (chat_id, now))
        conn.commit()


def unsubscribe(chat_id):
    with get_conn() as conn:
        conn.execute(
            "UPDATE subscribers SET active = 0 WHERE chat_id = ?",
            (chat_id,)
        )
        conn.commit()


def is_subscribed(chat_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT active FROM subscribers WHERE chat_id = ?",
            (chat_id,)
        ).fetchone()
        return bool(row and row["active"])


def get_active_subscribers():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT chat_id FROM subscribers WHERE active = 1"
        ).fetchall()
        return [r["chat_id"] for r in rows]


def mark_alert_sent(event_id):
    today = datetime.now(timezone.utc).date().isoformat()
    try:
        with get_conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO sent_alerts (event_id, alert_date) VALUES (?, ?)",
                (event_id, today)
            )
            conn.commit()
            return True
    except Exception:
        return False


def alert_already_sent(event_id):
    today = datetime.now(timezone.utc).date().isoformat()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM sent_alerts WHERE event_id = ? AND alert_date = ?",
            (event_id, today)
        ).fetchone()
        return row is not None


def save_pick(home_team, away_team, competition, pick_main, pick_secondary,
              confidence, odds_recommended, home_odds, draw_odds, away_odds,
              sport_key=None, odds_event_id=None, match_date=None):
    now = datetime.now(timezone.utc).isoformat()
    try:
        with get_conn() as conn:
            cur = conn.execute("""
                INSERT INTO picks
                (created_at, match_date, home_team, away_team, competition,
                 pick_main, pick_secondary, confidence, odds_recommended,
                 home_odds, draw_odds, away_odds, sport_key, odds_event_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (now, match_date, home_team, away_team, competition,
                  pick_main, pick_secondary, confidence, odds_recommended,
                  home_odds, draw_odds, away_odds, sport_key, odds_event_id))
            conn.commit()
            logger.info(f"Pick guardado ID={cur.lastrowid}: {home_team} vs {away_team} → {pick_main}")
            return cur.lastrowid
    except Exception as e:
        logger.error(f"Error guardando pick: {e}")
        return None


def get_pending_picks():
    try:
        with get_conn() as conn:
            rows = conn.execute("""
                SELECT * FROM picks
                WHERE pick_correct IS NULL
                AND created_at > datetime('now', '-3 days')
                ORDER BY created_at DESC
            """).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"Error obteniendo picks pendientes: {e}")
        return []


def update_pick_result(pick_id, result_home, result_away, pick_correct):
    now = datetime.now(timezone.utc).isoformat()
    try:
        with get_conn() as conn:
            conn.execute("""
                UPDATE picks SET
                    result_home = ?,
                    result_away = ?,
                    pick_correct = ?,
                    checked_at = ?
                WHERE id = ?
            """, (result_home, result_away, pick_correct, now, pick_id))
            conn.commit()
    except Exception as e:
        logger.error(f"Error actualizando resultado pick {pick_id}: {e}")


def get_history(limit=15):
    try:
        with get_conn() as conn:
            rows = conn.execute("""
                SELECT * FROM picks
                ORDER BY created_at DESC
                LIMIT ?
            """, (limit,)).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"Error obteniendo historial: {e}")
        return []


def get_stats():
    try:
        with get_conn() as conn:
            total = conn.execute("SELECT COUNT(*) FROM picks").fetchone()[0]
            resolved = conn.execute(
                "SELECT COUNT(*) FROM picks WHERE pick_correct IS NOT NULL"
            ).fetchone()[0]
            correct = conn.execute(
                "SELECT COUNT(*) FROM picks WHERE pick_correct = 1"
            ).fetchone()[0]
            pending = total - resolved

            hc_total = conn.execute(
                "SELECT COUNT(*) FROM picks WHERE confidence >= 70 AND pick_correct IS NOT NULL"
            ).fetchone()[0]
            hc_correct = conn.execute(
                "SELECT COUNT(*) FROM picks WHERE confidence >= 70 AND pick_correct = 1"
            ).fetchone()[0]

            return {
                "total": total,
                "resolved": resolved,
                "correct": correct,
                "wrong": resolved - correct,
                "pending": pending,
                "accuracy": round(correct / resolved * 100, 1) if resolved > 0 else 0,
                "hc_total": hc_total,
                "hc_correct": hc_correct,
                "hc_accuracy": round(hc_correct / hc_total * 100, 1) if hc_total > 0 else 0,
            }
    except Exception as e:
        logger.error(f"Error obteniendo stats: {e}")
        return {}


def get_stats_by_league(min_picks: int = 3) -> list:
    """
    Devuelve estadísticas de aciertos agrupadas por liga (sport_key).
    Solo incluye ligas con al menos `min_picks` picks resueltos.
    Ordenadas de mayor a menor acierto.
    """
    try:
        with get_conn() as conn:
            rows = conn.execute("""
                SELECT
                    sport_key,
                    competition,
                    COUNT(*) AS total,
                    SUM(CASE WHEN pick_correct = 1 THEN 1 ELSE 0 END) AS correct,
                    SUM(CASE WHEN pick_correct = 0 THEN 1 ELSE 0 END) AS wrong,
                    SUM(CASE WHEN pick_correct IS NULL THEN 1 ELSE 0 END) AS pending,
                    ROUND(
                        100.0 * SUM(CASE WHEN pick_correct = 1 THEN 1 ELSE 0 END) /
                        NULLIF(SUM(CASE WHEN pick_correct IS NOT NULL THEN 1 ELSE 0 END), 0),
                        1
                    ) AS accuracy
                FROM picks
                WHERE sport_key IS NOT NULL
                GROUP BY sport_key
                HAVING (total - pending) >= ?
                ORDER BY accuracy DESC
            """, (min_picks,)).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"Error obteniendo stats por liga: {e}")
        return []


def parse_ai_pick(ai_text, home_name, away_name):
    result = {
        "pick_main": None,
        "pick_secondary": None,
        "confidence": None,
        "odds_recommended": None,
    }
    if not ai_text:
        return result

    for line in ai_text.splitlines():
        line = line.strip()

        if "PICK PRINCIPAL" in line.upper() and result["pick_main"] is None:
            after = line.split(":", 1)[-1].strip()
            after = re.sub(r"[*_`]", "", after).strip()
            result["pick_main"] = after[:100]

        elif "PICK SECUNDARIO" in line.upper() and result["pick_secondary"] is None:
            after = line.split(":", 1)[-1].strip()
            after = re.sub(r"[*_`]", "", after).strip()
            result["pick_secondary"] = after[:100]

        elif "CONFIANZA" in line.upper() and result["confidence"] is None:
            numbers = re.findall(r"\d+", line)
            if numbers:
                result["confidence"] = int(numbers[0])

        elif "CUOTA RECOMENDADA" in line.upper() and result["odds_recommended"] is None:
            numbers = re.findall(r"\d+\.?\d*", line)
            if numbers:
                try:
                    result["odds_recommended"] = float(numbers[0])
                except Exception:
                    pass

    return result


def save_match_to_cache(
    home_team: str, away_team: str,
    home_score, away_score,
    match_date: str, competition: str = "",
    sport_key: str = "", source: str = "auto",
    ht_home=None, ht_away=None,
):
    now = datetime.now(timezone.utc).isoformat()
    try:
        with get_conn() as conn:
            conn.execute("""
                INSERT OR IGNORE INTO match_history
                (home_team, away_team, home_score, away_score, ht_home, ht_away,
                 match_date, competition, sport_key, source, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                home_team, away_team,
                home_score, away_score, ht_home, ht_away,
                match_date, competition, sport_key, source, now,
            ))
            conn.commit()
    except Exception as e:
        logger.debug(f"save_match_to_cache error: {e}")


def get_team_matches_from_cache(team_name: str, last: int = 20) -> list:
    name_lower = f"%{team_name.lower()}%"
    try:
        with get_conn() as conn:
            rows = conn.execute("""
                SELECT * FROM match_history
                WHERE lower(home_team) LIKE ? OR lower(away_team) LIKE ?
                ORDER BY match_date DESC
                LIMIT ?
            """, (name_lower, name_lower, last)).fetchall()
        result = []
        for r in rows:
            result.append({
                "id": r["id"],
                "utcDate": r["match_date"] or "",
                "status": "FINISHED",
                "homeTeam": {"id": None, "name": r["home_team"]},
                "awayTeam": {"id": None, "name": r["away_team"]},
                "score": {
                    "fullTime": {"home": r["home_score"], "away": r["away_score"]},
                    "halfTime": {"home": r["ht_home"], "away": r["ht_away"]},
                },
                "competition": {"id": None, "name": r["competition"] or "", "type": ""},
                "_source": "local_cache",
            })
        return result
    except Exception as e:
        logger.debug(f"get_team_matches_from_cache error: {e}")
        return []


def get_cache_team_count(team_name: str) -> int:
    name_lower = f"%{team_name.lower()}%"
    try:
        with get_conn() as conn:
            return conn.execute("""
                SELECT COUNT(*) FROM match_history
                WHERE lower(home_team) LIKE ? OR lower(away_team) LIKE ?
            """, (name_lower, name_lower)).fetchone()[0]
    except Exception:
        return 0


def name_matches(a, b):
    a = a.lower().strip()
    b = b.lower().strip()
    return a in b or b in a or a.split()[0] in b or b.split()[0] in a


def determine_correct(pick_main, home_team, away_team, result_home, result_away):
    if result_home is None or result_away is None:
        return None
    pick_lower = (pick_main or "").lower()

    if "empate" in pick_lower or "draw" in pick_lower:
        return 1 if result_home == result_away else 0

    if name_matches(home_team, pick_lower) or "local" in pick_lower or "1x2" in pick_lower:
        if "victoria" in pick_lower or "win" in pick_lower or "gana" in pick_lower:
            return 1 if result_home > result_away else 0

    if name_matches(away_team, pick_lower) or "visitante" in pick_lower:
        if "victoria" in pick_lower or "win" in pick_lower or "gana" in pick_lower:
            return 1 if result_away > result_home else 0

    if name_matches(home_team, pick_lower):
        return 1 if result_home > result_away else 0
    if name_matches(away_team, pick_lower):
        return 1 if result_away > result_home else 0

    return None
