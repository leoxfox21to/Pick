import math
from datetime import datetime, timezone
from scipy.stats import poisson


LEAGUE_AVG_GOALS = {
    "soccer_epl": 1.45,
    "soccer_spain_la_liga": 1.30,
    "soccer_germany_bundesliga": 1.55,
    "soccer_italy_serie_a": 1.28,
    "soccer_france_ligue_one": 1.22,
    "soccer_uefa_champs_league": 1.40,
    "soccer_uefa_europa_league": 1.35,
    "soccer_uefa_europa_conference_league": 1.38,
    "soccer_england_efl_champ": 1.42,
    "soccer_england_league1": 1.48,
    "soccer_england_league2": 1.40,
    "soccer_portugal_primeira_liga": 1.30,
    "soccer_netherlands_eredivisie": 1.60,
    "soccer_brazil_campeonato": 1.52,
    "soccer_usa_mls": 1.50,
    "soccer_argentina_primera_division": 1.38,
    "soccer_mexico_ligamx": 1.35,
    "soccer_turkey_super_league": 1.42,
    "soccer_chile_campeonato": 1.35,
    "soccer_colombia_primera_a": 1.33,
    "soccer_ecuador_liga_pro": 1.30,
    "soccer_peru_primera_division": 1.28,
    "soccer_uruguay_primera_division": 1.35,
    "soccer_venezuela_primera": 1.25,
    "soccer_australia_aleague": 1.48,
    "soccer_austria_bundesliga": 1.52,
    "soccer_belgium_first_div": 1.48,
    "soccer_denmark_superliga": 1.45,
    "soccer_greece_super_league": 1.32,
    "soccer_norway_eliteserien": 1.50,
    "soccer_poland_ekstraklasa": 1.35,
    "soccer_scotland_premiership": 1.42,
    "soccer_spain_segunda_division": 1.25,
    "soccer_sweden_allsvenskan": 1.45,
    "soccer_switzerland_superleague": 1.48,
    "soccer_germany_bundesliga2": 1.45,
    "soccer_italy_serie_b": 1.22,
    "soccer_france_ligue_two": 1.20,
    "soccer_conmebol_copa_libertadores": 1.28,
    "soccer_conmebol_copa_sudamericana": 1.25,
    "soccer_saudi_league": 1.45,
    "soccer_japan_j_league": 1.32,
    "soccer_korea_kleague1": 1.38,
    "soccer_russia_premier_league": 1.30,
}
DEFAULT_LEAGUE_AVG = 1.35

# Eficiencia del mercado por liga (0.0 = confiar solo en modelo, 1.0 = confiar solo en mercado)
# Ligas con más liquidez y seguimiento profesional tienen mercados más eficientes.
# Ligas pequeñas son menos eficientes → el modelo propio vale más allí.
LEAGUE_MARKET_EFFICIENCY = {
    "soccer_uefa_champs_league":          0.92,
    "soccer_epl":                         0.90,
    "soccer_spain_la_liga":               0.88,
    "soccer_germany_bundesliga":          0.87,
    "soccer_italy_serie_a":               0.86,
    "soccer_france_ligue_one":            0.85,
    "soccer_uefa_europa_league":          0.84,
    "soccer_uefa_europa_conference_league": 0.80,
    "soccer_portugal_primeira_liga":      0.78,
    "soccer_netherlands_eredivisie":      0.78,
    "soccer_england_efl_champ":           0.76,
    "soccer_scotland_premiership":        0.74,
    "soccer_turkey_super_league":         0.72,
    "soccer_brazil_campeonato":           0.70,
    "soccer_conmebol_copa_libertadores":  0.68,
    "soccer_mexico_ligamx":               0.66,
    "soccer_argentina_primera_division":  0.65,
    "soccer_usa_mls":                     0.65,
    "soccer_saudi_league":                0.62,
    "soccer_chile_campeonato":            0.58,
    "soccer_colombia_primera_a":          0.57,
    "soccer_ecuador_liga_pro":            0.55,
    "soccer_peru_primera_division":       0.54,
    "soccer_uruguay_primera_division":    0.55,
    "soccer_venezuela_primera":           0.50,
    "soccer_conmebol_copa_sudamericana":  0.60,
}
DEFAULT_MARKET_EFFICIENCY = 0.63

