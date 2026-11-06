"""Plano de treino gerenciado dentro do app + casamento com o executado.

O plano vive na tabela plan_sessions (editável pela interface). Cada sessão tem
um dia, um tipo (Qualidade/Longo/Easy/Forca) e um alvo livre. O casamento liga
cada sessão planejada a uma atividade executada na mesma semana, para mostrar
o que já foi feito e o que ainda falta.
"""

from __future__ import annotations

import datetime as dt

from . import db

KINDS = ["Qualidade", "Longo", "Easy", "Forca"]
_RUN_KINDS = {"Qualidade", "Longo", "Easy"}


def iso_week_of(date_str: str) -> str:
    y, w, _ = dt.date.fromisoformat(date_str).isocalendar()
    return f"{y}-W{w:02d}"


def monday_of(date: dt.date) -> dt.date:
    return date - dt.timedelta(days=date.weekday())


# ---- CRUD ----------------------------------------------------------------
def add_session(date: str, kind: str, title: str, target: str = "",
                target_km: float | None = None, notes: str = "") -> int:
    with db.connect() as conn:
        cur = conn.execute(
            """INSERT INTO plan_sessions(date, iso_week, kind, title, target, target_km, notes)
               VALUES(?,?,?,?,?,?,?)""",
            (date, iso_week_of(date), kind, title, target, target_km, notes),
        )
        return cur.lastrowid


def update_session(session_id: int, **fields) -> None:
    if "date" in fields:
        fields["iso_week"] = iso_week_of(fields["date"])
    cols = ", ".join(f"{k}=?" for k in fields)
    with db.connect() as conn:
        conn.execute(f"UPDATE plan_sessions SET {cols} WHERE id=?",
                     (*fields.values(), session_id))


def delete_session(session_id: int) -> None:
    with db.connect() as conn:
        conn.execute("DELETE FROM plan_sessions WHERE id=?", (session_id,))


def list_week(iso_week: str) -> list[dict]:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM plan_sessions WHERE iso_week=? ORDER BY date, id", (iso_week,)
        ).fetchall()
    return [dict(r) for r in rows]


# ---- Casamento planejado x executado -------------------------------------
def recompute_matches(iso_week: str) -> None:
    """Liga, de forma gulosa, cada sessão planejada a uma atividade da semana."""
    with db.connect() as conn:
        acts = [dict(r) for r in conn.execute(
            "SELECT activity_id, type_key, date, distance_m FROM activities WHERE iso_week=?",
            (iso_week,)).fetchall()]
        sessions = [dict(r) for r in conn.execute(
            "SELECT * FROM plan_sessions WHERE iso_week=? ORDER BY date, id",
            (iso_week,)).fetchall()]

        used: set[int] = set()
        for s in sessions:
            is_run = s["kind"] in _RUN_KINDS
            candidates = [
                a for a in acts
                if a["activity_id"] not in used
                and ((a["type_key"] in {"running", "treadmill_running", "trail_running"}) == is_run)
            ]
            if not candidates:
                match_id = None
            else:
                # preferir mesmo dia; desempate por proximidade de distância ao alvo
                tgt_m = (s["target_km"] or 0) * 1000
                candidates.sort(key=lambda a: (
                    abs((dt.date.fromisoformat(a["date"]) - dt.date.fromisoformat(s["date"])).days),
                    abs((a["distance_m"] or 0) - tgt_m) if tgt_m else 0,
                ))
                match_id = candidates[0]["activity_id"]
                used.add(match_id)
            # done = casado automaticamente OU marcado manualmente antes
            done = 1 if (match_id is not None or s["done"]) else 0
            conn.execute("UPDATE plan_sessions SET matched_id=?, done=? WHERE id=?",
                         (match_id, done, s["id"]))


def week_status(iso_week: str) -> dict:
    recompute_matches(iso_week)
    sessions = list_week(iso_week)
    done = [s for s in sessions if s["done"]]
    remaining = [s for s in sessions if not s["done"]]
    return {
        "planned": len(sessions),
        "done": len(done),
        "remaining": remaining,
        "sessions": sessions,
    }


def upcoming(days: int = 10, from_date: dt.date | None = None) -> list[dict]:
    """Próximas sessões ainda não feitas, a partir de hoje."""
    today = (from_date or dt.date.today()).isoformat()
    until = ((from_date or dt.date.today()) + dt.timedelta(days=days)).isoformat()
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM plan_sessions WHERE date>=? AND date<=? AND done=0 ORDER BY date, id",
            (today, until)).fetchall()
    return [dict(r) for r in rows]


# ---- Semana modelo (ponto de partida; editável) --------------------------
def seed_template_week(monday: dt.date) -> int:
    """Cria uma semana modelo alinhada à filosofia do plano (1 qualidade + 1 longo
    sagrados; força alternada; easy flexível). Serve de ponto de partida editável."""
    iso = f"{monday.isocalendar().year}-W{monday.isocalendar().week:02d}"
    with db.connect() as conn:
        exists = conn.execute("SELECT 1 FROM plan_sessions WHERE iso_week=? LIMIT 1", (iso,)).fetchone()
    if exists:
        return 0
    d = {i: (monday + dt.timedelta(days=i)).isoformat() for i in range(7)}
    template = [
        (d[1], "Qualidade", "Intervalado / Limiar", "ex.: 5–6×1000 no pace de 5k; FC Z4", 8.0),
        (d[3], "Forca", "Força A — pernas/potência", "agacho, terra, pliometria, core", None),
        (d[5], "Easy", "Easy + strides", "FC < 138 (~6:10–6:40/km) + 4–6 strides", 6.0),
        (d[6], "Longo", "Longo fácil", "progressivo leve no fim; FC baixa", 14.0),
    ]
    n = 0
    for date, kind, title, target, km in template:
        add_session(date, kind, title, target, km)
        n += 1
    return n
