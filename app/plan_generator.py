"""Motor de regras (sem IA) pra gerar o treino da semana.

Pipeline: VDOT atual (VO2max oficial do relógio, senão o proxy dos treinos) ->
paces de Daniels (metrics.training_paces) -> fase do ciclo a partir das semanas
até a prova (base/build/peak/taper) -> volume alvo (progressão limitada sobre a
média recente, cortada se a recuperação pedir cautela) -> sessões encaixadas
nos dias disponíveis (goals.get_availability).

Regras de segurança (mesmo espírito do que seria pedido a um treinador real):
  - no máximo 1 sessão de Qualidade por semana, e nenhuma se a recuperação
    estiver em alerta;
  - volume não sobe mais de ~8% sobre a média das últimas 4 semanas;
  - o longão nunca passa de 30% do volume da semana nem de 32 km;
  - 80/20 é mantido por construção: só 1 sessão forte, o resto é Easy/Longo
    em ritmo fácil.
"""

from __future__ import annotations

import datetime as dt
from typing import Literal

from . import db, goals, metrics, plan

Phase = Literal["base", "build", "peak", "taper"]
Recovery = Literal["ok", "caution"]


class PlanGeneratorError(RuntimeError):
    pass


def _current_vdot() -> float:
    """VO2max oficial do relógio (mais confiável) senão o melhor VDOT recente dos treinos."""
    with db.connect() as conn:
        row = conn.execute(
            "SELECT value FROM vo2max_history WHERE sport='RUNNING' ORDER BY date DESC LIMIT 1"
        ).fetchone()
        if row:
            return float(row["value"])
        row = conn.execute(
            "SELECT MAX(vdot) AS v FROM activities WHERE date >= date('now','-42 days') AND vdot IS NOT NULL"
        ).fetchone()
    return float(row["v"]) if row and row["v"] else 35.0


def _recent_weekly_volumes_km(weeks: int = 4) -> list[float]:
    """Volume (km) das últimas `weeks` semanas JÁ COMPLETAS — a semana atual fica de
    fora pra não puxar a média pra baixo artificialmente (ela ainda não acabou)."""
    today = dt.date.today()
    current_iso_week = f"{today.isocalendar().year}-W{today.isocalendar().week:02d}"
    cutoff = (today - dt.timedelta(weeks=weeks)).isoformat()
    with db.connect() as conn:
        rows = conn.execute(
            """SELECT iso_week, SUM(distance_m) AS m FROM activities
               WHERE date >= ? AND iso_week != ? AND type_key LIKE '%running%'
               GROUP BY iso_week""",
            (cutoff, current_iso_week),
        ).fetchall()
    return [r["m"] / 1000.0 for r in rows if r["m"]]


