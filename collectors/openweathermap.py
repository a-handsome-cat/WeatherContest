"""Fetch OpenWeatherMap's 5-day/3-hour forecast - the standard free-tier endpoint
(doesn't require the paid One Call 3.0 subscription). Coarser than the raw models
(3h steps, 5 days out) but that's fine - it's meant to represent "what a consumer
app shows", not compete on resolution.
"""
from __future__ import annotations

import requests

URL = "https://api.openweathermap.org/data/2.5/forecast"


def fetch(lat: float, lon: float, api_key: str) -> list[tuple[str, str, int, float]]:
    """Returns [(valid_time, variable, period_hours, value), ...]."""
    resp = requests.get(
        URL,
        params={"lat": lat, "lon": lon, "appid": api_key, "units": "metric"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    points: list[tuple[str, str, int, float]] = []
    for entry in data.get("list", []):
        valid_time = entry["dt_txt"].replace(" ", "T") + "Z"  # "2026-08-06 12:00:00" -> ISO

        main = entry.get("main", {})
        if "temp" in main:
            points.append((valid_time, "temperature_2m", 1, float(main["temp"])))

        clouds = entry.get("clouds", {})
        if "all" in clouds:
            points.append((valid_time, "cloud_cover", 1, float(clouds["all"])))

        wind = entry.get("wind", {})
        if "speed" in wind:
            points.append((valid_time, "wind_speed_10m", 1, float(wind["speed"])))
        if "deg" in wind:
            points.append((valid_time, "wind_direction_10m", 1, float(wind["deg"])))

        # rain/snow "3h" fields are only present in the response when it's actually
        # raining/snowing - absence means 0, not missing data
        precip = entry.get("rain", {}).get("3h", 0.0) + entry.get("snow", {}).get("3h", 0.0)
        points.append((valid_time, "precipitation", 3, float(precip)))

    return points