MIN_MATCHES_POISSON = 8


def get_league_efficiency(league_key):
    """Retorna la eficiencia del mercado para una liga (0-1).
    Más alto = mercado más confiable que el modelo estadístico."""
    return LEAGUE_MARKET_EFFICIENCY.get(league_key, DEFAULT_MARKET_EFFICIENCY)


def get_league_efficiency_label(league_key):
    """Etiqueta legible de la eficiencia del mercado."""
    eff = get_league_efficiency(league_key)
    if eff >= 0.85:
        return f"🏦 Mercado muy eficiente ({eff*100:.0f}%) — priorizar cuotas sobre modelo"
    elif eff >= 0.75:
        return f"📊 Mercado eficiente ({eff*100:.0f}%) — equilibrar cuotas y modelo"
    elif eff >= 0.65:
        return f"⚖️ Mercado moderado ({eff*100:.0f}%) — modelo y cuotas con peso similar"
    else:
        return f"🔬 Mercado poco eficiente ({eff*100:.0f}%) — el modelo estadístico es más fiable aquí"


def calculate_streak(results):
    if not results:
        return "Sin datos"
    current = results[0]
    count = 0
    for r in results:
        if r == current:
            count += 1
        else:
            break
    labels = {"W": "victorias", "D": "empates", "L": "derrotas"}
    icons  = {"W": "🔥",        "D": "➡️",       "L": "❄️"}
    return f"{icons.get(current,'')} {count} {labels.get(current, '')} seguidas"


def get_motivation(position, total_teams, competition):
    if not position or not total_teams:
        return "Sin datos de posición"
    if position <= 4:
        return f"🏆 Pelea por Champions (#{position})"
    if position <= 6:
        return f"🎯 Zona Europa (#{position})"
    if position >= total_teams - 2:
        return f"🚨 Zona de descenso (#{position}/{total_teams})"
    if position >= total_teams - 5:
        return f"⚠️ Cerca del descenso (#{position}/{total_teams})"
    return f"📊 Posición cómoda (#{position}/{total_teams})"


def days_since_last_match(matches):
    best_diff = None
    for m in matches:
        if m.get("status", "") not in ("FINISHED", ""):
            continue
        date_str = m.get("utcDate", "")
        if not date_str:
            continue
        try:
            match_dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            diff = (now - match_dt).days
            if diff < 0:
                continue
            if best_diff is None or diff < best_diff:
                best_diff = diff
        except Exception:
            continue
    if best_diff is not None and best_diff > 60:
        return None
    return best_diff


