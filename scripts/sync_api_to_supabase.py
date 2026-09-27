import os
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx


API_URL = "https://pulso-transmi.72-60-245-2.sslip.io"
SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
PULSO_API_KEY = os.environ["PULSO_API_KEY"]

PAGE_SIZE = 5000
LOOKBACK_DAYS = 7


def supabase_headers() -> dict[str, str]:
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }


def get_json(
    client: httpx.Client,
    path: str,
    params: dict[str, Any] | None = None,
) -> Any:
    response = client.get(
        f"{API_URL}{path}",
        headers={"Authorization": f"Bearer {PULSO_API_KEY}"},
        params=params,
    )
    response.raise_for_status()
    return response.json()


def supabase_get(
    client: httpx.Client,
    table: str,
    params: dict[str, Any],
) -> Any:
    response = client.get(
        f"{SUPABASE_URL}/rest/v1/{table}",
        headers=supabase_headers(),
        params=params,
    )
    response.raise_for_status()
    return response.json()


def supabase_upsert(
    client: httpx.Client,
    table: str,
    rows: list[dict[str, Any]],
    conflict_columns: str,
) -> None:
    if not rows:
        return

    response = client.post(
        f"{SUPABASE_URL}/rest/v1/{table}",
        headers=supabase_headers(),
        params={"on_conflict": conflict_columns},
        json=rows,
    )

    if response.is_error:
        raise RuntimeError(
            f"Error insertando en {table}: "
            f"HTTP {response.status_code} - {response.text}"
        )


def extract_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return payload

    if isinstance(payload, dict):
        for key in ("data", "items", "results", "observations", "context"):
            value = payload.get(key)
            if isinstance(value, list):
                return value

    return []


def extract_cursor(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None

    return (
        payload.get("next_cursor")
        or payload.get("next")
        or payload.get("cursor")
    )


def fetch_all(
    client: httpx.Client,
    endpoint: str,
    params: dict[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    cursor: str | None = None

    while True:
        page_params = {
            **params,
            "limit": PAGE_SIZE,
        }

        if cursor:
            page_params["cursor"] = cursor

        payload = get_json(client, endpoint, page_params)
        page = extract_items(payload)

        if not page:
            break

        rows.extend(page)

        next_cursor = extract_cursor(payload)
        if not next_cursor or next_cursor == cursor:
            break

        cursor = next_cursor

        if len(page) < PAGE_SIZE:
            break

    return rows


def timestamp_key(row: dict[str, Any]) -> str | None:
    for key in (
        "observed_at",
        "observation_at",
        "target_at",
        "timestamp",
        "created_at",
    ):
        value = row.get(key)
        if value:
            return str(value)

    return None


def max_timestamp(rows: list[dict[str, Any]]) -> str | None:
    timestamps = [timestamp_key(row) for row in rows]
    timestamps = [value for value in timestamps if value]
    return max(timestamps) if timestamps else None


def main() -> None:
    now = datetime.now(timezone.utc)

    with httpx.Client(timeout=90.0) as client:
        latest_rows = supabase_get(
            client,
            "demand_observations",
            {
                "select": "observed_at",
                "order": "observed_at.desc",
                "limit": "1",
            },
        )

        latest_db = None
        if latest_rows:
            latest_db = latest_rows[0].get("observed_at")

        if latest_db:
            latest_dt = datetime.fromisoformat(
                latest_db.replace("Z", "+00:00")
            )
            start_dt = latest_dt - timedelta(days=LOOKBACK_DAYS)
        else:
            start_dt = now - timedelta(days=LOOKBACK_DAYS)

        start = start_dt.isoformat()
        end = now.isoformat()

        print(f"Último dato existente en Supabase: {latest_db}")
        print(f"Rango consultado en la API: {start} hasta {end}")

        observations = fetch_all(
            client,
            "/v1/observations",
            {
                "start": start,
                "end": end,
            },
        )

        contexts = fetch_all(
            client,
            "/v1/context",
            {
                "start": start,
                "end": end,
            },
        )

        print(f"Observaciones recibidas de la API: {len(observations)}")
        print(f"Contextos recibidos de la API: {len(contexts)}")
        print(f"Máxima observación recibida: {max_timestamp(observations)}")
        print(f"Máximo contexto recibido: {max_timestamp(contexts)}")

        if observations:
            supabase_upsert(
                client,
                "demand_observations",
                observations,
                "station_id,observed_at",
            )

        if contexts:
            supabase_upsert(
                client,
                "weather_context",
                contexts,
                "observed_at",
            )

        final_rows = supabase_get(
            client,
            "demand_observations",
            {
                "select": "observed_at",
                "order": "observed_at.desc",
                "limit": "1",
            },
        )

        latest_after = (
            final_rows[0].get("observed_at")
            if final_rows
            else None
        )

        print(f"Último dato en Supabase después de cargar: {latest_after}")


if __name__ == "__main__":
    main()
