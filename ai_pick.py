import os
import re
import logging
import requests
from analyzer import market_vs_model_conflict, kelly_criterion, get_league_efficiency_label

logger = logging.getLogger(__name__)

GROQ_URL   = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"


def _load_keys():
    keys = []
    for i in ["1", "2", "3"]:
        k = os.environ.get(f"GROQ_API_KEY_{i}", "").strip()
        if k:
            keys.append(k)
    fallback = os.environ.get("GROQ_API_KEY", "").strip()
    if fallback and fallback not in keys:
        keys.append(fallback)
    return keys

_key_index = 0

def _next_key(keys):
    global _key_index
    key = keys[_key_index % len(keys)]
    _key_index = (_key_index + 1) % len(keys)
    return key


def _call_groq(prompt):
    keys = _load_keys()
    if not keys:
        return "❌ No hay ninguna GROQ_API_KEY configurada."

    body = {
        "model":       GROQ_MODEL,
        "max_tokens":  900,
        "temperature": 0.3,
        "messages":    [{"role": "user", "content": prompt}],
    }
    tried, last_error = set(), ""

    for attempt in range(len(keys)):
        key = _next_key(keys)
        if key in tried:
            continue
        tried.add(key)
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
        key_label = f"key_{len(tried)}"
        try:
            resp = requests.post(GROQ_URL, headers=headers, json=body, timeout=30)
            if resp.status_code == 200:
                logger.info(f"Groq OK con {key_label}")
                return resp.json()["choices"][0]["message"]["content"]
            elif resp.status_code == 429:
                logger.warning(f"Groq 429 en {key_label}, probando siguiente...")
                last_error = "429 rate limit"
            else:
                logger.error(f"Groq {resp.status_code} en {key_label}: {resp.text[:200]}")
                last_error = f"Error {resp.status_code}"
        except requests.exceptions.Timeout:
            logger.warning(f"Groq timeout en {key_label}")
            last_error = "Timeout"
        except Exception as e:
            logger.error(f"Groq error en {key_label}: {e}")
            last_error = str(e)[:100]

    return f"❌ Todas las claves Groq fallaron. Último error: {last_error}"


def _cap_confidence_in_text(text, cap):
    return re.sub(
        r'(CONFIANZA\s*:\s*)(\d+)(\s*%)',
        lambda m: m.group(1) + str(min(int(m.group(2)), cap)) + m.group(3),
        text,
        flags=re.IGNORECASE,
    )


def _fmt_odds(val):
    """Formatea una cuota — nunca muestra None."""
    if val is None:
        return "N/D"
    try:
        return str(round(float(val), 2))
    except Exception:
        return "N/D"


