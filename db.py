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
                checked_at TEXT,
                closing_home_odds REAL,
                closing_draw_odds REAL,
                closing_away_odds REAL,
                clv REAL
            )
        """)
        # Agregar columnas CLV si la tabla ya existía sin ellas
        for col in ["closing_home_odds REAL", "closing_draw_odds REAL", "closing_away_odds REAL", "clv REAL"]:
            try:
                conn.execute(f"ALTER TABLE picks ADD COLUMN {col}")
            except Exception:
                pass

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
        _init_bankroll_tables(conn)
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


def save_closing_odds(pick_id, closing_home, closing_draw, closing_away):
    """Guarda las cuotas de cierre (justo antes del partido) y calcula el CLV.
    CLV positivo = el pick fue bueno (las cuotas cerraron peor para nosotros = el mercado nos dio la razón).
    CLV negativo = el pick fue malo (el mercado se movió en contra)."""
    if not pick_id:
        return
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT home_odds, draw_odds, away_odds, pick_main, home_team, away_team FROM picks WHERE id = ?",
                (pick_id,)
            ).fetchone()
            if not row:
                return

            clv = None
            pick_lower = (row["pick_main"] or "").lower()
            open_odds = None
            close_odds = None

            if name_matches(row["home_team"], pick_lower) or "local" in pick_lower:
                open_odds  = row["home_odds"]
                close_odds = closing_home
            elif name_matches(row["away_team"], pick_lower) or "visitante" in pick_lower:
                open_odds  = row["away_odds"]
                close_odds = closing_away
            elif "empate" in pick_lower or "draw" in pick_lower:
                open_odds  = row["draw_odds"]
                close_odds = closing_draw

            if open_odds and close_odds and open_odds > 1.0 and close_odds > 1.0:
                # CLV = diferencia en valor esperado entre cuota de apertura y cierre
                clv = round((1 / close_odds - 1 / open_odds) * 100, 2)

            conn.execute("""
                UPDATE picks SET
                    closing_home_odds = ?,
                    closing_draw_odds = ?,
                    closing_away_odds = ?,
                    clv = ?
                WHERE id = ?
            """, (closing_home, closing_draw, closing_away, clv, pick_id))
            conn.commit()
            logger.info(f"CLV guardado para pick {pick_id}: {clv}")
    except Exception as e:
        logger.error(f"Error guardando closing odds: {e}")


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

            clv_avg = conn.execute(
                "SELECT AVG(clv) FROM picks WHERE clv IS NOT NULL"
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
                "clv_avg": round(clv_avg, 2) if clv_avg is not None else None,
            }
    except Exception as e:
        logger.error(f"Error obteniendo stats: {e}")
        return {}


def get_calibration_stats():
    """Compara la confianza declarada del bot con el acierto real.
    Si el bot dice 80% de confianza, ¿gana realmente el 80% de las veces?
    Devuelve buckets: [(rango_conf, picks_totales, aciertos, acierto_real%), ...]"""
    try:
        with get_conn() as conn:
            rows = conn.execute("""
                SELECT
                    CASE
                        WHEN confidence < 60 THEN '50-59%'
                        WHEN confidence < 70 THEN '60-69%'
                        WHEN confidence < 80 THEN '70-79%'
                        WHEN confidence < 90 THEN '80-89%'
                        ELSE '90%+'
                    END AS bucket,
                    COUNT(*) AS total,
                    SUM(CASE WHEN pick_correct = 1 THEN 1 ELSE 0 END) AS correct,
                    ROUND(100.0 * SUM(CASE WHEN pick_correct = 1 THEN 1 ELSE 0 END) /
                        NULLIF(SUM(CASE WHEN pick_correct IS NOT NULL THEN 1 ELSE 0 END), 0), 1) AS real_accuracy
                FROM picks
                WHERE confidence IS NOT NULL AND pick_correct IS NOT NULL
                GROUP BY bucket
                ORDER BY bucket
            """).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"Error obteniendo calibración: {e}")
        return []


def get_stats_by_league(min_picks: int = 3) -> list:
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


# ══════════════════════════════════════════════════════════════
# BANKROLL AUTÓNOMO — balance $90, Kelly automático
# ══════════════════════════════════════════════════════════════

STARTING_BALANCE  = 90.0
MAX_DAILY_EXP_PCT = 0.20   # máx 20% del balance apostado en total por día
MAX_BET_PCT       = 0.12   # máx 12% del balance por apuesta individual
MIN_STAKE         = 1.0    # apuesta mínima $1


def _init_bankroll_tables(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bankroll (
            id              INTEGER PRIMARY KEY CHECK (id = 1),
            balance         REAL    NOT NULL DEFAULT 90.0,
            initial_balance REAL    NOT NULL DEFAULT 90.0,
            updated_at      TEXT    NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bankroll_bets (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            pick_id        INTEGER,
            match_desc     TEXT,
            pick_label     TEXT,
            odds           REAL,
            kelly_pct      REAL,
            stake          REAL,
            balance_before REAL,
            balance_after  REAL,
            pnl            REAL,
            status         TEXT NOT NULL DEFAULT 'pending',
            created_at     TEXT NOT NULL,
            resolved_at    TEXT
        )
    """)
    existing = conn.execute("SELECT id FROM bankroll WHERE id = 1").fetchone()
    if not existing:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO bankroll (id, balance, initial_balance, updated_at) VALUES (1, ?, ?, ?)",
            (STARTING_BALANCE, STARTING_BALANCE, now),
        )


