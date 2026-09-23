"""Sincroniza directamente la API Pulso TransMi con Supabase, sin CSV."""

import os
from datetime import timedelta

import httpx
import pandas as pd


API_URL = "https://pulso-transmi.72-60-245-2.sslip.io"
BATCH_SIZE = 500


def chunks(rows):
    for start in range(0, len(rows), BATCH_SIZE):
        yield rows[start:start + BATCH_SIZE]


def api_rows(client, endpoint, params=None):
    rows, cursor = [], None
    while True:
        request_params = dict(params or {}, limit=5000)
        if cursor:
            request_params["cursor"] = cursor
        response = client.get(endpoint, params=request_params)
        response.raise_for_status()
        payload = response.json()
        rows.extend(payload["data"])
        cursor = payload.get("next_cursor")
        if cursor is None:
            return rows


def upsert(client, table, rows, conflict):
    if not rows:
        return
    for batch in chunks(rows):
        response = client.post(
            f"/rest/v1/{table}",
            params={"on_conflict": conflict},
            headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
            json=batch,
        )
        response.raise_for_status()


def main():
    supabase_url = os.environ["SUPABASE_URL"].rstrip("/")
    service_key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    api_headers = {}
    if os.getenv("PULSO_API_KEY"):
        api_headers["Authorization"] = f"Bearer {os.environ['PULSO_API_KEY']}"

    with httpx.Client(base_url=supabase_url, headers={"apikey": service_key, "Authorization": f"Bearer {service_key}"}, timeout=60) as db, httpx.Client(base_url=API_URL, headers=api_headers, timeout=60) as api:
        station_rows = api.get("/v1/stations").json()["data"]
        corridor_names = sorted({row["corridor"] for row in station_rows})
        upsert(db, "corridors", [{"name": name} for name in corridor_names], "name")
        corridor_data = db.get("/rest/v1/corridors", params={"select": "corridor_id,name"})
        corridor_data.raise_for_status()
        corridor_ids = {row["name"]: row["corridor_id"] for row in corridor_data.json()}
        upsert(db, "stations", [{"station_id": row["station_id"], "station_name": row["station_name"], "corridor_id": corridor_ids[row["corridor"]], "latitude": row["latitude"], "longitude": row["longitude"]} for row in station_rows], "station_id")

        last = db.get("/rest/v1/time_dimension", params={"select": "observed_at", "order": "observed_at.desc", "limit": 1})
        last.raise_for_status()
        params = {}
        if last.json():
            params["start"] = (pd.Timestamp(last.json()[0]["observed_at"]) + timedelta(minutes=15)).isoformat()
        context = api_rows(api, "/v1/context", params)
        observations = api_rows(api, "/v1/observations", params)
        times = []
        weather = []
        events = []
        for row in context:
            dt = pd.Timestamp(row["observed_at"])
            times.append({"observed_at": row["observed_at"], "date_value": dt.date().isoformat(), "year": dt.year, "month": dt.month, "day": dt.day, "hour": dt.hour, "minute": dt.minute, "day_of_week": dt.isoweekday(), "is_weekend": dt.weekday() >= 5})
            weather.append({"observed_at": row["observed_at"], "rain_mm": row["rain_mm"], "rain_forecast": row["rain_forecast"], "temperature_c": row["temperature_c"], "temperature_forecast": row["temperature_forecast"]})
            events.append({"observed_at": row["observed_at"], "event_intensity": row["event_intensity"]})
        upsert(db, "time_dimension", times, "observed_at")
        upsert(db, "weather_context", weather, "observed_at")
        upsert(db, "event_context", events, "observed_at")
        upsert(db, "demand_observations", observations, "observed_at,station_id")
        print(f"Sincronizados: {len(station_rows)} estaciones, {len(context)} contextos y {len(observations)} observaciones nuevas")


if __name__ == "__main__":
    main()
