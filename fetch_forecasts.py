"""Entry point: pull every keyless forecast source for every configured city.

Run 4x/day, ~4h after each 00/06/12/18 UTC model cycle (see .github/workflows/collect.yml).
Each run is tagged with the wall-clock fetch time, which stands in for "what a user
checking the site right now would see" - see the methodology notes on why we don't
chase per-model run-init timestamps.
"""
from datetime import datetime, timezone

import config
import db
from collectors import metno, open_meteo


def main() -> None:
    fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn = db.connect()

    for city_id, city in config.CITIES.items():
        print(f"=== {city_id} ===")

        by_model = open_meteo.fetch_multimodel(city["lat"], city["lon"])
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

    conn.commit()
    conn.close()


if __name__ == "__main__":
    main()
