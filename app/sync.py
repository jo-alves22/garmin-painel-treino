"""Sincroniza dados da Garmin Personal API para o SQLite local.

Usa endpoints de INTERVALO (poucas chamadas, independe do tamanho da janela):
  - atividades      : activitylist-service/.../search  (com startDate/endDate, paginado)
  - RHR / stress    : usersummary-service/v2/dailysummaries
  - HRV             : hrv-service/hrv/daily/{ini}/{fim}
  - sono            : sleep-service/.../dailySleepSummariesByDate
  - body battery    : wellness-service/.../bodyBattery/reports/daily
  - peso            : weight-service/weight/range/{ini}/{fim}

Rode:  python -m app.sync [semanas]      (padrão: 16 semanas)
"""

from __future__ import annotations

import datetime as dt
import sys

from . import db, metrics
from .garmin_client import GarminClient


def _iso_week(date_str: str) -> str:
    y, w, _ = dt.date.fromisoformat(date_str).isocalendar()
    return f"{y}-W{w:02d}"


def _daterange_str(weeks: int) -> tuple[str, str]:
    today = dt.date.today()
    return (today - dt.timedelta(weeks=weeks)).isoformat(), today.isoformat()


def _chunks(start: str, end: str, max_days: int = 30):
    """Fatia [start, end] em janelas de no máximo max_days (limites da API:
    sono/body battery = 31 dias, stress = 28)."""
    s, e = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    cur = s
    while cur <= e:
        stop = min(cur + dt.timedelta(days=max_days - 1), e)
        yield cur.isoformat(), stop.isoformat()
        cur = stop + dt.timedelta(days=1)


# --------------------------------------------------------------------------
# Atividades
# --------------------------------------------------------------------------
def sync_activities(client: GarminClient, start: str, end: str) -> int:
    rows, page, limit = [], 0, 100
    while True:
        batch = client.get(
            "/activitylist-service/activities/search/activities",
            params={"start": page * limit, "limit": limit, "startDate": start, "endDate": end},
        ) or []
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < limit:
            break
        page += 1

    out = []
    for a in rows:
        start_local = a.get("startTimeLocal") or ""
        date = start_local[:10]
        if not date:
            continue
        type_key = (a.get("activityType") or {}).get("typeKey", "")
        dist, dur = a.get("distance"), a.get("duration")
        avg_hr = a.get("averageHR")
        vdot = metrics.vdot_from_effort(dist, dur) if type_key in metrics.RUN_TYPES else None
        ef = metrics.efficiency_factor(dist, dur, avg_hr) if type_key in metrics.RUN_TYPES else None
        out.append((
            a.get("activityId"), a.get("activityName"), type_key, start_local, date, _iso_week(date),
            dist, dur, a.get("averageSpeed"), avg_hr, a.get("maxHR"),
            a.get("hrTimeInZone_1"), a.get("hrTimeInZone_2"), a.get("hrTimeInZone_3"),
            a.get("hrTimeInZone_4"), a.get("hrTimeInZone_5"),
            a.get("elevationGain"), a.get("averageRunningCadenceInStepsPerMinute"),
            vdot, ef,
        ))

    with db.connect() as conn:
        conn.executemany(
            """INSERT INTO activities(
                   activity_id, name, type_key, start_local, date, iso_week,
                   distance_m, duration_s, avg_speed_ms, avg_hr, max_hr,
                   hr_z1_s, hr_z2_s, hr_z3_s, hr_z4_s, hr_z5_s,
                   elevation_gain_m, cadence_spm, vdot, ef)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(activity_id) DO UPDATE SET
                   name=excluded.name, distance_m=excluded.distance_m,
                   duration_s=excluded.duration_s, avg_hr=excluded.avg_hr,
                   vdot=excluded.vdot, ef=excluded.ef""",
            out,
        )
    return len(out)


# --------------------------------------------------------------------------
# Bem-estar (merge por data)
# --------------------------------------------------------------------------
def _upsert_wellness(conn, date: str, field: str, value) -> None:
    if value is None:
        return
    conn.execute(f"INSERT INTO wellness(date, {field}) VALUES(?, ?) "
                 f"ON CONFLICT(date) DO UPDATE SET {field}=excluded.{field}", (date, value))