def _recovery_flag() -> Recovery:
    """Alerta se a leitura mais recente estiver bem pior que a mediana dos últimos 30 dias."""
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM wellness WHERE date >= date('now','-30 days') ORDER BY date"
        ).fetchall()
    if len(rows) < 5:
        return "ok"  # dado de menos pra julgar — não bloqueia por falta de histórico
    latest = rows[-1]
    rhr = sorted(r["resting_hr"] for r in rows if r["resting_hr"])
    hrv = sorted(r["hrv_last_night"] for r in rows if r["hrv_last_night"])
    if rhr and latest["resting_hr"] and latest["resting_hr"] > rhr[len(rhr) // 2] * 1.08:
        return "caution"
    if hrv and latest["hrv_last_night"] and latest["hrv_last_night"] < hrv[len(hrv) // 2] * 0.85:
        return "caution"
    return "ok"


def _phase(weeks_to_goal: int | None) -> Phase:
    if weeks_to_goal is None:
        return "build"  # sem prova marcada: ciclo padrão de construção de forma
    if weeks_to_goal <= 1:
        return "taper"
    if weeks_to_goal <= 3:
        return "peak"
    if weeks_to_goal <= 8:
        return "build"
    return "base"


def _target_volume_km(baseline_km: float, phase: Phase, recovery: Recovery) -> float:
    if baseline_km <= 0:
        return 15.0  # ponto de partida conservador p/ quem ainda não tem histórico
    factor = {"base": 1.08, "build": 1.08, "peak": 0.95, "taper": 0.6}[phase]
    if recovery == "caution":
        factor = min(factor, 0.85)
    return round(baseline_km * factor, 1)


def _quality_session(phase: Phase, goal_km: float, paces: dict[str, float]) -> tuple[str, str, float]:
    """(título, alvo, km) da sessão de qualidade da semana."""
    long_goal = goal_km >= 15  # meia/maratona pedem mais trabalho em ritmo de prova
    if phase == "taper":
        return ("Ativação", f"4–6×20s stride em {metrics.pace_str(paces['repetition'])}, bem leve", 4.0)
    if phase == "base":
        return ("Limiar curto", f"2×10min @ {metrics.pace_str(paces['threshold'])} (rec 2min)", 8.0)
    if phase == "build":
        if long_goal:
            return ("Limiar", f"3×10min @ {metrics.pace_str(paces['threshold'])} (rec 2min)", 10.0)
        return ("Intervalado", f"6×800m @ {metrics.pace_str(paces['interval'])} (rec 2min)", 8.0)
    # peak
    if long_goal:
        return ("Ritmo de prova", f"3×15min @ {metrics.pace_str(paces['marathon'])} (rec 3min)", 12.0)
    return ("Afiar", f"5×3min @ {metrics.pace_str(paces['interval'])} (rec 2min)", 6.0)


def generate_week_plan(monday: dt.date) -> list[dict]:
    """Sessões da semana de `monday`, prontas pra `plan.add_session` (ou apply_week_plan)."""
    goal = goals.get_goal()
    avail = sorted(goals.get_availability())
    if not avail:
        raise PlanGeneratorError("Marque pelo menos um dia disponível em 🎯 Objetivo e disponibilidade.")

    vdot = _current_vdot()
    paces = metrics.training_paces(vdot)
    baseline = _recent_weekly_volumes_km(4)
    baseline_km = sum(baseline) / len(baseline) if baseline else 0.0
    recovery = _recovery_flag()

    weeks_to_goal = None
    if goal["date"]:
        weeks_to_goal = max(0, (dt.date.fromisoformat(goal["date"]) - monday).days // 7)
    phase = _phase(weeks_to_goal)
    target_km = _target_volume_km(baseline_km, phase, recovery)

    sessions: list[dict] = []
    remaining = list(avail)

    # 1) Longão — "sagrado", só entra com pelo menos 2 dias disponíveis.
    long_km = 0.0
    if len(remaining) >= 2:
        long_day = remaining.pop()  # último dia disponível da semana (tende a cair no fds)
        long_km = round(min(target_km * 0.30, 32.0), 1)
        title = "Longão" if phase != "taper" else "Longo curto (pré-prova)"
        sessions.append({
            "weekday": long_day, "kind": "Longo", "title": title,
            "target": f"ritmo fácil ({metrics.pace_str(paces['easy'])}), FC baixa",
            "target_km": long_km,
        })

    # 2) Qualidade — só com pelo menos 3 dias disponíveis e recuperação ok.
    quality_km = 0.0
    if len(remaining) >= 2 and recovery == "ok":
        mid = remaining.pop(len(remaining) // 2)
        title, target, quality_km = _quality_session(phase, goal["distance_km"], paces)
        sessions.append({"weekday": mid, "kind": "Qualidade", "title": title,
                          "target": target, "target_km": quality_km})

    # 3) Força — alterna A/B; só se sobrar dia além do mínimo de 1 easy.
    if len(remaining) >= 2:
        forca_day = remaining.pop(0)
        variant = "A — pernas/potência" if monday.isocalendar().week % 2 == 0 else "B — potência/plio"
        sessions.append({"weekday": forca_day, "kind": "Forca", "title": f"Força {variant}",
                          "target": "agacho, terra, pliometria, core", "target_km": None})

    # 4) Easy — só usa os dias que o volume restante realmente justifica (os dias
    # disponíveis que sobrarem viram descanso de verdade, não é "treinar todo dia
    # disponível só porque dá"). Corredor forte roda easy um pouco mais longo.
    easy_total = max(target_km - long_km - quality_km, 0.0)
    if remaining and easy_total > 0:
        easy_run_km = max(4.0, min(8.0, round(target_km * 0.15, 1)))
        n_easy = min(max(1, round(easy_total / easy_run_km)), len(remaining))
        step = len(remaining) / n_easy
        chosen_days = [remaining[int(i * step)] for i in range(n_easy)]
        per_day = min(round(easy_total / n_easy, 1), 15.0)  # teto de segurança por sessão
        for day in chosen_days:
            sessions.append({"weekday": day, "kind": "Easy", "title": "Easy",
                              "target": f"FC baixa (~{metrics.pace_str(paces['easy'])})",
                              "target_km": per_day})

    sessions.sort(key=lambda s: s["weekday"])
    return sessions


def apply_week_plan(monday: dt.date, sessions: list[dict]) -> int:
    for s in sessions:
        date = (monday + dt.timedelta(days=s["weekday"])).isoformat()
        plan.add_session(date, s["kind"], s["title"], s["target"], s["target_km"])
    return len(sessions)
