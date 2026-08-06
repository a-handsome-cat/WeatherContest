"""Fetch and decode hourly SYNOP reports for the Novi Sad station from ogimet.

ogimet re-broadcasts WMO GTS SYNOP messages verbatim - this is the same official
RHMZ station data, just in a stable machine-readable form instead of scraping
RHMZ's own web pages.
"""
from __future__ import annotations

import warnings
from datetime import datetime, timedelta, timezone

import requests
from pymetdecoder import synop as synop_decoder

URL = "https://www.ogimet.com/cgi-bin/getsynop"

# knots -> m/s, in case a report uses wind_indicator != m/s
KT_TO_MS = 0.514444


def _to_ms(speed: dict) -> float | None:
    if speed is None or speed.get("value") is None:
        return None
    value, unit = speed["value"], speed.get("unit", "m/s")
    if unit in ("m/s", "mps", "Cel"):
        return float(value)
    if unit in ("kt", "kn", "knot", "knots"):
        return float(value) * KT_TO_MS
    return float(value)


def fetch_raw(station_block: str, hours_back: int = 72) -> str:
    end = datetime.now(timezone.utc)
    begin = end - timedelta(hours=hours_back)
    resp = requests.get(
        URL,
        params={
            "block": station_block,
            "begin": begin.strftime("%Y%m%d%H%M"),
            "end": end.strftime("%Y%m%d%H%M"),
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.text


def parse(raw_text: str) -> list[tuple[str, str, int, float]]:
    """Returns [(obs_time_iso, variable, period_hours, value), ...]."""
    points: list[tuple[str, str, int, float]] = []
    decoder = synop_decoder.SYNOP()

    for line in raw_text.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(",", 6)
        if len(parts) != 7:
            continue
        _block, year, month, day, hour, _minute, raw_message = parts
        obs_time = datetime(int(year), int(month), int(day), int(hour), tzinfo=timezone.utc)
        obs_time_iso = obs_time.strftime("%Y-%m-%dT%H:%M:%SZ")

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            try:
                decoded = decoder.decode(raw_message)
            except Exception:
                continue

        temp = decoded.get("air_temperature")
        if temp and temp.get("value") is not None:
            points.append((obs_time_iso, "temperature_2m", 1, float(temp["value"])))

        wind = decoded.get("surface_wind") or {}
        speed_ms = _to_ms(wind.get("speed"))
        if speed_ms is not None:
            points.append((obs_time_iso, "wind_speed_10m", 1, speed_ms))
        direction = (wind.get("direction") or {}).get("value")
        if direction is not None:
            points.append((obs_time_iso, "wind_direction_10m", 1, float(direction)))

        cloud = decoded.get("cloud_cover")
        if cloud and cloud.get("value") is not None and cloud["value"] <= 8:
            points.append((obs_time_iso, "cloud_cover", 1, float(cloud["value"]) * 12.5))

        precip = decoded.get("precipitation_s1")
        if precip and precip.get("amount", {}).get("value") is not None:
            hours = (precip.get("time_before_obs") or {}).get("value") or 1
            points.append((obs_time_iso, "precipitation", int(hours), float(precip["amount"]["value"])))

    return points


def fetch(station_block: str, hours_back: int = 72) -> list[tuple[str, str, int, float]]:
    return parse(fetch_raw(station_block, hours_back))
