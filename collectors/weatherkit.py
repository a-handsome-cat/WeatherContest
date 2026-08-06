"""Fetch Apple WeatherKit's hourly forecast - literally the source behind the Apple
Weather app that started this whole project. Needs a WeatherKit-enabled key from an
Apple Developer Program membership: Team ID, Service ID, Key ID, and an ES256
private key (.p8). See https://developer.apple.com/documentation/weatherkitrestapi/
request_authentication_for_weatherkit_rest_api for how to generate these in the
Apple Developer portal - the exact click-path there is more authoritative than
anything paraphrased here.
"""
from __future__ import annotations

import time

import jwt
import requests

URL = "https://weatherkit.apple.com/api/v1/weather/en/{lat}/{lon}"


def _make_token(team_id: str, service_id: str, key_id: str, private_key_pem: str) -> str:
    now = int(time.time())
    headers = {"alg": "ES256", "kid": key_id, "id": f"{team_id}.{service_id}", "typ": "JWT"}
    payload = {"iss": team_id, "iat": now, "exp": now + 1800, "sub": service_id}
    return jwt.encode(payload, private_key_pem, algorithm="ES256", headers=headers)


def fetch(
    lat: float,
    lon: float,
    team_id: str,
    service_id: str,
    key_id: str,
    private_key_pem: str,
) -> list[tuple[str, str, int, float]]:
    """Returns [(valid_time, variable, period_hours, value), ...]."""
    token = _make_token(team_id, service_id, key_id, private_key_pem)
    resp = requests.get(
        URL.format(lat=lat, lon=lon),
        headers={"Authorization": f"Bearer {token}"},
        params={"dataSets": "forecastHourly", "timezone": "UTC"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    points: list[tuple[str, str, int, float]] = []
    for hour in data.get("forecastHourly", {}).get("hours", []):
        valid_time = hour["forecastStart"]  # expected ISO8601 UTC, e.g. "2026-08-06T12:00:00Z"

        if "temperature" in hour:
            points.append((valid_time, "temperature_2m", 1, float(hour["temperature"])))
        if "cloudCover" in hour:
            points.append((valid_time, "cloud_cover", 1, float(hour["cloudCover"]) * 100))
        if "windSpeed" in hour:
            points.append((valid_time, "wind_speed_10m", 1, float(hour["windSpeed"]) / 3.6))  # km/h -> m/s
        if "windDirection" in hour:
            points.append((valid_time, "wind_direction_10m", 1, float(hour["windDirection"])))
        if "precipitationIntensity" in hour:
            # mm/h over a 1h bucket is numerically the same as mm accumulated that hour
            points.append((valid_time, "precipitation", 1, float(hour["precipitationIntensity"])))

    return points
