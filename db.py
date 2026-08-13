"""SQLite storage for forecast snapshots and station observations, keyed by city."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS forecast_runs (
    id INTEGER PRIMARY KEY,
    city TEXT NOT NULL,
    source TEXT NOT NULL,          -- e.g. 'open-meteo:ecmwf_ifs025', 'metno'
    fetched_at TEXT NOT NULL,      -- UTC ISO8601, also used as our lead-time reference point
    UNIQUE(city, source, fetched_at)
);

CREATE TABLE IF NOT EXISTS forecast_points (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES forecast_runs(id) ON DELETE CASCADE,
    valid_time TEXT NOT NULL,      -- UTC ISO8601, timestamp the forecast value applies to
    variable TEXT NOT NULL,        -- temperature_2m | precipitation | cloud_cover | wind_speed_10m | wind_direction_10m
    period_hours INTEGER NOT NULL DEFAULT 1,  -- aggregation window (matters for precipitation, e.g. met.no thins to 6h/12h further out)
    value REAL,
    UNIQUE(run_id, valid_time, variable)
);

CREATE INDEX IF NOT EXISTS idx_forecast_points_lookup
    ON forecast_points(variable, valid_time);

CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY,
    city TEXT NOT NULL,
    station TEXT NOT NULL,
    obs_time TEXT NOT NULL,        -- UTC ISO8601
    variable TEXT NOT NULL,
    period_hours INTEGER NOT NULL DEFAULT 1,  -- accumulation window, matters for precipitation
    value REAL,
    UNIQUE(city, station, obs_time, variable, period_hours)
);

CREATE INDEX IF NOT EXISTS idx_observations_lookup
    ON observations(city, variable, obs_time);

-- Per-attempt success/failure record for fragile (scraped) sources, so a broken
-- collector shows up as a visible trend instead of silently going quiet. Not used
-- by the stable API-backed sources - see fetch_forecasts.py.
CREATE TABLE IF NOT EXISTS collection_log (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    city TEXT NOT NULL,
    fetched_at TEXT NOT NULL,      -- UTC ISO8601, same run-id timestamp as forecast_runs.fetched_at
    status TEXT NOT NULL,          -- 'ok' | 'error'
    detail TEXT                    -- point count on success, short error message on failure
);

CREATE INDEX IF NOT EXISTS idx_collection_log_lookup
    ON collection_log(source, city, fetched_at);
"""


def connect() -> sqlite3.Connection:
    Path(config.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def insert_forecast_run(conn: sqlite3.Connection, city: str, source: str, fetched_at: str) -> int:
    conn.execute(
        "INSERT OR IGNORE INTO forecast_runs (city, source, fetched_at) VALUES (?, ?, ?)",
        (city, source, fetched_at),
    )
    row = conn.execute(
        "SELECT id FROM forecast_runs WHERE city = ? AND source = ? AND fetched_at = ?",
        (city, source, fetched_at),
    ).fetchone()
    return row[0]


def insert_forecast_points(conn: sqlite3.Connection, run_id: int, points: list[tuple[str, str, int, float]]) -> None:
    conn.executemany(
        """INSERT OR REPLACE INTO forecast_points (run_id, valid_time, variable, period_hours, value)
           VALUES (?, ?, ?, ?, ?)""",
        [(run_id, valid_time, variable, period_hours, value) for valid_time, variable, period_hours, value in points],
    )


def insert_observations(conn: sqlite3.Connection, city: str, station: str, points: list[tuple[str, str, int, float]]) -> None:
    conn.executemany(
        """INSERT OR REPLACE INTO observations (city, station, obs_time, variable, period_hours, value)
           VALUES (?, ?, ?, ?, ?, ?)""",
        [(city, station, obs_time, variable, period_hours, value) for obs_time, variable, period_hours, value in points],
    )


def insert_collection_log(conn: sqlite3.Connection, source: str, city: str, fetched_at: str, status: str, detail: str) -> None:
    conn.execute(
        "INSERT INTO collection_log (source, city, fetched_at, status, detail) VALUES (?, ?, ?, ?, ?)",
        (source, city, fetched_at, status, detail),
    )


def recent_collection_log(conn: sqlite3.Connection, source: str, city: str, limit: int = 12) -> list[tuple[str, str, str]]:
    return conn.execute(
        "SELECT fetched_at, status, detail FROM collection_log WHERE source = ? AND city = ? ORDER BY fetched_at DESC LIMIT ?",
        (source, city, limit),
    ).fetchall()
