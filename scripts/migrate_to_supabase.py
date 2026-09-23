"""Carga los CSV locales en Supabase mediante la API REST por lotes."""

import csv
import os
from pathlib import Path

import httpx


BASE_URL = os.environ["PULSO_SUPABASE_URL"].rstrip("/")
API_KEY = os.environ["PULSO_SUPABASE_KEY"]
ROOT = Path(__file__).resolve().parents[1]
BATCH_SIZE = 500


def rows(filename: str) -> list[dict[str, str]]:
    with (ROOT / "artifacts" / filename).open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def post(client: httpx.Client, table: str, payload: list[dict], conflict: str | None = None) -> None:
    headers = {"Prefer": "resolution=merge-duplicates,return=minimal"}
    params = {"on_conflict": conflict} if conflict else None
    for start in range(0, len(payload), BATCH_SIZE):
        response = client.post(f"/rest/v1/{table}", params=params, headers=headers, json=payload[start:start + BATCH_SIZE])
        response.raise_for_status()
        print(f"{table}: {min(start + BATCH_SIZE, len(payload))}/{len(payload)}")


def main() -> None:
    stations = rows("stations.csv")
    context = rows("context.csv")
    observations = rows("observations.csv")
    corridors = [{"name": name} for name in sorted({row["corridor"] for row in stations})]
    corridor_ids: dict[str, int]

    with httpx.Client(base_url=BASE_URL, headers={"apikey": API_KEY, "Authorization": f"Bearer {API_KEY}"}, timeout=60) as client:
        post(client, "corridors", corridors, "name")
        corridor_response = client.get("/rest/v1/corridors", params={"select": "corridor_id,name"})
        corridor_response.raise_for_status()
        corridor_ids = {row["name"]: row["corridor_id"] for row in corridor_response.json()}

        station_payload = []
        for row in stations:
            station_payload.append({
                "station_id": row["station_id"],
                "station_name": row["station_name"],
                "corridor_id": corridor_ids[row["corridor"]],
                "latitude": float(row["latitude"]),
                "longitude": float(row["longitude"]),
            })
        post(client, "stations", station_payload, "station_id")

        time_rows = []
        weather_rows = []
        event_rows = []
        for row in context:
            from datetime import datetime
            dt = datetime.fromisoformat(row["observed_at"].replace("Z", "+00:00"))
            time_rows.append({"observed_at": row["observed_at"], "date_value": dt.date().isoformat(), "year": dt.year, "month": dt.month, "day": dt.day, "hour": dt.hour, "minute": dt.minute, "day_of_week": dt.isoweekday(), "is_weekend": dt.weekday() >= 5})
            weather_rows.append({"observed_at": row["observed_at"], "rain_mm": float(row["rain_mm"]), "rain_forecast": float(row["rain_forecast"]), "temperature_c": float(row["temperature_c"]), "temperature_forecast": float(row["temperature_forecast"])})
            event_rows.append({"observed_at": row["observed_at"], "event_intensity": float(row["event_intensity"])})
        post(client, "time_dimension", time_rows, "observed_at")
        post(client, "weather_context", weather_rows, "observed_at")
        post(client, "event_context", event_rows, "observed_at")
        post(client, "demand_observations", observations, "observed_at,station_id")

        for table in ("corridors", "stations", "time_dimension", "weather_context", "event_context", "demand_observations"):
            response = client.get(f"/rest/v1/{table}", params={"select": "*", "limit": "1"}, headers={"Prefer": "count=exact"})
            response.raise_for_status()
            print(f"{table}: {response.headers.get('content-range', 'count verificado')}")


if __name__ == "__main__":
    main()