def extract_team_stats(matches, team_id):
    goals_scored, goals_conceded, results = [], [], []
    home_goals_scored, home_goals_conceded, home_results = [], [], []
    away_goals_scored, away_goals_conceded, away_results = [], [], []

    for m in matches:
        score = m.get("score", {})
        full = score.get("fullTime", {})
        home_goals = full.get("home")
        away_goals = full.get("away")
        if home_goals is None or away_goals is None:
            continue

        home_id = m.get("homeTeam", {}).get("id")
        is_home = home_id == team_id

        if is_home:
            gf, ga = home_goals, away_goals
            home_goals_scored.append(gf)
            home_goals_conceded.append(ga)
            home_results.append("W" if gf > ga else "D" if gf == ga else "L")
        else:
            gf, ga = away_goals, home_goals
            away_goals_scored.append(gf)
            away_goals_conceded.append(ga)
            away_results.append("W" if gf > ga else "D" if gf == ga else "L")

        goals_scored.append(gf)
        goals_conceded.append(ga)
        results.append("W" if gf > ga else "D" if gf == ga else "L")

    total = len(results)
    if total == 0:
        return {}

    wins  = results.count("W")
    draws = results.count("D")
    losses = results.count("L")

    avg_scored   = sum(goals_scored) / total   if goals_scored   else 0
    avg_conceded = sum(goals_conceded) / total if goals_conceded else 0

    avg_home_scored   = sum(home_goals_scored)   / len(home_goals_scored)   if home_goals_scored   else avg_scored
    avg_home_conceded = sum(home_goals_conceded) / len(home_goals_conceded) if home_goals_conceded else avg_conceded
    avg_away_scored   = sum(away_goals_scored)   / len(away_goals_scored)   if away_goals_scored   else avg_scored
    avg_away_conceded = sum(away_goals_conceded) / len(away_goals_conceded) if away_goals_conceded else avg_conceded

    form_5      = results[:5]
    form_str    = "".join(form_5)
    form_points = sum(3 if r == "W" else 1 if r == "D" else 0 for r in form_5)

    home_form_5   = home_results[:5] if home_results else []
    home_form_str = "".join(home_form_5) if home_form_5 else "N/A"
    home_form_pts = sum(3 if r == "W" else 1 if r == "D" else 0 for r in home_form_5)

    away_form_5   = away_results[:5] if away_results else []
    away_form_str = "".join(away_form_5) if away_form_5 else "N/A"
    away_form_pts = sum(3 if r == "W" else 1 if r == "D" else 0 for r in away_form_5)

    home_win_rate = home_results.count("W") / len(home_results) if home_results else 0
    away_win_rate = away_results.count("W") / len(away_results) if away_results else 0

    clean_sheets      = sum(1 for ga in goals_conceded if ga == 0)
    failed_to_score   = sum(1 for gf in goals_scored   if gf == 0)
    btts_count        = sum(1 for gf, ga in zip(goals_scored, goals_conceded) if gf > 0 and ga > 0)
    over25_count      = sum(1 for gf, ga in zip(goals_scored, goals_conceded) if gf + ga > 2.5)

    elo = calculate_elo(results)

    return {
        "total_matches":         total,
        "wins":                  wins,
        "draws":                 draws,
        "losses":                losses,
        "win_rate":              round(wins / total, 3),
        "avg_scored":            round(avg_scored, 2),
        "avg_conceded":          round(avg_conceded, 2),
        "avg_home_scored":       round(avg_home_scored, 2),
        "avg_home_conceded":     round(avg_home_conceded, 2),
        "avg_away_scored":       round(avg_away_scored, 2),
        "avg_away_conceded":     round(avg_away_conceded, 2),
        "home_win_rate":         round(home_win_rate, 3),
        "away_win_rate":         round(away_win_rate, 3),
        "home_form_5":           home_form_str,
        "home_form_pts":         home_form_pts,
        "away_form_5":           away_form_str,
        "away_form_pts":         away_form_pts,
        "clean_sheets_rate":     round(clean_sheets / total, 3),
        "failed_to_score_rate":  round(failed_to_score / total, 3),
        "form_5":                form_str,
        "form_points_5":         form_points,
        "btts_rate":             round(btts_count / total, 3),
        "over25_rate":           round(over25_count / total, 3),
        "elo":                   elo,
        "results":               results,
    }


def calculate_elo(results, k=32, base=1500):
    elo = base
    for r in reversed(results):
        expected = 1 / (1 + 10 ** ((base - elo) / 400))
        act = 1 if r == "W" else 0.5 if r == "D" else 0
        elo += k * (act - expected)
    return round(elo, 1)


def _dixon_coles_tau(h, a, lh, la, rho=-0.10):
    """Corrección Dixon-Coles para marcadores bajos.
    Mejora la predicción de 0-0, 1-0, 0-1 y 1-1 que Poisson puro subestima/sobreestima."""
    if h == 0 and a == 0:
        return max(0.001, 1 - lh * la * rho)
    elif h == 0 and a == 1:
        return max(0.001, 1 + lh * rho)
    elif h == 1 and a == 0:
        return max(0.001, 1 + la * rho)
    elif h == 1 and a == 1:
        return max(0.001, 1 - rho)
    return 1.0


