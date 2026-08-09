"""Fetch Tomorrow.io's hourly forecast. Unlike the other Tier B sources, this one
runs its own proprietary sensor/radar data fusion rather than just repackaging the
public NWP models we already pull via Open-Meteo - genuinely different methodology.
Free tier caps hourly forecasts at 120h (5 days) out.
"""
from __future__ import annotations

import requests

URL = "https://api.tomorrow.io/v4/weather/forecast"


def fetch(lat: float, lon: float, api_key: str) -> list[tuple[str, str, int, float]]:
    """Returns [(valid_time, variable, period_hours, value), ...]."""
    resp = requests.get(
        URL,
        params={"location": f"{lat},{lon}", "apikey": api_key, "units": "metric", "timesteps": "1h"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    points: list[tuple[str, str, int, float]] = []
    for entry in data.get("timelines", {}).get("hourly", []):
        valid_time = entry["time"]  # already ISO8601 UTC, e.g. "2026-08-06T12:00:00Z"
        values = entry.get("values", {})

        if "temperature" in values:
            points.append((valid_time, "temperature_2m", 1, float(values["temperature"])))
        if "cloudCover" in values:
            points.append((valid_time, "cloud_cover", 1, float(values["cloudCover"])))
        if "windSpeed" in values:
            points.append((valid_time, "wind_speed_10m", 1, float(values["windSpeed"])))
        if "windDirection" in values:
            points.append((valid_time, "wind_direction_10m", 1, float(values["windDirection"])))

        # this endpoint has no unified "precipitationIntensity" field (confirmed by inspecting
        # a live response) - precip is split by type instead, so sum all three. mm/h over a 1h
        # bucket is numerically the same as mm accumulated that hour, same as elsewhere.
        rain = values.get("rainIntensity") or 0.0
        sleet = values.get("sleetIntensity") or 0.0
        snow = values.get("snowIntensity") or 0.0
        points.append((valid_time, "precipitation", 1, float(rain + sleet + snow)))

    return points
