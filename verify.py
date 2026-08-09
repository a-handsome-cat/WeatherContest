"""Join stored forecasts against observations and compute accuracy metrics per
source / variable / lead-time bucket, per the methodology agreed with the user:

- lead time is measured from our fetch time (not a model's true run-init time,
  which Open-Meteo doesn't expose) - this matches "what would the user actually
  see checking each morning", which is the real question for this project
- metrics are always computed and shown, never hidden - each bucket instead
  carries a confidence tag (insufficient / preliminary / confident) based on
  sample size, so thin data reads as "not solid yet" rather than "invisible"
- skill score is relative to a naive persistence baseline (last known
  observation at the time the forecast was made), so a model only looks good
  if it beats "tomorrow = same as today"
- precipitation is scored two ways: continuous error (MAE/bias) and occurrence
  skill (POD/FAR/CSI) since it's a rare-event variable where plain MAE is
  misleading
"""
from __future__ import annotations

import bisect
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import config
import db

LEAD_BUCKETS = [
    (0, 6, "0-6ч"),
    (6, 24, "6-24ч"),
    (24, 48, "1-2 дня"),
    (48, 72, "2-3 дня"),
    (72, 120, "3-5 дней"),
    (120, 241, "5-10 дней"),
]
SHORT_RANGE_BUCKETS = {"0-6ч", "6-24ч", "1-2 дня"}
LONG_RANGE_BUCKETS = {"3-5 дней", "5-10 дней"}

MIN_CONFIDENT_N = 25
MIN_PRELIMINARY_N = 10

PRECIP_THRESHOLD_MM = 0.2  # occurrence threshold for POD/FAR/CSI
CONTINUOUS_VARS = {"temperature_2m", "wind_speed_10m", "cloud_cover"}


def _lead_bucket(lead_hours: float) -> str | None:
    for lo, hi, label in LEAD_BUCKETS:
        if lo <= lead_hours < hi:
            return label
    return None


def confidence(n: int) -> str:
    if n >= MIN_CONFIDENT_N:
        return "confident"
    if n >= MIN_PRELIMINARY_N:
        return "preliminary"
    return "insufficient"


