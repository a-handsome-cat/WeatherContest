"""Entry point: pull every configured forecast source for every configured city.

Run 4x/day, ~4h after each 00/06/12/18 UTC model cycle (see .github/workflows/collect.yml).
Each run is tagged with the wall-clock fetch time, which stands in for "what a user
checking the site right now would see" - see the methodology notes on why we don't
chase per-model run-init timestamps.

The Tier B (consumer-app) sources need API keys, read from the environment
(OPENWEATHERMAP_API_KEY / WEATHERAPI_API_KEY - set as GitHub Actions secrets in
CI, or export them locally). A missing key just skips that source with a warning
rather than failing the whole run - Tier A (Open-Meteo + met.no) needs no keys
at all and always runs.
"""
import os
from datetime import datetime, timezone

import config
import db
from collectors import metno, open_meteo, openweathermap, tomorrow_io, weatherapi, weatherkit, yandex

KEYED_SOURCES = [
    ("openweathermap", openweathermap.fetch, "OPENWEATHERMAP_API_KEY"),
    ("weatherapi", weatherapi.fetch, "WEATHERAPI_API_KEY"),
    ("tomorrow_io", tomorrow_io.fetch, "TOMORROW_IO_API_KEY"),
]

WEATHERKIT_ENV_VARS = ("APPLE_TEAM_ID", "APPLE_SERVICE_ID", "APPLE_KEY_ID", "APPLE_WEATHERKIT_PRIVATE_KEY")


def main() -> None:
    fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn = db.connect()

    api_keys = {env_var: os.environ.get(env_var) for _, _, env_var in KEYED_SOURCES}
    for source_name, _, env_var in KEYED_SOURCES:
        if not api_keys[env_var]:
            print(f"[skip] {env_var} not set - skipping {source_name} for all cities")

    weatherkit_creds = {var: os.environ.get(var) for var in WEATHERKIT_ENV_VARS}
    weatherkit_ready = all(weatherkit_creds.values())
    if not weatherkit_ready:
        missing = [v for v in WEATHERKIT_ENV_VARS if not weatherkit_creds[v]]
        print(f"[skip] weatherkit missing env vars {missing} - skipping for all cities")

    for city_id, city in config.CITIES.items():
        print(f"=== {city_id} ===")

        try:
            by_model = open_meteo.fetch_multimodel(city["lat"], city["lon"])
        except Exception as e:
            print(f"[error] open-meteo fetch failed for {city_id}: {e}")
            by_model = {}
        for model_id, points in by_model.items():
            if not points:
                print(f"[warn] open-meteo:{model_id} returned no points")
                continue
            run_id = db.insert_forecast_run(conn, city_id, f"open-meteo:{model_id}", fetched_at)
            db.insert_forecast_points(conn, run_id, points)
            print(f"open-meteo:{model_id}: {len(points)} points")

        try:
            metno_points = metno.fetch(city["lat"], city["lon"])
            run_id = db.insert_forecast_run(conn, city_id, "metno", fetched_at)
            db.insert_forecast_points(conn, run_id, metno_points)
            print(f"metno: {len(metno_points)} points")
        except Exception as e:
            print(f"[error] metno fetch failed for {city_id}: {e}")

        for source_name, fetch_fn, env_var in KEYED_SOURCES:
            api_key = api_keys[env_var]
            if not api_key:
                continue
            try:
                points = fetch_fn(city["lat"], city["lon"], api_key)
                run_id = db.insert_forecast_run(conn, city_id, source_name, fetched_at)
                db.insert_forecast_points(conn, run_id, points)
                print(f"{source_name}: {len(points)} points")
            except Exception as e:
                print(f"[error] {source_name} fetch failed for {city_id}: {e}")

        if weatherkit_ready:
            try:
                points = weatherkit.fetch(
                    city["lat"], city["lon"],
                    weatherkit_creds["APPLE_TEAM_ID"],
                    weatherkit_creds["APPLE_SERVICE_ID"],
                    weatherkit_creds["APPLE_KEY_ID"],
                    weatherkit_creds["APPLE_WEATHERKIT_PRIVATE_KEY"],
                )
                run_id = db.insert_forecast_run(conn, city_id, "weatherkit", fetched_at)
                db.insert_forecast_points(conn, run_id, points)
                print(f"weatherkit: {len(points)} points")
            except Exception as e:
                print(f"[error] weatherkit fetch failed for {city_id}: {e}")

        # Experimental / test-mode: scraped, so it's expected to break on Yandex's own
        # deploys sometimes. Logged to collection_log either way so that's visible on
        # the site (behind the hidden toggle) instead of just disappearing from CI logs.
        try:
            yandex_points = yandex.fetch(city["lat"], city["lon"], city["timezone"])
            run_id = db.insert_forecast_run(conn, city_id, "yandex", fetched_at)
            db.insert_forecast_points(conn, run_id, yandex_points)
            db.insert_collection_log(conn, "yandex", city_id, fetched_at, "ok", f"{len(yandex_points)} points")
            print(f"yandex: {len(yandex_points)} points")
        except Exception as e:
            db.insert_collection_log(conn, "yandex", city_id, fetched_at, "error", str(e)[:300])
            print(f"[error] yandex fetch failed for {city_id}: {e}")

        conn.commit()  # per city, so one city's failure can't roll back another city's successful fetches

    conn.close()


if __name__ == "__main__":
    main()
