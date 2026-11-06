"""Objetivo de prova e dias disponíveis pra treinar — usados pelo motor de regras
(plan_generator.py) pra montar a semana. Persistidos na tabela `meta`
(chave/valor), igual a `last_sync`.
"""

from __future__ import annotations

from . import db

WEEKDAY_NAMES = ["Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo"]


def get_goal() -> dict:
    return {
        "distance_km": float(db.get_meta("goal_distance_km") or 5.0),
        "time_s": int(db.get_meta("goal_time_s") or 1200),
        "date": db.get_meta("goal_date") or None,
    }


def set_goal(distance_km: float, time_s: int, date: str | None) -> None:
    db.set_meta("goal_distance_km", str(distance_km))
    db.set_meta("goal_time_s", str(time_s))
    db.set_meta("goal_date", date or "")


def get_availability() -> list[int]:
    raw = db.get_meta("avail_weekdays")
    if raw is None:
        return [0, 1, 2, 3, 4, 5, 6]
    return [int(x) for x in raw.split(",") if x != ""]


def set_availability(weekdays: list[int]) -> None:
    db.set_meta("avail_weekdays", ",".join(str(w) for w in sorted(weekdays)))
