# -*- coding: utf-8 -*-
"""Static configuration for the multi-city weather-source comparison project."""

CITIES = {
    "novi_sad": {
        "display_name": "Нови-Сад",
        "lat": 45.2671,
        "lon": 19.8335,
        "synop_block": "13274",  # Novi Sad / Rimski Sancevi
    },
    "arkhangelsk": {
        "display_name": "Архангельск",
        "lat": 64.5401,
        "lon": 40.5433,
        "synop_block": "22550",  # Talagi, ~64.50N 40.73E, right by the city
    },
}

# Raw NWP / AI models pulled through Open-Meteo's multi-model endpoint in a single call.
# See https://open-meteo.com/en/docs for the full model id list; these are the
# "seamless" (auto-blended across available runs) variants where offered.
OPEN_METEO_MODELS = [
    "ecmwf_ifs025",          # ECMWF IFS HRES - generally the reference model globally
    "ecmwf_aifs025_single",  # ECMWF's own AI model
    "gfs_seamless",          # NOAA GFS
    "icon_seamless",         # DWD ICON - strong over Europe
    "gem_seamless",          # Environment Canada
    "ukmo_seamless",         # UK Met Office
    "meteofrance_seamless",  # ARPEGE
    "jma_seamless",          # Japan Meteorological Agency
]

HOURLY_VARS = [
    "temperature_2m",
    "precipitation",
    "cloud_cover",
    "wind_speed_10m",
    "wind_direction_10m",
]

FORECAST_DAYS = 10

DB_PATH = "data/weathercontest.sqlite3"
SITE_DIR = "docs"

# MET Norway (yr.no) requires a descriptive User-Agent identifying the app + a contact,
# per https://api.met.no/doc/TermsOfService. Replace the contact before real deployment.
METNO_USER_AGENT = "WeatherContestPrototype/0.1 github.com/artemij (contact: bwpgqk4htq@privaterelay.appleid.com)"