def poisson_prediction(home_avg_score, away_avg_score, home_avg_concede, away_avg_concede,
                       is_home=True, league_key=None):
    """Calcula probabilidades Poisson con corrección Dixon-Coles para empates y marcadores bajos."""
    league_avg = LEAGUE_AVG_GOALS.get(league_key, DEFAULT_LEAGUE_AVG) if league_key else DEFAULT_LEAGUE_AVG

    home_attack  = home_avg_score   / league_avg if league_avg > 0 else 1.0
    home_defense = home_avg_concede / league_avg if league_avg > 0 else 1.0
    away_attack  = away_avg_score   / league_avg if league_avg > 0 else 1.0
    away_defense = away_avg_concede / league_avg if league_avg > 0 else 1.0

    home_advantage = 1.1 if is_home else 1.0
    lambda_home = max(0.1, min(home_attack * away_defense * league_avg * home_advantage, 5.0))
    lambda_away = max(0.1, min(away_attack * home_defense * league_avg, 5.0))

    max_goals = 8
    prob_home_win = prob_draw = prob_away_win = prob_over25 = prob_btts = 0
    score_matrix = {}
    total_prob = 0.0

    for h in range(max_goals + 1):
        for a in range(max_goals + 1):
            p_raw = poisson.pmf(h, lambda_home) * poisson.pmf(a, lambda_away)
            tau   = _dixon_coles_tau(h, a, lambda_home, lambda_away)
            p     = p_raw * tau
            score_matrix[f"{h}-{a}"] = p
            total_prob += p

    # Normalizar y calcular probabilidades finales
    prob_home_win = prob_draw = prob_away_win = prob_over25 = prob_btts = 0.0
    normalized_matrix = {}
    for score, p in score_matrix.items():
        p_norm = p / total_prob if total_prob > 0 else p
        normalized_matrix[score] = round(p_norm * 100, 2)
        h_g, a_g = map(int, score.split("-"))
        if h_g > a_g:    prob_home_win += p_norm
        elif h_g == a_g: prob_draw     += p_norm
        else:            prob_away_win += p_norm
        if h_g + a_g > 2.5: prob_over25 += p_norm
        if h_g > 0 and a_g > 0: prob_btts += p_norm

    most_likely_score = max(normalized_matrix, key=normalized_matrix.get)
    top_scores = sorted(normalized_matrix.items(), key=lambda x: x[1], reverse=True)[:5]

    return {
        "lambda_home":    round(lambda_home, 2),
        "lambda_away":    round(lambda_away, 2),
        "prob_home_win":  round(prob_home_win  * 100, 1),
        "prob_draw":      round(prob_draw      * 100, 1),
        "prob_away_win":  round(prob_away_win  * 100, 1),
        "prob_over25":    round(prob_over25    * 100, 1),
        "prob_btts":      round(prob_btts      * 100, 1),
        "most_likely_score": most_likely_score,
        "top_scores":     top_scores,
        "league_avg_used": league_avg,
    }


def kelly_criterion(prob_pct, odds, fraction=0.25):
    """Calcula el % del bankroll a apostar según Kelly Criterion fraccional (25%).
    Retorna None si no hay ventaja real (EV negativo).
    fraction=0.25 es conservador — reduce riesgo de ruina."""
    if not odds or odds <= 1.0 or not prob_pct or prob_pct <= 0:
        return None
    prob = prob_pct / 100.0
    b = odds - 1.0
    q = 1.0 - prob
    kelly_full = (b * prob - q) / b
    if kelly_full <= 0:
        return None
    kelly_frac = kelly_full * fraction
    return round(kelly_frac * 100, 1)


def market_vs_model_conflict(poisson_data, odds, league_key=None):
    """Detecta conflictos entre lo que dice el modelo Poisson y lo que dice el mercado.
    Un conflicto grande (>20%) es una señal de alerta importante.
    La eficiencia del mercado de la liga determina a quién darle más peso."""
    if not odds or not poisson_data:
        return None

    efficiency = get_league_efficiency(league_key)
    conflicts = []

    for outcome, prob_key, odds_key, label in [
        ("local",     "prob_home_win", "home_win",  "Victoria local"),
        ("empate",    "prob_draw",     "draw",      "Empate"),
        ("visitante", "prob_away_win", "away_win",  "Victoria visitante"),
    ]:
        model_prob = poisson_data.get(prob_key, 0)
        market_odds = odds.get(odds_key)
        if not market_odds or market_odds <= 1.01:
            continue
        market_prob = round(1 / market_odds * 100, 1)
        diff = abs(model_prob - market_prob)

        if diff >= 15:
            if market_prob > model_prob:
                trust = "mercado"
                icon = "🏦" if efficiency >= 0.80 else "⚖️"
            else:
                trust = "modelo"
                icon = "🔬" if efficiency < 0.70 else "⚖️"

            severity = "🚨 ALTO" if diff >= 25 else "⚠️ MEDIO"
            conflicts.append({
                "outcome":    label,
                "model":      round(model_prob, 1),
                "market":     round(market_prob, 1),
                "diff":       round(diff, 1),
                "trust":      trust,
                "severity":   severity,
                "icon":       icon,
                "efficiency": efficiency,
            })

    return conflicts if conflicts else None