def _ensure_bankroll():
    with get_conn() as conn:
        _init_bankroll_tables(conn)
        conn.commit()


def get_balance() -> float:
    try:
        _ensure_bankroll()
        with get_conn() as conn:
            row = conn.execute("SELECT balance FROM bankroll WHERE id = 1").fetchone()
            return round(row["balance"], 2) if row else STARTING_BALANCE
    except Exception:
        return STARTING_BALANCE


def get_bankroll_row() -> dict:
    try:
        _ensure_bankroll()
        with get_conn() as conn:
            row = conn.execute("SELECT * FROM bankroll WHERE id = 1").fetchone()
            return dict(row) if row else {"balance": STARTING_BALANCE, "initial_balance": STARTING_BALANCE}
    except Exception:
        return {"balance": STARTING_BALANCE, "initial_balance": STARTING_BALANCE}


def get_today_total_staked() -> float:
    import zoneinfo
    cuba = zoneinfo.ZoneInfo("America/Havana")
    today = datetime.now(cuba).date().isoformat()
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(stake), 0) AS total FROM bankroll_bets "
                "WHERE date(created_at) = ? AND status != 'cancelled'",
                (today,),
            ).fetchone()
            return float(row["total"]) if row else 0.0
    except Exception:
        return 0.0


def place_auto_bet(pick_id: int, match_desc: str, pick_label: str,
                   odds: float, kelly_pct: float):
    """Registra una apuesta automática usando Kelly fraccional.
    Devuelve dict con detalles si se apostó, None si se omitió."""
    if not kelly_pct or kelly_pct <= 0 or not odds or odds <= 1.0:
        return None
    _ensure_bankroll()
    balance = get_balance()
    if balance < MIN_STAKE:
        return None

    raw_stake = balance * kelly_pct / 100.0
    stake     = max(MIN_STAKE, min(raw_stake, balance * MAX_BET_PCT))

    today_staked    = get_today_total_staked()
    max_today       = balance * MAX_DAILY_EXP_PCT
    if today_staked >= max_today:
        logger.info(f"Auto-bet omitido: límite diario ${max_today:.2f} ya alcanzado (${today_staked:.2f})")
        return None
    stake = min(stake, max_today - today_staked)
    stake = round(stake, 2)
    if stake < MIN_STAKE:
        return None

    now = datetime.now(timezone.utc).isoformat()
    try:
        with get_conn() as conn:
            cur = conn.execute("""
                INSERT INTO bankroll_bets
                (pick_id, match_desc, pick_label, odds, kelly_pct, stake, balance_before, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)
            """, (pick_id, match_desc, pick_label, odds, kelly_pct, stake, balance, now))
            conn.commit()
            bet_id = cur.lastrowid
        logger.info(f"Auto-bet #{bet_id}: ${stake:.2f} en «{match_desc}» → {pick_label} @ {odds}")
        return {
            "id": bet_id, "stake": stake, "odds": odds,
            "pick_label": pick_label, "match_desc": match_desc,
            "potential_win": round(stake * (odds - 1), 2),
            "balance_before": balance,
        }
    except Exception as e:
        logger.error(f"Error place_auto_bet: {e}")
        return None


