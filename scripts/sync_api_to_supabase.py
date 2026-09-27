"""Sincroniza directamente la API Pulso TransMi con Supabase, sin CSV."""

import os
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx


API_URL = "https://pulso-transmi.72-60-245-2.sslip.io"
PAGE_SIZE = 5000
BATCH_SIZE = 500


def supabase_headers(key: str) -> dict[str, str]:
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }


def supabase_get(
    client: httpx.Client,
    url: str,
    key: str,
    table: str,
    params: dict[str, Any],
) -> list[dict[str, Any]]:
    response = client.get(
        f"{url}/rest/v1/{table}",
        headers=supabase_headers(key),
        params=params,
    )
    response.raise_for_status()
    return response.json()


def supabase_upsert(
    client: httpx.Client,
    url: str,
    key: str,
    table: str,
    rows: list[dict[str, Any]],
    conflict_columns: str,
) -> None:
    if not rows:
        return
    for start in range(0, len(rows), BATCH_SIZE):
        batch = rows[start:start + BATCH_SIZE]
        response = client.post(
            f"{url}/rest/v1/{table}",
            headers=supabase_headers(key),
            params={"on_conflict": conflict_columns},
            json=batch,
        )
        if response.is_error:
            raise RuntimeError(
                f"Error insertando en {table}: "
                f"HTTP {response.status_code} - {response.text}"
            )


def fetch_all(
    client: httpx.Client,
    endpoint: str,
    params: dict[str, Any],
    api_key: str | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    cursor: str | None = None
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    while True:
        page_params = {**params, "limit": PAGE_SIZE}
        if cursor:
            page_params["cursor"] = cursor

        response = client.get(
            f"{API_URL}{endpoint}",
            headers=headers,
            params=page_params,
        )
        response.raise_for_status()
        payload = response.json()

        page = payload.get("data", [])
        if not page:
            break

        rows.extend(page)

        next_cursor = payload.get("next_cursor")
        if not next_cursor or next_cursor == cursor:
            break
        cursor = next_cursor

        if len(page) < PAGE_SIZE:
            break

    return rows


def clean_stream_observations(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convierte observaciones liberadas al esquema de demand_observations."""
    return [
        {"station_id": row["station_id"], "observed_at": row["observed_at"], "demand": row["demand"]}
        for row in rows
    ]


def main() -> None:
    supabase_url = os.environ["SUPABASE_URL"].rstrip("/")
    service_key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    api_key = os.getenv("PULSO_API_KEY")

    now = datetime.now(timezone.utc)

    with httpx.Client(timeout=120.0) as client:
        # --- Estaciones y corredores ---
        station_rows = fetch_all(client, "/v1/stations", {}, api_key)
        corridor_names = sorted({row["corridor"] for row in station_rows})
        supabase_upsert(
            client, supabase_url, service_key,
            "corridors",
            [{"name": name} for name in corridor_names],
            "name",
        )
        corridor_data = supabase_get(
            client, supabase_url, service_key,
            "corridors",
            {"select": "corridor_id,name"},
        )
        corridor_ids = {row["name"]: row["corridor_id"] for row in corridor_data}
        supabase_upsert(
            client, supabase_url, service_key,
            "stations",
            [
                {
                    "station_id": row["station_id"],
                    "station_name": row["station_name"],
                    "corridor_id": corridor_ids[row["corridor"]],
                    "latitude": row["latitude"],
                    "longitude": row["longitude"],
                }
                for row in station_rows
            ],
            "station_id",
        )

        # --- Determinar desde cuándo traer datos ---
        latest_rows = supabase_get(
            client, supabase_url, service_key,
            "demand_observations",
            {"select": "observed_at", "order": "observed_at.desc", "limit": "1"},
        )

        if latest_rows:
            latest_db = latest_rows[0]["observed_at"]
            latest_dt = datetime.fromisoformat(latest_db.replace("Z", "+00:00"))
            # Empezamos desde la última fecha guardada (sin límite artificial de días)
            # para asegurar que se traigan todos los datos faltantes hasta hoy.
            start_dt = latest_dt - timedelta(hours=1)
            print(f"Último dato en Supabase: {latest_db}")
        else:
            start_dt = now - timedelta(days=60)
            print("No hay datos previos en Supabase. Trayendo últimos 60 días.")

        start = start_dt.isoformat()
        print(f"Trayendo datos desde: {start} hasta: {now.isoformat()}")

        # --- Observaciones de demanda ---
        observations = fetch_all(
            client, "/v1/observations",
            {"start": start, "end": now.isoformat()},
            api_key,
        )
        stream_observations = clean_stream_observations(fetch_all(
            client, "/v1/stream/observations", {}, api_key
        ))
        observations = observations + stream_observations
        print(f"Observaciones históricas recibidas: {len(observations) - len(stream_observations)}")
        print(f"Observaciones liberadas del stream: {len(stream_observations)}")

        if observations:
            supabase_upsert(
                client, supabase_url, service_key,
                "demand_observations",
                observations,
                "station_id,observed_at",
            )

        # --- Contexto (clima + eventos) ---
        contexts = fetch_all(
            client, "/v1/context",
            {"start": start, "end": now.isoformat()},
            api_key,
        )
        print(f"Contextos recibidos: {len(contexts)}")

        if contexts:
            # time_dimension
            time_rows = []
            for row in contexts:
                dt = datetime.fromisoformat(row["observed_at"].replace("Z", "+00:00"))
                time_rows.append({
                    "observed_at": row["observed_at"],
                    "date_value": dt.date().isoformat(),
                    "year": dt.year,
                    "month": dt.month,
                    "day": dt.day,
                    "hour": dt.hour,
                    "minute": dt.minute,
                    "day_of_week": dt.isoweekday(),
                    "is_weekend": dt.weekday() >= 5,
                })
            supabase_upsert(
                client, supabase_url, service_key,
                "time_dimension", time_rows, "observed_at",
            )

            # weather_context — solo columnas de clima (sin event_intensity)
            weather_rows = [
                {
                    "observed_at": row["observed_at"],
                    "rain_mm": row.get("rain_mm"),
                    "rain_forecast": row.get("rain_forecast"),
                    "temperature_c": row.get("temperature_c"),
                    "temperature_forecast": row.get("temperature_forecast"),
                }
                for row in contexts
            ]
            supabase_upsert(
                client, supabase_url, service_key,
                "weather_context", weather_rows, "observed_at",
            )

            # event_context — solo event_intensity
            event_rows = [
                {
                    "observed_at": row["observed_at"],
                    "event_intensity": row.get("event_intensity"),
                }
                for row in contexts
            ]
            supabase_upsert(
                client, supabase_url, service_key,
                "event_context", event_rows, "observed_at",
            )

        # --- Verificación final ---
        final_rows = supabase_get(
            client, supabase_url, service_key,
            "demand_observations",
            {"select": "observed_at", "order": "observed_at.desc", "limit": "1"},
        )
        latest_after = final_rows[0]["observed_at"] if final_rows else None
        print(f"Último dato en Supabase tras sync: {latest_after}")
        print(f"Sync completado: {len(station_rows)} estaciones, {len(observations)} observaciones, {len(contexts)} contextos.")


if __name__ == "__main__":
    main()