def _safe(label: str, fn, errors: list) -> None:
    """Executa uma fonte de bem-estar; registra o erro e segue (não derruba o sync)."""
    try:
        fn()
    except Exception as e:  # noqa: BLE001
        errors.append(f"{label}: {e}")


def sync_wellness(client: GarminClient, start: str, end: str) -> dict:
    counts = {"rhr": 0, "hrv": 0, "sleep": 0, "bb": 0, "weight": 0, "errors": []}
    with db.connect() as conn:
        # RHR via daily summaries (limite ~1 ano: chamada única)
        def _rhr():
            daily = client.get("/usersummary-service/v2/dailysummaries",
                               params={"startDate": start, "endDate": end}) or {}
            for d in daily.get("data", []) or []:
                rhr = (d.get("heartRate") or {}).get("restingValue")
                if d.get("calendarDate") and rhr:
                    _upsert_wellness(conn, d["calendarDate"], "resting_hr", rhr)
                    counts["rhr"] += 1
        _safe("RHR", _rhr, counts["errors"])

        # HRV (fatiado por segurança)
        def _hrv():
            for s, e in _chunks(start, end):
                hrv = client.get(f"/hrv-service/hrv/daily/{s}/{e}") or {}
                for h in hrv.get("hrvSummaries", []) or []:
                    if h.get("calendarDate") and h.get("lastNightAvg"):
                        _upsert_wellness(conn, h["calendarDate"], "hrv_last_night", h["lastNightAvg"])
                        counts["hrv"] += 1
        _safe("HRV", _hrv, counts["errors"])

        # Sono (limite 31 dias -> fatiar)
        def _sleep():
            for s, e in _chunks(start, end):
                data = client.get("/sleep-service/sleep/dailySleepSummariesByDate",
                                  params={"startDate": s, "endDate": e}) or []
                for it in data if isinstance(data, list) else []:
                    if it.get("calendarDate") and it.get("sleepTimeSeconds"):
                        _upsert_wellness(conn, it["calendarDate"], "sleep_s", it["sleepTimeSeconds"])
                        counts["sleep"] += 1
        _safe("Sono", _sleep, counts["errors"])

        # Body Battery (limite 31 dias -> fatiar)
        def _bb():
            for s, e in _chunks(start, end):
                data = client.get("/wellness-service/wellness/bodyBattery/reports/daily",
                                  params={"startDate": s, "endDate": e}) or []
                for b in data if isinstance(data, list) else []:
                    if b.get("date"):
                        _upsert_wellness(conn, b["date"], "bb_charged", b.get("charged"))
                        _upsert_wellness(conn, b["date"], "bb_drained", b.get("drained"))
                        counts["bb"] += 1
        _safe("BodyBattery", _bb, counts["errors"])

        # Peso (gramas -> kg; fatiado por segurança)
        def _weight():
            for s, e in _chunks(start, end):
                data = client.get(f"/weight-service/weight/range/{s}/{e}",
                                  params={"includeAll": "true"}) or {}
                for w in data.get("dailyWeightSummaries", []) or []:
                    latest = w.get("latestWeight") or {}
                    grams = latest.get("weight")
                    if w.get("summaryDate") and grams:
                        _upsert_wellness(conn, w["summaryDate"], "weight_kg", round(grams / 1000.0, 2))
                        counts["weight"] += 1
        _safe("Peso", _weight, counts["errors"])
    return counts


# --------------------------------------------------------------------------
# Orquestração
# --------------------------------------------------------------------------
def run_sync(weeks: int = 16) -> dict:
    db.init_db()
    start, end = _daterange_str(weeks)
    client = GarminClient()
    n_act = sync_activities(client, start, end)
    well = sync_wellness(client, start, end)
    stamp = dt.datetime.now().isoformat(timespec="seconds")
    db.set_meta("last_sync", stamp)
    db.set_meta("sync_window", f"{start}..{end}")
    return {"last_sync": stamp, "window": f"{start}..{end}", "activities": n_act, "wellness": well}


if __name__ == "__main__":
    wk = int(sys.argv[1]) if len(sys.argv) > 1 else 16
    print(f"Sincronizando {wk} semanas...")
    result = run_sync(wk)
    print(result)