def resolve_auto_bets_for_pick(pick_id: int, pick_correct: int):
    """Resuelve todas las apuestas pendientes de un pick cuando llega el resultado.
    Devuelve lista de resultados o None."""
    try:
        _ensure_bankroll()
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM bankroll_bets WHERE pick_id = ? AND status = 'pending'",
                (pick_id,),
            ).fetchall()
        if not rows:
            return None

        resolved = []
        for row in rows:
            bet = dict(row)
            balance_before = get_balance()
            stake = bet["stake"]
            odds  = bet["odds"]
            now   = datetime.now(timezone.utc).isoformat()

            if pick_correct == 1:
                pnl         = round(stake * (odds - 1), 2)
                new_balance = round(balance_before + pnl, 2)
                status      = "won"
            else:
                pnl         = round(-stake, 2)
                new_balance = round(balance_before + pnl, 2)
                status      = "lost"

            with get_conn() as conn:
                conn.execute("""
                    UPDATE bankroll_bets
                    SET status = ?, pnl = ?, balance_after = ?, resolved_at = ?
                    WHERE id = ?
                """, (status, pnl, new_balance, now, bet["id"]))
                conn.execute(
                    "UPDATE bankroll SET balance = ?, updated_at = ? WHERE id = 1",
                    (new_balance, now),
                )
                conn.commit()

            resolved.append({
                **bet, "status": status, "pnl": pnl,
                "balance_before": balance_before, "balance_after": new_balance,
            })
        return resolved if resolved else None
    except Exception as e:
        logger.error(f"Error resolve_auto_bets: {e}")
        return None


def get_bankroll_history(limit: int = 20) -> list:
    try:
        _ensure_bankroll()
        with get_conn() as conn:
            rows = conn.execute("""
                SELECT bb.*, p.home_team, p.away_team
                FROM bankroll_bets bb
                LEFT JOIN picks p ON bb.pick_id = p.id
                ORDER BY bb.created_at DESC
                LIMIT ?
            """, (limit,)).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"Error get_bankroll_history: {e}")
        return []


def get_bankroll_summary() -> dict:
    try:
        _ensure_bankroll()
        with get_conn() as conn:
            br     = conn.execute("SELECT * FROM bankroll WHERE id = 1").fetchone()
            balance = float(br["balance"])   if br else STARTING_BALANCE
            initial = float(br["initial_balance"]) if br else STARTING_BALANCE

            won     = conn.execute("SELECT COUNT(*) FROM bankroll_bets WHERE status='won'").fetchone()[0]
            lost    = conn.execute("SELECT COUNT(*) FROM bankroll_bets WHERE status='lost'").fetchone()[0]
            pending = conn.execute("SELECT COUNT(*) FROM bankroll_bets WHERE status='pending'").fetchone()[0]
            staked  = conn.execute(
                "SELECT COALESCE(SUM(stake),0) FROM bankroll_bets WHERE status IN ('won','lost')"
            ).fetchone()[0]
            total_pnl = conn.execute(
                "SELECT COALESCE(SUM(pnl),0) FROM bankroll_bets WHERE pnl IS NOT NULL"
            ).fetchone()[0]

        roi = round(total_pnl / staked * 100, 1) if staked > 0 else 0.0
        return {
            "balance":      round(balance, 2),
            "initial":      round(initial, 2),
            "profit":       round(balance - initial, 2),
            "roi":          roi,
            "won":          won,
            "lost":         lost,
            "pending":      pending,
            "total_bets":   won + lost + pending,
            "total_staked": round(staked, 2),
            "total_pnl":    round(total_pnl, 2),
            "win_rate":     round(won / (won + lost) * 100, 1) if (won + lost) > 0 else 0.0,
        }
    except Exception as e:
        logger.error(f"Error get_bankroll_summary: {e}")
        return {}