def generate_pick(
    home_team, away_team,
    home_stats, away_stats,
    poisson_data, h2h, odds, competition,
    home_standing=None, away_standing=None,
    home_streak="", away_streak="",
    odds_movement=None,
    home_injuries=None, away_injuries=None,
    home_days_rest=None, away_days_rest=None,
    confidence_score=None,
    home_season_stats=None, away_season_stats=None,
    weather=None,
    home_ht=None, away_ht=None,
    home_dow=None, away_dow=None,
    home_night=None, away_night=None,
    home_cup_fatigue=None, away_cup_fatigue=None,
    home_half_season=None, away_half_season=None,
    home_coach=None, away_coach=None,
    match_dow=None,
    home_xg=None, away_xg=None,
    home_odds_range=None, away_odds_range=None,
    referee_info=None, referee_stats=None,
    home_suspensions=None, away_suspensions=None,
    league_key=None,
):
    # ── Helpers ───────────────────────────────────────────────────────
    def standing_str(name, s):
        if not s or not s.get("position"):
            return f"{name}: Sin datos de tabla"
        return (
            f"{name}: #{s['position']}/{s.get('total_teams','?')} | "
            f"{s.get('points','?')}pts | GD: {s.get('goal_diff',0):+d} | "
            f"GF/GC: {s.get('goals_for','?')}/{s.get('goals_against','?')}"
        )

    def fmt_injuries(injuries, name):
        if not injuries:
            return f"{name}: Sin bajas confirmadas"
        lines = [f"{name}: {len(injuries)} baja(s)"]
        for p in injuries[:4]:
            pos = f" ({p.get('position','')})" if p.get("position") else ""
            lines.append(f"  ❌ {p.get('name','?')}{pos} — {p.get('reason','Lesión')}")
        return "\n".join(lines)

    def rest_str(name, days):
        if days is None:
            return f"{name}: Sin datos"
        if days <= 2:
            return f"{name}: ⚠️ Solo {days} días de descanso (FATIGA)"
        if days <= 4:
            return f"{name}: {days} días (ritmo exigente)"
        return f"{name}: {days} días (descansado)"

    def coach_str(name, coach):
        if not coach or not coach.get("name"):
            return f"{name}: Sin datos de entrenador"
        since = f" (desde {coach['start']})" if coach.get("start") else ""
        return f"{name}: {coach['name']}{since} | {coach.get('nationality','')} | {coach.get('age','?')} años"

    def cup_fatigue_str(name, fatigue):
        if not fatigue:
            return f"{name}: Sin desgaste de copa detectado"
        return f"{name}: ⚠️ {fatigue.get('warning', 'Jugó en copa recientemente')}"

    def xg_str(name, xg):
        if not xg:
            return f"{name}: Sin datos de xG"
        lines = [
            f"{name} ({xg['sample_size']} partidos):",
            f"  xG anotados/p: {xg['avg_xg_scored']} | xG concedidos/p: {xg['avg_xg_conceded']}",
            f"  Balance xG: {xg['xg_diff']:+.2f}",
        ]
        if xg.get("avg_goals") is not None:
            lines.append(f"  Goles reales/p: {xg['avg_goals']}")
        if xg.get("over_performer"):
            lines.append(f"  ⚠️ {xg['over_performer']}")
        return "\n".join(lines)

    def odds_range_str(name, perf):
        if not perf or not perf.get("label"):
            return f"{name}: Sin datos de rendimiento por cuota"
        return f"{name}: {perf['label']}"

    def suspensions_str(name, risks):
        if not risks:
            return f"{name}: Sin riesgo de suspensión"
        parts = [f"{r['name']} ({r['yellows_active']}/{r['threshold']} amarillas ⚠️)" for r in risks]
        return f"{name}: {', '.join(parts)}"

    # ── Movimiento de cuotas ──────────────────────────────────────────
    movement_str = "Sin datos de movimiento"
    alert_str = ""
    if odds_movement and odds_movement.get("movements"):
        movement_str = "\n".join(odds_movement["movements"])
        if odds_movement.get("alert"):
            alert_str = f"⚠️ ALERTA: {odds_movement['alert_msg']}"

    # ── Árbitro ───────────────────────────────────────────────────────
    from referee import format_referee_for_ai
    referee_section = format_referee_for_ai(referee_info, referee_stats)

    # ── Clima ─────────────────────────────────────────────────────────
    from weather import weather_for_ai
    weather_section = weather_for_ai(weather, home_team)

    # ── Scores Poisson ────────────────────────────────────────────────
    top_scores_str = "Sin datos"
    if poisson_data.get("top_scores"):
        top_scores_str = "\n".join(
            f"  {score} → {prob:.1f}%"
            for score, prob in poisson_data["top_scores"]
        )

    # ── Señal de confianza ────────────────────────────────────────────
    conf_str = "Sin datos"
    if confidence_score:
        leader_map = {
            "home": f"Victoria {home_team}",
            "draw": "Empate",
            "away": f"Victoria {away_team}",
        }
        leader_label = leader_map.get(confidence_score.get("leader", ""), "?")
        conf_str = (
            f"Señal combinada → {leader_label} ({confidence_score.get('confidence', 0)}% confianza)\n"
            f"  {home_team} {confidence_score.get('home',0):.0f}% | "
            f"Empate {confidence_score.get('draw',0):.0f}% | "
            f"{away_team} {confidence_score.get('away',0):.0f}%"
        )

    # ── Conflicto mercado vs modelo ───────────────────────────────────
    conflicts = market_vs_model_conflict(poisson_data, odds, league_key)
    conflict_str = "Sin conflictos detectados entre modelo y mercado"
    conflict_warning = ""
    if conflicts:
        lines = []
        for c in conflicts:
            lines.append(
                f"  {c['severity']} {c['outcome']}: modelo={c['model']}% vs mercado={c['market']}% "
                f"(diferencia={c['diff']}%) → confiar en {c['trust']}"
            )
        conflict_str = "\n".join(lines)
        max_diff = max(c["diff"] for c in conflicts)
        if max_diff >= 25:
            conflict_warning = (
                f"\n🚨 CONFLICTO GRAVE: El modelo y el mercado difieren en más de {max_diff:.0f}%. "
                f"Reduce la confianza del pick y prioriza el {'mercado' if conflicts[0]['trust'] == 'mercado' else 'modelo'}."
            )

    # ── Eficiencia del mercado ────────────────────────────────────────
    efficiency_label = get_league_efficiency_label(league_key) if league_key else "Liga desconocida — eficiencia del mercado incierta"

    # ── Kelly Criterion ───────────────────────────────────────────────
    kelly_home = kelly_criterion(poisson_data.get("prob_home_win", 0), odds.get("home_win"))
    kelly_away = kelly_criterion(poisson_data.get("prob_away_win", 0), odds.get("away_win"))
    kelly_draw = kelly_criterion(poisson_data.get("prob_draw", 0), odds.get("draw"))
    kelly_lines = []
    if kelly_home:  kelly_lines.append(f"  Victoria {home_team}: {kelly_home}% del bankroll")
    if kelly_draw:  kelly_lines.append(f"  Empate: {kelly_draw}% del bankroll")
    if kelly_away:  kelly_lines.append(f"  Victoria {away_team}: {kelly_away}% del bankroll")
    kelly_str = "\n".join(kelly_lines) if kelly_lines else "  Sin ventaja matemática detectada (no apostar)"

    # ── Sin estadísticas ──────────────────────────────────────────────
    has_no_stats = not home_stats and not away_stats
    no_stats_warning = ""
    if has_no_stats:
        no_stats_warning = (
            "\n⚠️ IMPORTANTE: NO hay estadísticas históricas disponibles. "
            "Basa el análisis ÚNICAMENTE en cuotas, movimiento de mercado, árbitro y clima. "
            "NO inventes estadísticas. La confianza máxima es 70%.\n"
        )

    # ══════════════════════════════════════════════════════════════════
    # PROMPT
    # ══════════════════════════════════════════════════════════════════
    prompt = f"""Eres un experto analista de apuestas deportivas con 20 años de experiencia.
Analiza el partido y da un pick basándote en las señales clave en orden de importancia.
{no_stats_warning}
{conflict_warning}

=== PARTIDO ===
{home_team} vs {away_team}
Competición: {competition}
Día del partido: {match_dow or 'desconocido'}

=== EFICIENCIA DEL MERCADO (IMPORTANTE) ===
{efficiency_label}
Esto define cuánto peso darle a las cuotas vs al modelo estadístico.

=== TABLA DE POSICIONES ===
{standing_str(home_team, home_standing)}
{standing_str(away_team, away_standing)}
{home_team}: {_motivation(home_standing)}
{away_team}: {_motivation(away_standing)}

=== ENTRENADORES ===
{coach_str(home_team, home_coach)}
{coach_str(away_team, away_coach)}

══════════════════════════════════════
SEÑAL 1 — CONFLICTO MERCADO VS MODELO (revisar primero)
══════════════════════════════════════
{conflict_str}
Si hay conflicto alto, prioriza el mercado en ligas eficientes y el modelo en ligas poco eficientes.

══════════════════════════════════════
SEÑAL 2 — MOVIMIENTO DE CUOTAS
══════════════════════════════════════
{movement_str}
{alert_str}
Cuotas actuales:
  1 ({home_team}): {_fmt_odds(odds.get('home_win'))} | X: {_fmt_odds(odds.get('draw'))} | 2 ({away_team}): {_fmt_odds(odds.get('away_win'))}
  Over 2.5: {_fmt_odds(odds.get('over_25'))} | Under 2.5: {_fmt_odds(odds.get('under_25'))}
  BTTS Sí: {_fmt_odds(odds.get('btts_yes'))} | BTTS No: {_fmt_odds(odds.get('btts_no'))}

══════════════════════════════════════
SEÑAL 3 — xG (Expected Goals) — calidad real de juego
══════════════════════════════════════
{xg_str(home_team, home_xg)}
{xg_str(away_team, away_xg)}
(Si un equipo marca MÁS que su xG, probablemente no lo sostenga. Si marca MENOS, puede mejorar.)

══════════════════════════════════════
SEÑAL 4 — RENDIMIENTO COMO FAVORITO / UNDERDOG
══════════════════════════════════════
{odds_range_str(home_team, home_odds_range)}
{odds_range_str(away_team, away_odds_range)}

══════════════════════════════════════
SEÑAL 5 — DESCANSO Y FATIGA
══════════════════════════════════════
{rest_str(home_team, home_days_rest)}
{rest_str(away_team, away_days_rest)}
{cup_fatigue_str(home_team, home_cup_fatigue)}
{cup_fatigue_str(away_team, away_cup_fatigue)}
Racha: {home_team}: {home_streak} | {away_team}: {away_streak}

══════════════════════════════════════
SEÑAL 6 — ÁRBITRO DESIGNADO
══════════════════════════════════════
{referee_section}

══════════════════════════════════════
SEÑAL 7 — MODELO POISSON (con corrección Dixon-Coles)
══════════════════════════════════════
Goles esperados → {home_team}: {poisson_data.get('lambda_home',0)} | {away_team}: {poisson_data.get('lambda_away',0)}
Victoria {home_team}: {poisson_data.get('prob_home_win',0)}%
Empate: {poisson_data.get('prob_draw',0)}%
Victoria {away_team}: {poisson_data.get('prob_away_win',0)}%
Over 2.5: {poisson_data.get('prob_over25',0)}% | BTTS: {poisson_data.get('prob_btts',0)}%
Score más probable: {poisson_data.get('most_likely_score','N/A')}
Top marcadores:
{top_scores_str}

══════════════════════════════════════
KELLY CRITERION (tamaño de apuesta recomendado — 25% Kelly conservador)
══════════════════════════════════════
{kelly_str}
Nota: Estos % son del bankroll total. Si Kelly da 0% o negativo, no hay ventaja matemática.

══════════════════════════════════════
DATOS ADICIONALES
══════════════════════════════════════
=== ESTADÍSTICAS {home_team} ({home_stats.get('total_matches',0)} partidos) ===
V/E/D: {home_stats.get('wins',0)}/{home_stats.get('draws',0)}/{home_stats.get('losses',0)} | Forma: {home_stats.get('form_5','N/A')} | ELO: {home_stats.get('elo',1500)}
Local: {home_stats.get('avg_home_scored',0)} GF | {home_stats.get('avg_home_conceded',0)} GC | {home_stats.get('home_win_rate',0)*100:.1f}% victorias
Portería a 0: {home_stats.get('clean_sheets_rate',0)*100:.1f}% | BTTS: {home_stats.get('btts_rate',0)*100:.1f}%

=== ESTADÍSTICAS {away_team} ({away_stats.get('total_matches',0)} partidos) ===
V/E/D: {away_stats.get('wins',0)}/{away_stats.get('draws',0)}/{away_stats.get('losses',0)} | Forma: {away_stats.get('form_5','N/A')} | ELO: {away_stats.get('elo',1500)}
Visitante: {away_stats.get('avg_away_scored',0)} GF | {away_stats.get('avg_away_conceded',0)} GC | {away_stats.get('away_win_rate',0)*100:.1f}% victorias
Portería a 0: {away_stats.get('clean_sheets_rate',0)*100:.1f}% | BTTS: {away_stats.get('btts_rate',0)*100:.1f}%

=== HEAD TO HEAD ===
{f"Últimos {h2h.get('total',0)} partidos: {home_team} ganó {h2h.get('home_wins',0)} | Empates {h2h.get('draws',0)} | {away_team} ganó {h2h.get('away_wins',0)}" if h2h else "Sin datos H2H"}

=== LESIONES ===
{fmt_injuries(home_injuries or [], home_team)}
{fmt_injuries(away_injuries or [], away_team)}

=== RIESGO DE SUSPENSIÓN ===
{suspensions_str(home_team, home_suspensions or [])}
{suspensions_str(away_team, away_suspensions or [])}

=== CLIMA ===
{weather_section}

=== SCORE DE CONFIANZA COMBINADO ===
{conf_str}

=== TU TAREA ===
Pesa las señales en orden de importancia según la eficiencia del mercado de la liga.
En ligas muy eficientes (≥85%), prioriza conflicto mercado/modelo y movimiento de cuotas.
En ligas poco eficientes (<65%), da más peso al modelo Poisson y xG.

Responde EXACTAMENTE en este formato:

🎯 PICK PRINCIPAL: [UNO: "Victoria {home_team}" / "Empate" / "Victoria {away_team}"]
📊 PICK SECUNDARIO: [UNO: "Over 2.5 goles" / "Under 2.5 goles" / "BTTS Sí" / "BTTS No"]
⭐ CONFIANZA: [1-100]%
💰 CUOTA RECOMENDADA: [cuota del pick principal, número]
💸 KELLY: [% del bankroll recomendado según Kelly, o "No apostar (sin ventaja)"]
📝 RAZONAMIENTO: [4-5 líneas: menciona conflicto mercado/modelo, eficiencia de liga, movimiento cuotas, xG, descanso, árbitro y Poisson]
⚠️ RIESGO: [Bajo / Medio / Alto]

Solo el análisis, sin saludos."""

    result = _call_groq(prompt)

    if has_no_stats and result and not result.startswith("❌"):
        result = _cap_confidence_in_text(result, cap=70)
        logger.info("Cap de confianza 70% aplicado (sin estadísticas históricas)")

    return result


def _motivation(standing):
    if not standing or not standing.get("position"):
        return "Sin datos de posición"
    pos   = standing["position"]
    total = standing.get("total_teams", 20)
    pts   = standing.get("points", 0)
    if pos <= 1:    return f"🥇 Líder con {pts}pts — pelea por el título"
    if pos <= 4:    return f"🏆 Zona Champions (#{pos}) con {pts}pts"
    if pos <= 6:    return f"🎯 Zona Europa (#{pos}) con {pts}pts"
    if pos >= total - 2: return f"🚨 EN ZONA DE DESCENSO (#{pos}/{total}) — partido crucial"
    if pos >= total - 5: return f"⚠️ Peligro de descenso (#{pos}/{total}) con {pts}pts"
    return f"📊 Posición media (#{pos}/{total}) con {pts}pts"
