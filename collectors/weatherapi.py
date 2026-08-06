"""Fetch WeatherAPI.com's hourly forecast. Free tier caps this at 3 days out -
shorter than the raw models, but that's an honest reflection of what the free
consumer product actually offers.
"""
from __future__ import annotations

from datetime import datetime, timezone

import requests

URL = "https://api.weatherapi.com/v1/forecast.json"
FORECAST_DAYS = 3


def fetch(lat: float, lon: float, api_key: str) -> list[tuple[str, str, int, float]]:
    """Returns [(valid_time, variable, period_hours, value), ...]."""
    resp = requests.get(
        URL,
        params={"key": api_key, "q": f"{lat},{lon}", "days": FORECAST_DAYS, "aqi": "no", "alerts": "no"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    points: list[tuple[str, str, int, float]] = []
    for day in data.get("forecast", {}).get("forecastday", []):
        for hour in day.get("hour", []):
            valid_time = datetime.fromtimestamp(hour["time_epoch"], tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

            if "temp_c" in hour:
                points.append((valid_time, "temperature_2m", 1, float(hour["temp_c"])))
            if "cloud" in hour:
                points.append((valid_time, "cloud_cover", 1, float(hour["cloud"])))
            if "wind_kph" in hour:
                points.append((valid_time, "wind_speed_10m", 1, float(hour["wind_kph"]) / 3.6))
            if "wind_degree" in hour:
                points.append((valid_time, "wind_direction_10m", 1, float(hour["wind_degree"])))
            if "precip_mm" in hour:
                points.append((valid_time, "precipitation", 1, float(hour["precip_mm"])))

    return points