def get_rendimiento_stats() -> dict:
    """Estadísticas avanzadas: ROI por tipo de pick, por confianza, por liga."""
    try:
        with get_conn() as conn:
            # ROI por tipo de pick
            by_type = conn.execute("""
                SELECT
                    CASE
                        WHEN lower(pick_main) LIKE '%empate%' OR lower(pick_main) LIKE '%draw%' THEN 'Empate'
                        WHEN lower(pick_main) LIKE '%visitante%' OR lower(pick_main) LIKE '%away%' THEN 'Visitante'
                        ELSE 'Local'
                    END AS tipo,
                    COUNT(*) AS total,
                    SUM(CASE WHEN pick_correct=1 THEN 1 ELSE 0 END) AS wins,
                    ROUND(100.0*SUM(CASE WHEN pick_correct=1 THEN 1 ELSE 0 END)/
                        NULLIF(SUM(CASE WHEN pick_correct IS NOT NULL THEN 1 ELSE 0 END),0),1) AS acc
                FROM picks
                WHERE pick_correct IS NOT NULL AND pick_main IS NOT NULL
                GROUP BY tipo ORDER BY acc DESC
            """).fetchall()

            # ROI por confianza
            by_conf = conn.execute("""
                SELECT
                    CASE
                        WHEN confidence < 60 THEN '50-59%'
                        WHEN confidence < 70 THEN '60-69%'
                        WHEN confidence < 80 THEN '70-79%'
                        WHEN confidence < 90 THEN '80-89%'
                        ELSE '90%+'
                    END AS bucket,
                    COUNT(*) AS total,
                    SUM(CASE WHEN pick_correct=1 THEN 1 ELSE 0 END) AS wins,
                    ROUND(100.0*SUM(CASE WHEN pick_correct=1 THEN 1 ELSE 0 END)/
                        NULLIF(SUM(CASE WHEN pick_correct IS NOT NULL THEN 1 ELSE 0 END),0),1) AS acc
                FROM picks
                WHERE pick_correct IS NOT NULL AND confidence IS NOT NULL
                GROUP BY bucket ORDER BY bucket
            """).fetchall()

            # Top 3 ligas por acierto (mín 5 picks)
            top_leagues = conn.execute("""
                SELECT competition,
                    COUNT(*) AS total,
                    SUM(CASE WHEN pick_correct=1 THEN 1 ELSE 0 END) AS wins,
                    ROUND(100.0*SUM(CASE WHEN pick_correct=1 THEN 1 ELSE 0 END)/
                        NULLIF(SUM(CASE WHEN pick_correct IS NOT NULL THEN 1 ELSE 0 END),0),1) AS acc
                FROM picks
                WHERE pick_correct IS NOT NULL AND competition IS NOT NULL
                GROUP BY competition HAVING (total) >= 5
                ORDER BY acc DESC LIMIT 3
            """).fetchall()

        return {
            "by_type":    [dict(r) for r in by_type],
            "by_conf":    [dict(r) for r in by_conf],
            "top_leagues":[dict(r) for r in top_leagues],
        }
    except Exception as e:
        logger.error(f"Error get_rendimiento_stats: {e}")
        return {}
