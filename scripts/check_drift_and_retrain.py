"""Calcula accuracy/WAPE, registra drift en Supabase y reentrena si aplica."""
import json
import os
import subprocess
import sys
from pathlib import Path

import httpx
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
THRESHOLD = float(os.getenv("DRIFT_ACCURACY_THRESHOLD", "0.85"))

def fetch(client, table, select):
    response = client.get(f"/rest/v1/{table}", params={"select": select, "limit": 100000})
    response.raise_for_status()
    return response.json()

def record_metric(client, metric):
    response = client.post(
        "/rest/v1/drift_metrics",
        headers={"Prefer": "return=minimal"},
        json=metric,
    )
    response.raise_for_status()

def main():
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    with httpx.Client(base_url=os.environ["SUPABASE_URL"].rstrip("/"), headers=headers, timeout=60) as client:
        predictions = pd.DataFrame(fetch(client, "prediction_estimates", "station_id,target_at,estimated_value,created_at"))
        observations = pd.DataFrame(fetch(client, "demand_observations", "station_id,observed_at,demand"))
        if predictions.empty or observations.empty:
            record_metric(client, {"threshold": THRESHOLD, "status": "insufficient_data", "details": {"reason": "predictions_or_observations_empty"}})
            print("Sin datos suficientes para medir drift")
            return
        predictions["target_at"] = pd.to_datetime(predictions.target_at, utc=True)
        predictions["created_at"] = pd.to_datetime(predictions.created_at, utc=True)
        observations["observed_at"] = pd.to_datetime(observations.observed_at, utc=True)
        recent = predictions[predictions.created_at == predictions.created_at.max()]
        joined = recent.merge(observations, left_on=["station_id", "target_at"], right_on=["station_id", "observed_at"])
        if joined.empty:
            record_metric(client, {"prediction_batch_at": recent.created_at.iloc[0].isoformat(), "threshold": THRESHOLD, "status": "awaiting_actuals", "details": {"reason": "no_prediction_actual_matches"}})
            print("Las predicciones recientes aún no tienen valores reales")
            return
        wape = float((joined.demand - joined.estimated_value).abs().sum() / max(joined.demand.sum(), 1))
        accuracy = max(0.0, 1.0 - wape)
        drift_detected = accuracy < THRESHOLD
        retrained = False
        if drift_detected:
            subprocess.run([sys.executable, str(ROOT / "scripts/train_extratrees.py")], check=True)
            retrained = True
        metric = {"prediction_batch_at": recent.created_at.max().isoformat(), "accuracy": accuracy, "wape": wape, "matched_predictions": int(len(joined)), "threshold": THRESHOLD, "drift_detected": drift_detected, "retrained": retrained, "status": "ok", "details": {"stations": int(joined.station_id.nunique())}}
        record_metric(client, metric)
        print(f"accuracy={accuracy:.4f} threshold={THRESHOLD:.4f} matched={len(joined)} drift={drift_detected} retrained={retrained}")

if __name__ == "__main__":
    main()