def get_xg_from_matches(matches, team_id) -> dict:
    xg_scored_list    = []
    xg_conceded_list  = []
    goals_scored_list = []

    for m in matches:
        if m.get("_source") != "sofascore":
            continue
        score = m.get("score", {})
        ft    = score.get("fullTime", {})
        xg_h  = score.get("xg_home")
        xg_a  = score.get("xg_away")
        if xg_h is None or xg_a is None:
            continue

        is_home = m.get("homeTeam", {}).get("id") == team_id
        xg_scored_list.append(xg_h   if is_home else xg_a)
        xg_conceded_list.append(xg_a if is_home else xg_h)
        gf = ft.get("home") if is_home else ft.get("away")
        if gf is not None:
            goals_scored_list.append(gf)

    n = len(xg_scored_list)
    if n < 3:
        return {}

    avg_xg_scored   = round(sum(xg_scored_list) / n, 2)
    avg_xg_conceded = round(sum(xg_conceded_list) / n, 2)
    avg_goals       = round(sum(goals_scored_list) / len(goals_scored_list), 2) if goals_scored_list else None
    xg_diff         = round(avg_xg_scored - avg_xg_conceded, 2)

    over_performer = None
    if avg_goals is not None:
        diff = round(avg_goals - avg_xg_scored, 2)
        if diff > 0.3:
            over_performer = f"Marcando MÁS de lo esperado (+{diff} sobre xG) → posible regresión"
        elif diff < -0.3:
            over_performer = f"Marcando MENOS de lo esperado ({diff} vs xG) → podría mejorar"

    return {
        "avg_xg_scored":   avg_xg_scored,
        "avg_xg_conceded": avg_xg_conceded,
        "avg_goals":       avg_goals,
        "xg_diff":         xg_diff,
        "sample_size":     n,
        "over_performer":  over_performer,
    }


def odds_range_performance(matches, team_id, current_odds, is_home) -> dict:
    if not current_odds or current_odds <= 1.0:
        return {}

    low  = current_odds * 0.70
    high = current_odds * 1.30
    results = []

    for m in matches:
        match_odds = m.get("_odds")
        if not match_odds:
            continue
        role_odds = match_odds.get("home_win") if is_home else match_odds.get("away_win")
        if not role_odds:
            continue
        if not (low <= role_odds <= high):
            continue

        score = m.get("score", {}).get("fullTime", {})
        hg = score.get("home")
        ag = score.get("away")
        if hg is None or ag is None:
            continue

        gf = hg if is_home else ag
        ga = ag if is_home else hg
        results.append("W" if gf > ga else "D" if gf == ga else "L")

    n = len(results)
    if n < 3:
        return {}

    win_rate = round(results.count("W") / n * 100, 1)
    role = "favorito" if current_odds < 2.0 else "underdog"

    if win_rate >= 60:
        label = f"✅ Rinde bien como {role} ({win_rate}% victorias en {n} partidos similares)"
    elif win_rate <= 35:
        label = f"⚠️ Rinde mal como {role} ({win_rate}% victorias en {n} partidos similares)"
    else:
        label = f"📊 Rendimiento regular como {role} ({win_rate}% victorias en {n} partidos similares)"

    return {"win_rate": win_rate, "sample_size": n, "role": role, "label": label}


