"""Fetch yr.no / MET Norway's global forecast directly (Open-Meteo's metno wrapper only covers the Nordic domain).

Requires a descriptive User-Agent per https://api.met.no/doc/TermsOfService - do not strip it.
"""
from __future__ import annotations

import requests

import config

URL = "https://api.met.no/weatherapi/locationforecast/2.0/compact"


def fetch(lat: float, lon: float) -> list[tuple[str, str, int, float]]:
    """Returns [(valid_time, variable, period_hours, value), ...]."""
    resp = requests.get(
        URL,
        params={"lat": lat, "lon": lon},
        headers={"User-Agent": config.METNO_USER_AGENT},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    points: list[tuple[str, str, int, float]] = []
    for entry in data["properties"]["timeseries"]:
        valid_time = entry["time"]  # already ISO8601 UTC, e.g. "2026-08-06T01:00:00Z"
        d = entry["data"]

        instant = d.get("instant", {}).get("details", {})
        if "air_temperature" in instant:
            points.append((valid_time, "temperature_2m", 1, float(instant["air_temperature"])))
        if "cloud_area_fraction" in instant:
            points.append((valid_time, "cloud_cover", 1, float(instant["cloud_area_fraction"])))
        if "wind_speed" in instant:
            points.append((valid_time, "wind_speed_10m", 1, float(instant["wind_speed"])))
        if "wind_from_direction" in instant:
            points.append((valid_time, "wind_direction_10m", 1, float(instant["wind_from_direction"])))

        # Precipitation resolution degrades with lead time: prefer the tightest window available.
        for window_key, period_hours in (("next_1_hours", 1), ("next_6_hours", 6), ("next_12_hours", 12)):
            block = d.get(window_key, {}).get("details", {})
            if "precipitation_amount" in block:
                points.append((valid_time, "precipitation", period_hours, float(block["precipitation_amount"])))
                break

    return points
