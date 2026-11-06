"""Armazenamento local em SQLite (histórico de treino + plano).

Um arquivo único: ../garmin_painel.db. Tabelas:
  - activities      : atividades executadas (corrida/força) vindas da API
  - wellness        : métricas diárias de recuperação (RHR, HRV, sono, body battery, peso)
  - plan_sessions   : plano do usuário, editável dentro do app
  - meta            : chave/valor (ex.: last_sync)
  - vo2max_history  : VO2max oficial do relógio (importado do export GDPR da Garmin —
                       a Personal API não expõe esse valor, só treina-effect/labels)
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "garmin_painel.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS activities (
    activity_id      INTEGER PRIMARY KEY,
    name             TEXT,
    type_key         TEXT,          -- running, strength_training, ...
    start_local      TEXT,          -- 'YYYY-MM-DD HH:MM:SS'
    date             TEXT,          -- 'YYYY-MM-DD'
    iso_week         TEXT,          -- 'YYYY-Www'
    distance_m       REAL,
    duration_s       REAL,
    avg_speed_ms     REAL,
    avg_hr           REAL,
    max_hr           REAL,
    hr_z1_s          REAL,
    hr_z2_s          REAL,
    hr_z3_s          REAL,
    hr_z4_s          REAL,
    hr_z5_s          REAL,
    elevation_gain_m REAL,
    cadence_spm      REAL,
    vdot             REAL,          -- estimativa calculada (metrics.vdot_from_effort)
    ef               REAL           -- efficiency factor = (m/min) / FC média
);

CREATE TABLE IF NOT EXISTS wellness (
    date            TEXT PRIMARY KEY,   -- 'YYYY-MM-DD'
    resting_hr      REAL,
    hrv_last_night  REAL,
    sleep_s         REAL,
    bb_charged      REAL,
    bb_drained      REAL,
    weight_kg       REAL
);

CREATE TABLE IF NOT EXISTS plan_sessions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT NOT NULL,         -- 'YYYY-MM-DD' (dia planejado)
    iso_week    TEXT NOT NULL,         -- 'YYYY-Www'
    kind        TEXT NOT NULL,         -- Qualidade | Longo | Easy | Forca
    title       TEXT NOT NULL,
    target      TEXT,                  -- descrição livre (pace/distância/séries)
    target_km   REAL,                  -- distância alvo (opcional, p/ matching)
    done        INTEGER DEFAULT 0,     -- 0/1 marcação manual
    matched_id  INTEGER,               -- activity_id casado automaticamente
    notes       TEXT
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS vo2max_history (
    date    TEXT NOT NULL,              -- 'YYYY-MM-DD'
    sport   TEXT NOT NULL,              -- RUNNING, CYCLING, ...
    value   REAL NOT NULL,              -- vo2MaxValue reportado pelo relógio (Firstbeat)
    source  TEXT NOT NULL DEFAULT 'garmin_export',
    PRIMARY KEY (date, sport)
);
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(_SCHEMA)


def set_meta(key: str, value: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def get_meta(key: str, default: str | None = None) -> str | None:
    with connect() as conn:
        row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default