def calculate_confidence_score(home_stats, away_stats, poisson_data, h2h, odds,
                               home_standing, away_standing, league_key=None):
    score_home = score_draw = score_away = 0

    ph = poisson_data.get("prob_home_win", 33)
    pd = poisson_data.get("prob_draw", 33)
    pa = poisson_data.get("prob_away_win", 33)
    score_home += ph; score_draw += pd; score_away += pa

    elo_diff = home_stats.get("elo", 1500) - away_stats.get("elo", 1500)
    if elo_diff > 50:
        score_home += min(elo_diff / 10, 20)
    elif elo_diff < -50:
        score_away += min(abs(elo_diff) / 10, 20)
    else:
        score_draw += 10

    home_form = home_stats.get("form_points_5", 7)
    away_form = away_stats.get("form_points_5", 7)
    form_diff = home_form - away_form
    if form_diff > 3:
        score_home += min(form_diff * 2, 15)
    elif form_diff < -3:
        score_away += min(abs(form_diff) * 2, 15)
    else:
        score_draw += 5

    h_home_wr = home_stats.get("home_win_rate", 0.33)
    a_away_wr = away_stats.get("away_win_rate", 0.33)
    if h_home_wr > 0.55:   score_home += 10
    elif h_home_wr < 0.30: score_home -= 5
    if a_away_wr > 0.40:   score_away += 10
    elif a_away_wr < 0.20: score_away -= 5

    if h2h and h2h.get("total", 0) >= 3:
        total_h2h  = h2h["total"]
        score_home += h2h.get("home_wins", 0) / total_h2h * 15
        score_draw += h2h.get("draws", 0)     / total_h2h * 15
        score_away += h2h.get("away_wins", 0) / total_h2h * 15

    # Incorporar eficiencia del mercado: en ligas eficientes, las cuotas tienen más peso
    efficiency = get_league_efficiency(league_key)
    market_weight = 0.3 + (efficiency - 0.5) * 0.4  # rango ~0.18 a 0.50

    if odds and odds.get("home_win") and odds.get("draw") and odds.get("away_win"):
        try:
            ih  = 1 / odds["home_win"]
            id_ = 1 / odds["draw"]
            ia  = 1 / odds["away_win"]
            tot = ih + id_ + ia
            score_home += (ih / tot) * 100 * market_weight
            score_draw += (id_ / tot) * 100 * market_weight
            score_away += (ia / tot) * 100 * market_weight
        except Exception:
            pass

    total_signal = score_home + score_draw + score_away
    if total_signal == 0:
        return {"home": 33, "draw": 34, "away": 33, "confidence": 50, "leader": "draw"}

    pct_home  = score_home  / total_signal * 100
    pct_draw  = score_draw  / total_signal * 100
    pct_away  = score_away  / total_signal * 100

    leader_val = max(pct_home, pct_draw, pct_away)
    second_val = sorted([pct_home, pct_draw, pct_away])[-2]
    raw_confidence = int(50 + (leader_val - second_val) * 1.2)

    home_matches_count = home_stats.get("total_matches", 0)
    away_matches_count = away_stats.get("total_matches", 0)
    min_matches = min(home_matches_count, away_matches_count)

    if min_matches == 0:
        max_confidence = 55
    elif min_matches < MIN_MATCHES_POISSON:
        max_confidence = 63
    elif min_matches < 10:
        max_confidence = 72
    elif min_matches < 15:
        max_confidence = 82
    else:
        max_confidence = 97

    # Penalidad por conflicto mercado vs modelo
    conflicts = market_vs_model_conflict(poisson_data, odds, league_key)
    if conflicts:
        max_diff = max(c["diff"] for c in conflicts)
        if max_diff >= 25:
            max_confidence = min(max_confidence, 70)
        elif max_diff >= 15:
            max_confidence = min(max_confidence, 78)

    confidence = min(raw_confidence, max_confidence)

    if leader_val == pct_home:   leader = "home"
    elif leader_val == pct_draw: leader = "draw"
    else:                        leader = "away"

    return {
        "home":  round(pct_home, 1),
        "draw":  round(pct_draw, 1),
        "away":  round(pct_away, 1),
        "confidence": confidence,
        "leader": leader,
        "data_quality": {
            "home_matches":  home_matches_count,
            "away_matches":  away_matches_count,
            "max_allowed":   max_confidence,
            "market_weight": round(market_weight, 2),
        },
    }


