"""Entry point: pull the latest SYNOP observations for every configured city.

Run alongside fetch_forecasts.py (4x/day). Requests a 72h lookback window (not
just since the last run) so a missed run doesn't leave a permanent gap - ogimet
re-sends the same historical reports and the UNIQUE constraint on observations
makes re-inserts a no-op.
"""
import config
import db
from collectors import synop


def main() -> None:
    conn = db.connect()
    for city_id, city in config.CITIES.items():
        points = synop.fetch(city["synop_block"], hours_back=72)
        db.insert_observations(conn, city_id, city["synop_block"], points)
        print(f"{city_id} (synop {city['synop_block']}): {len(points)} points (72h lookback)")
    conn.commit()
    conn.close()


if __name__ == "__main__":
    main()
