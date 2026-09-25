import os
from datetime import datetime, timezone
import httpx


def fetch(client, table, select="*", limit=1000, order=None):
    params = {"select": select, "limit": limit}
    if order:
        params["order"] = order
    response = client.get(f"/rest/v1/{table}", params=params)
    response.raise_for_status()
    return response.json()


def handler(request):
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        return {"statusCode": 500, "body": {"error": "Configura SUPABASE_URL y SUPABASE_SERVICE_ROLE_KEY en Vercel."}}
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    try:
        with httpx.Client(base_url=url.rstrip("/"), headers=headers, timeout=20) as client:
            stations = fetch(client, "stations", "station_id,station_name,latitude,longitude,active", 1000)
            observations = fetch(client, "demand_observations", "station_id,observed_at,demand", 1000, "observed_at.desc")
            predictions = fetch(client, "prediction_estimates", "station_id,target_at,estimated_value,created_at,model_version,status", 1000, "created_at.desc")
            runs = fetch(client, "ingestion_runs", "ingestion_id,started_at,status,rows_loaded", 20, "started_at.desc")
    except Exception as exc:
        return {"statusCode": 502, "body": {"error": f"No se pudo consultar Supabase: {exc}"}}

    obs_by_station = {}
    for row in reversed(observations):
        obs_by_station.setdefault(row["station_id"], []).append(row)
    latest_predictions = predictions[:]
    errors = []
    matched = []
    obs_lookup = {(r["station_id"], r["observed_at"]): r["demand"] for r in observations}
    for p in predictions:
        actual = obs_lookup.get((p["station_id"], p["target_at"]))
        if actual is not None:
            error = float(p["estimated_value"]) - float(actual)
            errors.append(error)
            matched.append({"station_id": p["station_id"], "target_at": p["target_at"], "error": error, "actual": actual, "estimated": p["estimated_value"]})
    accuracy = None
    if matched:
        denom = sum(abs(float(x["actual"])) for x in matched) or 1
        accuracy = max(0, 1 - sum(abs(x["error"]) for x in matched) / denom)
    model_version = next((p.get("model_version") for p in latest_predictions if p.get("model_version")), "No disponible")
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "stations": stations,
        "series": {sid: rows[-96:] for sid, rows in obs_by_station.items()},
        "errors": errors[-500:],
        "matched_errors": matched[-500:],
        "metrics": {"accuracy": accuracy, "matched": len(matched), "rolling_24h": None, "data_drift": None, "concept_drift": None},
        "last_run": runs[0] if runs else None,
        "model_version": model_version,
        "leaderboard": None,
    }
    return {"statusCode": 200, "headers": {"content-type": "application/json", "cache-control": "s-maxage=60, stale-while-revalidate=300"}, "body": payload}