def halftime_stats(matches, team_id):
    g1_scored, g2_scored, g1_conceded, g2_conceded = [], [], [], []
    leading_ht_results = []

    for m in matches:
        score = m.get("score", {})
        full  = score.get("fullTime", {})
        ht    = score.get("halfTime", {})
        ft_h, ft_a = full.get("home"), full.get("away")
        ht_h, ht_a = ht.get("home"),   ht.get("away")
        if ft_h is None or ft_a is None:
            continue
        is_home = m.get("homeTeam", {}).get("id") == team_id
        ft_gf = ft_h if is_home else ft_a
        ft_ga = ft_a if is_home else ft_h
        if ht_h is not None and ht_a is not None:
            ht_gf = ht_h if is_home else ht_a
            ht_ga = ht_a if is_home else ht_h
            g1_scored.append(ht_gf)
            g1_conceded.append(ht_ga)
            g2_scored.append(max(0, ft_gf - ht_gf))
            g2_conceded.append(max(0, ft_ga - ht_ga))
            if ht_gf > ht_ga:
                if ft_gf > ft_ga:   leading_ht_results.append("W")
                elif ft_gf == ft_ga: leading_ht_results.append("D")
                else:               leading_ht_results.append("L")

    total = len(g1_scored)
    if total == 0:
        return {}
    result = {
        "games":            total,
        "avg_1h_scored":    round(sum(g1_scored)    / total, 2),
        "avg_2h_scored":    round(sum(g2_scored)    / total, 2),
        "avg_1h_conceded":  round(sum(g1_conceded)  / total, 2),
        "avg_2h_conceded":  round(sum(g2_conceded)  / total, 2),
        "stronger_half_att": "1T" if sum(g1_scored) >= sum(g2_scored) else "2T",
        "weaker_half_def":   "1T" if sum(g1_conceded) >= sum(g2_conceded) else "2T",
    }
    if leading_ht_results:
        result["win_when_leading_ht_pct"] = round(
            leading_ht_results.count("W") / len(leading_ht_results) * 100, 1
        )
        result["leading_ht_games"] = len(leading_ht_results)
        result["collapse_risk"]    = leading_ht_results.count("L") > 0
    return result


def day_of_week_stats(matches, team_id, target_dow=None):
    from collections import defaultdict
    day_results = defaultdict(list)
    for m in matches:
        date_str = m.get("utcDate", "")
        if not date_str:
            continue
        try:
            dt  = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            day = dt.strftime("%a")
        except Exception:
            continue
        score = m.get("score", {}).get("fullTime", {})
        hg, ag = score.get("home"), score.get("away")
        if hg is None or ag is None:
            continue
        is_home = m.get("homeTeam", {}).get("id") == team_id
        gf = hg if is_home else ag
        ga = ag if is_home else hg
        day_results[day].append("W" if gf > ga else "D" if gf == ga else "L")

    summary = {}
    for day, results in day_results.items():
        if len(results) >= 2:
            summary[day] = {
                "played":   len(results),
                "win_rate": round(results.count("W") / len(results) * 100, 1),
                "form":     "".join(results[-3:]),
            }
    if target_dow and target_dow in summary:
        return summary[target_dow]
    return summary


def night_vs_day_stats(matches, team_id):
    day_r, night_r = [], []
    for m in matches:
        date_str = m.get("utcDate", "")
        if not date_str:
            continue
        try:
            dt   = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            hour = dt.hour
        except Exception:
            continue
        score = m.get("score", {}).get("fullTime", {})
        hg, ag = score.get("home"), score.get("away")
        if hg is None or ag is None:
            continue
        is_home = m.get("homeTeam", {}).get("id") == team_id
        gf = hg if is_home else ag
        ga = ag if is_home else hg
        res = "W" if gf > ga else "D" if gf == ga else "L"
        (night_r if hour >= 18 else day_r).append(res)

    def _s(results):
        if len(results) < 3:
            return None
        return {
            "played":   len(results),
            "win_rate": round(results.count("W") / len(results) * 100, 1),
        }
    ds, ns = _s(day_r), _s(night_r)
    result = {}
    if ds: result["day"]   = ds
    if ns: result["night"] = ns
    if ds and ns:
        result["night_advantage"] = round(ns["win_rate"] - ds["win_rate"], 1)
    return result


