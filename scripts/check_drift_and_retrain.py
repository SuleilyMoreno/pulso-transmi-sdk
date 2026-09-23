"""Evalúa drift de predicciones y reentrena si la accuracy cae bajo el umbral."""
import os
import subprocess
from pathlib import Path
import httpx
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
THRESHOLD = float(os.getenv("DRIFT_ACCURACY_THRESHOLD", "0.85"))

def fetch(client, table, select):
    response = client.get(f"/rest/v1/{table}", params={"select": select, "limit": 100000})
    response.raise_for_status()
    return response.json()

def main():
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    with httpx.Client(base_url=os.environ["SUPABASE_URL"].rstrip("/"), headers={"apikey": key, "Authorization": f"Bearer {key}"}, timeout=60) as client:
        predictions = pd.DataFrame(fetch(client, "prediction_estimates", "station_id,target_at,estimated_value,created_at"))
        observations = pd.DataFrame(fetch(client, "demand_observations", "station_id,observed_at,demand"))
    if predictions.empty or observations.empty:
        print("Sin datos suficientes para medir drift")
        return
    predictions["target_at"] = pd.to_datetime(predictions.target_at, utc=True)
    predictions["created_at"] = pd.to_datetime(predictions.created_at, utc=True)
    observations["observed_at"] = pd.to_datetime(observations.observed_at, utc=True)
    recent = predictions[predictions.created_at == predictions.created_at.max()]
    joined = recent.merge(observations, left_on=["station_id", "target_at"], right_on=["station_id", "observed_at"])
    if joined.empty:
        print("Las predicciones recientes aún no tienen valores reales")
        return
    wape = (joined.demand - joined.estimated_value).abs().sum() / joined.demand.sum()
    accuracy = max(0.0, 1.0 - wape)
    print(f"accuracy={accuracy:.4f} threshold={THRESHOLD:.4f} matched={len(joined)}")
    if accuracy < THRESHOLD:
        subprocess.run([str(ROOT / ".venv/bin/python"), str(ROOT / "scripts/train_extratrees.py")], check=True)
        print("retrained=true")
    else:
        print("retrained=false")

if __name__ == "__main__": main()
