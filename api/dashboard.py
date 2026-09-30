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
            drift_metrics = fetch(client, "drift_metrics", "metric_id,evaluated_at,prediction_batch_at,accuracy,wape,matched_predictions,threshold,drift_detected,retrained,status,details", 1000, "evaluated_at.desc")
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
    latest_metric = drift_metrics[0] if drift_metrics else None
    scored_metrics = [m for m in drift_metrics if m.get("accuracy") is not None]
    metric_values = [float(m["accuracy"]) for m in scored_metrics]
    rolling_24h = sum(metric_values[:24]) / len(metric_values[:24]) if metric_values else None
    last_six = scored_metrics[:6]
    accuracy_cumulative = sum(metric_values) / len(metric_values) if metric_values else None
    accuracy_last6 = sum(float(m["accuracy"]) for m in last_six) / len(last_six) if last_six else None
    drift_rate_cumulative = sum(bool(m.get("drift_detected")) for m in scored_metrics) / len(scored_metrics) if scored_metrics else None
    drift_rate_last6 = sum(bool(m.get("drift_detected")) for m in last_six) / len(last_six) if last_six else None
    cycle_history = []
    for metric in reversed(scored_metrics):
        details = metric.get("details") or {}
        cycle_history.append({
            "cycle_id": details.get("cycle_id", f"metric-{metric.get('metric_id')}"),
            "evaluated_at": metric.get("evaluated_at"),
            "accuracy": float(metric["accuracy"]),
            "threshold": float(metric.get("threshold") or 0.8),
            "drift_detected": bool(metric.get("drift_detected")),
            "retrained": bool(metric.get("retrained")),
            "matched_predictions": metric.get("matched_predictions", 0),
        })
    last_run = runs[0] if runs else (
        {"started_at": latest_predictions[0]["created_at"], "status": "prediction_submitted", "rows_loaded": len(latest_predictions)}
        if latest_predictions else None
    )
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "stations": stations,
        "series": {sid: rows[-96:] for sid, rows in obs_by_station.items()},
        "errors": errors[-500:],
        "matched_errors": matched[-500:],
        "metrics": {
            "accuracy": latest_metric.get("accuracy") if latest_metric and latest_metric.get("accuracy") is not None else accuracy,
            "matched": latest_metric.get("matched_predictions", len(matched)) if latest_metric else len(matched),
            "rolling_24h": rolling_24h,
            "accuracy_cumulative": accuracy_cumulative,
            "accuracy_last6": accuracy_last6,
            "drift_rate_cumulative": drift_rate_cumulative,
            "drift_rate_last6": drift_rate_last6,
            "cycles_evaluated": len(scored_metrics),
            "data_drift": None,
            "concept_drift": latest_metric.get("drift_detected") if latest_metric else None,
            "wape": latest_metric.get("wape") if latest_metric else None,
            "status": latest_metric.get("status") if latest_metric else "no_metrics",
            "retrained": latest_metric.get("retrained") if latest_metric else False,
        },
        "drift_history": drift_metrics[:100],
        "cycle_history": cycle_history[-100:],
        "last_run": last_run,
        "model_version": model_version,
        "leaderboard": None,
    }
    return {"statusCode": 200, "headers": {"content-type": "application/json", "cache-control": "s-maxage=60, stale-while-revalidate=300"}, "body": payload}


# Vercel Python recognizes this explicit entry point.
app = handler