def _parse(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


class PersistenceIndex:
    """For a given variable, finds the most recent observation at or before a timestamp."""

    def __init__(self, obs_rows: list[tuple[str, float]]):
        # obs_rows: [(obs_time_iso, value), ...] for a single variable, period_hours=1
        pairs = sorted((( _parse(t), v) for t, v in obs_rows), key=lambda p: p[0])
        self._times = [p[0] for p in pairs]
        self._values = [p[1] for p in pairs]

    def value_before(self, when: datetime):
        idx = bisect.bisect_right(self._times, when) - 1
        if idx < 0:
            return None
        return self._values[idx]


def _fetch_pairs(conn: sqlite3.Connection, city: str) -> list[dict]:
    obs_rows = conn.execute(
        "SELECT obs_time, variable, period_hours, value FROM observations WHERE city = ?",
        (city,),
    ).fetchall()
    # exact-match index for non-precipitation variables (all instantaneous, period_hours=1 on both sides)
    obs_index: dict[tuple[str, str, int], float] = {
        (obs_time, variable, period_hours): value for obs_time, variable, period_hours, value in obs_rows
        if variable != "precipitation"
    }
    precip_obs = [(obs_time, period_hours, value) for obs_time, variable, period_hours, value in obs_rows if variable == "precipitation"]

    persistence_by_var: dict[str, PersistenceIndex] = {}
    for variable in CONTINUOUS_VARS:
        rows = [(t, v) for t, var, ph, v in obs_rows if var == variable and ph == 1]
        persistence_by_var[variable] = PersistenceIndex(rows)

    fc_rows = conn.execute(
        """SELECT fr.source, fr.fetched_at, fp.valid_time, fp.variable, fp.period_hours, fp.value
           FROM forecast_points fp JOIN forecast_runs fr ON fp.run_id = fr.id
           WHERE fr.city = ?""",
        (city,),
    ).fetchall()

    pairs = []

    # Station precipitation reports are almost always accumulated over 6h/12h, never hourly,
    # while every forecast source gives clean hourly precip - so instead of requiring an exact
    # period_hours match (which would essentially never fire), sum each source's hourly values
    # over the same window the station's reading covers.
    hourly_precip: dict[tuple[str, str], dict[str, float]] = defaultdict(dict)
    for source, fetched_at, valid_time, variable, period_hours, fvalue in fc_rows:
        if variable == "precipitation" and period_hours == 1:
            hourly_precip[(source, fetched_at)][valid_time] = fvalue

    for obs_time, period_hours, observed in precip_obs:
        obs_dt = _parse(obs_time)
        window_start = obs_dt - timedelta(hours=period_hours)
        for (source, fetched_at), series in hourly_precip.items():
            fetched_dt = _parse(fetched_at)
            lead_hours = (obs_dt - fetched_dt).total_seconds() / 3600
            if lead_hours < 0:
                continue
            bucket = _lead_bucket(lead_hours)
            if bucket is None:
                continue
            total, hours_found = 0.0, 0
            for h in range(1, period_hours + 1):
                vt = (window_start + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%SZ")
                if vt in series:
                    total += series[vt]
                    hours_found += 1
            if hours_found != period_hours:
                continue  # incomplete window - skip rather than compare a biased partial sum
            pairs.append({
                "source": source,
                "variable": "precipitation",
                "bucket": bucket,
                "forecast": total,
                "observed": observed,
                "persistence": None,
                "fetched_at": fetched_at,
                "valid_time": obs_time,
                "lead_hours": round(lead_hours, 1),
                "period_hours": period_hours,
            })

    for source, fetched_at, valid_time, variable, period_hours, fvalue in fc_rows:
        if variable == "precipitation":
            continue  # handled above via window aggregation
        key = (valid_time, variable, period_hours)
        if key not in obs_index:
            continue
        fetched_dt = _parse(fetched_at)
        valid_dt = _parse(valid_time)
        lead_hours = (valid_dt - fetched_dt).total_seconds() / 3600
        if lead_hours < 0:
            continue
        bucket = _lead_bucket(lead_hours)
        if bucket is None:
            continue

        observed = obs_index[key]
        persistence_pred = None
        if variable in persistence_by_var:
            persistence_pred = persistence_by_var[variable].value_before(fetched_dt)

        pairs.append({
            "source": source,
            "variable": variable,
            "bucket": bucket,
            "forecast": fvalue,
            "observed": observed,
            "persistence": persistence_pred,
            "fetched_at": fetched_at,
            "valid_time": valid_time,
            "lead_hours": round(lead_hours, 1),
        })
    return pairs


def pairs_by_cell(conn: sqlite3.Connection, city: str) -> dict[tuple[str, str, str], list[dict]]:
    """Groups _fetch_pairs() output by (variable, bucket, source) - the same key
    compute_metrics() aggregates over - so callers (the CSV export) can show the exact
    raw pairs behind any given cell."""
    grouped: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for p in _fetch_pairs(conn, city):
        grouped[(p["variable"], p["bucket"], p["source"])].append(p)
    return grouped


def _aggregate_continuous(pairs: list[dict]) -> dict:
    n = len(pairs)
    errors = [p["forecast"] - p["observed"] for p in pairs]
    abs_errors = [abs(e) for e in errors]
    mae = sum(abs_errors) / n
    bias = sum(errors) / n
    rmse = (sum(e * e for e in errors) / n) ** 0.5

    skill = None
    rmse_persistence = None
    n_persistence = 0
    persistence_pairs = [p for p in pairs if p["persistence"] is not None]
    if len(persistence_pairs) >= MIN_PRELIMINARY_N:
        n_persistence = len(persistence_pairs)
        mse_model = sum((p["forecast"] - p["observed"]) ** 2 for p in persistence_pairs) / n_persistence
        mse_persist = sum((p["persistence"] - p["observed"]) ** 2 for p in persistence_pairs) / n_persistence
        rmse_persistence = mse_persist ** 0.5
        if mse_persist > 0:
            skill = 1 - mse_model / mse_persist

    return {
        "n": n,
        "mae": mae,
        "bias": bias,
        "rmse": rmse,
        "skill_vs_persistence": skill,
        "confidence": confidence(n),
        "rmse_persistence": rmse_persistence,
        "n_persistence": n_persistence,
    }


def _aggregate_precip(pairs: list[dict]) -> dict:
    n = len(pairs)
    errors = [p["forecast"] - p["observed"] for p in pairs]
    mae = sum(abs(e) for e in errors) / n
    bias = sum(errors) / n

    hits = sum(1 for p in pairs if p["forecast"] >= PRECIP_THRESHOLD_MM and p["observed"] >= PRECIP_THRESHOLD_MM)
    misses = sum(1 for p in pairs if p["forecast"] < PRECIP_THRESHOLD_MM and p["observed"] >= PRECIP_THRESHOLD_MM)
    false_alarms = sum(1 for p in pairs if p["forecast"] >= PRECIP_THRESHOLD_MM and p["observed"] < PRECIP_THRESHOLD_MM)

    pod = hits / (hits + misses) if (hits + misses) > 0 else None
    far = false_alarms / (hits + false_alarms) if (hits + false_alarms) > 0 else None
    csi = hits / (hits + misses + false_alarms) if (hits + misses + false_alarms) > 0 else None

    return {"n": n, "mae": mae, "bias": bias, "pod": pod, "far": far, "csi": csi, "confidence": confidence(n)}


def compute_metrics(conn: sqlite3.Connection, city: str) -> dict:
    """Returns metrics[variable][bucket][source] = {...}."""
    pairs = _fetch_pairs(conn, city)
    grouped: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for p in pairs:
        grouped[(p["variable"], p["bucket"], p["source"])].append(p)

    metrics: dict = defaultdict(lambda: defaultdict(dict))
    for (variable, bucket, source), group in grouped.items():
        if variable == "precipitation":
            metrics[variable][bucket][source] = _aggregate_precip(group)
        elif variable in CONTINUOUS_VARS:
            metrics[variable][bucket][source] = _aggregate_continuous(group)
        # wind_direction_10m intentionally left out of MVP scoring (circular error, TODO later)
    return metrics


def classify_specialization(metrics: dict) -> dict[str, str]:
    """Ranks sources by average skill score in short- vs long-range buckets (temperature only,
    since it's the variable with the most complete coverage across sources)."""
    temp = metrics.get("temperature_2m", {})
    short_skill: dict[str, list[float]] = defaultdict(list)
    long_skill: dict[str, list[float]] = defaultdict(list)

    for bucket, by_source in temp.items():
        target = short_skill if bucket in SHORT_RANGE_BUCKETS else long_skill if bucket in LONG_RANGE_BUCKETS else None
        if target is None:
            continue
        for source, m in by_source.items():
            if m["confidence"] != "insufficient" and m["skill_vs_persistence"] is not None:
                target[source].append(m["skill_vs_persistence"])

    def top3(d):
        avgs = {s: sum(v) / len(v) for s, v in d.items() if v}
        return set(sorted(avgs, key=avgs.get, reverse=True)[:3])

    short_leaders = top3(short_skill)
    long_leaders = top3(long_skill)

    tags: dict[str, str] = {}
    for source in set(short_skill) | set(long_skill):
        is_short = source in short_leaders
        is_long = source in long_leaders
        if is_short and is_long:
            tags[source] = "стабильно сильна на всех горизонтах"
        elif is_short:
            tags[source] = "сильна на ближайшие сутки"
        elif is_long:
            tags[source] = "сильна на дальнем горизонте"
    return tags


if __name__ == "__main__":
    conn = db.connect()
    for city_id in config.CITIES:
        m = compute_metrics(conn, city_id)
        n_pairs = sum(v["n"] for var in m.values() for bucket in var.values() for v in bucket.values())
        print(f"{city_id}: {n_pairs} scored forecast-observation pairs so far")
    conn.close()
