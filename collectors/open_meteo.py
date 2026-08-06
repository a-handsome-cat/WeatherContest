"""Fetch raw NWP/AI model forecasts from Open-Meteo's multi-model endpoint (no API key needed)."""
from __future__ import annotations

import requests

import config

BASE_URL = "https://api.open-meteo.com/v1/forecast"


def fetch_multimodel(lat: float, lon: float) -> dict[str, list[tuple[str, str, int, float]]]:
    """Returns {model_id: [(valid_time, variable, period_hours, value), ...]}."""
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": ",".join(config.HOURLY_VARS),
        "models": ",".join(config.OPEN_METEO_MODELS),
        "timezone": "UTC",
        "forecast_days": config.FORECAST_DAYS,
        "wind_speed_unit": "ms",
    }
    resp = requests.get(BASE_URL, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    times = data["hourly"]["time"]
    by_model: dict[str, list[tuple[str, str, int, float]]] = {m: [] for m in config.OPEN_METEO_MODELS}

    for key, series in data["hourly"].items():
        if key == "time":
            continue
        # keys look like "temperature_2m_ecmwf_ifs025" - split off the trailing model id
        matched_model = next((m for m in config.OPEN_METEO_MODELS if key.endswith("_" + m)), None)
        if matched_model is None:
            continue
        variable = key[: -(len(matched_model) + 1)]
        for valid_time, value in zip(times, series):
            if value is None:
                continue
            by_model[matched_model].append((valid_time + ":00Z", variable, 1, float(value)))

    return by_model
