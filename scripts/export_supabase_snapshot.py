"""Exporta tablas de Supabase para versionado como artefacto de GitHub."""
import csv
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parents[1]
TABLES = ["corridors", "stations", "time_dimension", "weather_context", "event_context", "demand_observations", "prediction_estimates", "drift_metrics", "ingestion_runs"]

def fetch_all(client, table):
    rows, offset = [], 0
    while True:
        response = client.get(f"/rest/v1/{table}", params={"select": "*", "limit": 1000, "offset": offset})
        response.raise_for_status()
        batch = response.json(); rows.extend(batch)
        if len(batch) < 1000: return rows
        offset += 1000

def main():
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = ROOT / "snapshot" / stamp; output.mkdir(parents=True, exist_ok=True)
    with httpx.Client(base_url=os.environ["SUPABASE_URL"].rstrip("/"), headers={"apikey": key, "Authorization": f"Bearer {key}"}, timeout=60) as client:
        for table in TABLES:
            rows = fetch_all(client, table)
            (output / f"{table}.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
            if rows:
                with (output / f"{table}.csv").open("w", newline="", encoding="utf-8") as stream:
                    writer = csv.DictWriter(stream, fieldnames=rows[0].keys()); writer.writeheader(); writer.writerows(rows)
            print(f"{table}: {len(rows)}")
    (ROOT / "snapshot" / "LATEST").write_text(stamp, encoding="utf-8")

if __name__ == "__main__": main()