def post_cup_fatigue(matches, team_id, days_threshold=4):
    if not matches:
        return None
    sorted_m = sorted(
        (m for m in matches if m.get("utcDate")),
        key=lambda x: x["utcDate"], reverse=True,
    )
    if len(sorted_m) < 2:
        return None
    last, prev = sorted_m[0], sorted_m[1]
    try:
        dt_last = datetime.fromisoformat(last["utcDate"].replace("Z", "+00:00"))
        dt_prev = datetime.fromisoformat(prev["utcDate"].replace("Z", "+00:00"))
        days_diff = (dt_last - dt_prev).days
    except Exception:
        return None

    prev_name = prev.get("competition", {}).get("name", "")
    prev_type = prev.get("competition", {}).get("type", "")
    cup_keywords = ["cup", "copa", "coupe", "pokal", "coppa", "fa cup", "league cup",
                    "champions", "europa", "conference", "libertadores", "sudamericana"]
    prev_is_cup = any(k in prev_name.lower() for k in cup_keywords) or prev_type == "Cup"
    if prev_is_cup and days_diff <= days_threshold:
        return {
            "cup_game": prev_name,
            "days_ago": days_diff,
            "warning":  f"⚠️ Jugó en {prev_name} hace {days_diff} días",
        }
    return None


def half_season_stats(matches, team_id):
    if not matches or len(matches) < 10:
        return {}
    sorted_m = sorted(
        (m for m in matches if m.get("utcDate")),
        key=lambda x: x["utcDate"],
    )
    mid = len(sorted_m) // 2
    first_half, second_half = sorted_m[:mid], sorted_m[mid:]

    def _rate(ms):
        results, gf_list, ga_list = [], [], []
        for m in ms:
            score = m.get("score", {}).get("fullTime", {})
            hg, ag = score.get("home"), score.get("away")
            if hg is None or ag is None:
                continue
            is_home = m.get("homeTeam", {}).get("id") == team_id
            gf = hg if is_home else ag
            ga = ag if is_home else hg
            results.append("W" if gf > ga else "D" if gf == ga else "L")
            gf_list.append(gf); ga_list.append(ga)
        if not results:
            return None
        total = len(results)
        return {
            "played":   total,
            "win_rate": round(results.count("W") / total * 100, 1),
            "avg_gf":   round(sum(gf_list) / total, 2),
            "avg_ga":   round(sum(ga_list) / total, 2),
            "form":     "".join(results[-5:]),
        }

    s1, s2 = _rate(first_half), _rate(second_half)
    if not s1 or not s2:
        return {}
    trend = ("📈 Mejorando" if s2["win_rate"] > s1["win_rate"] + 5
             else "📉 Bajando" if s2["win_rate"] < s1["win_rate"] - 5
             else "➡️ Estable")
    return {"first_half_season": s1, "second_half_season": s2, "trend": trend}


def calculate_value_bet(our_prob_pct, bookmaker_odds):
    if not bookmaker_odds or bookmaker_odds <= 1.0:
        return None
    our_prob = our_prob_pct / 100
    value = (our_prob * bookmaker_odds) - 1
    return round(value * 100, 1)


def h2h_stats(h2h_matches, home_id, away_id):
    if not h2h_matches:
        return {}
    home_wins = draws = away_wins = total = 0
    for m in h2h_matches:
        score = m.get("score", {}).get("fullTime", {})
        hg, ag = score.get("home"), score.get("away")
        if hg is None or ag is None:
            continue
        match_home_id = m.get("homeTeam", {}).get("id")
        if match_home_id == home_id:
            if hg > ag:   home_wins += 1
            elif hg == ag: draws    += 1
            else:          away_wins += 1
        else:
            if ag > hg:   home_wins += 1
            elif hg == ag: draws    += 1
            else:          away_wins += 1
        total += 1
    if total == 0:
        return {}
    return {"total": total, "home_wins": home_wins, "draws": draws, "away_wins": away_wins}
