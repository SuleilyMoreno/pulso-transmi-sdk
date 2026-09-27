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

def fetch_all(client, table, select, order):
    rows = []
    offset = 0
    page_size = 1000
    while True:
        response = client.get(
            f"/rest/v1/{table}",
            params={"select": select, "order": order, "limit": page_size, "offset": offset},
        )
        response.raise_for_status()
        page = response.json()
        rows.extend(page)
        if len(page) < page_size:
            return rows
        offset += page_size

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
        predictions = pd.DataFrame(fetch_all(
            client,
            "prediction_estimates",
            "cycle_id,station_id,target_at,estimated_value,created_at,data_cutoff",
            order="created_at.desc,station_id.asc,target_at.asc",
        ))
        observations = pd.DataFrame(fetch_all(
            client,
            "demand_observations",
            "station_id,observed_at,demand",
            order="observed_at.desc",
        ))
        if predictions.empty or observations.empty:
            record_metric(client, {"threshold": THRESHOLD, "status": "insufficient_data", "details": {"reason": "predictions_or_observations_empty"}})
            print("Sin datos suficientes para medir drift")
            return
        predictions["target_at"] = pd.to_datetime(predictions.target_at, utc=True)
        predictions["created_at"] = pd.to_datetime(predictions.created_at, utc=True)
        observations["observed_at"] = pd.to_datetime(observations.observed_at, utc=True)
        latest_cycle = predictions.sort_values("created_at").iloc[-1]["cycle_id"]
        recent = predictions[predictions.cycle_id == latest_cycle].copy()
        expected = len(recent)
        print(
            f"Diagnóstico: predicciones={len(predictions)} ciclo={latest_cycle} "
            f"esperadas={expected} "
            f"targets={recent.target_at.min()}..{recent.target_at.max()} "
            f"observaciones={len(observations)} "
            f"observed_at={observations.observed_at.min()}..{observations.observed_at.max()}"
        )
        joined = recent.merge(observations, left_on=["station_id", "target_at"], right_on=["station_id", "observed_at"])
        print(f"Diagnóstico: coincidencias={len(joined)}")
        if len(joined) < expected:
            record_metric(client, {"prediction_batch_at": recent.created_at.max().isoformat(), "threshold": THRESHOLD, "matched_predictions": int(len(joined)), "status": "awaiting_actuals", "details": {"cycle_id": latest_cycle, "expected_predictions": expected, "reason": "cycle_not_complete"}})
            print(f"Ciclo incompleto: {len(joined)}/{expected} valores reales")
            return
        # La referencia del proyecto calcula la métrica por estación y luego
        # promedia esos accuracies; no usa un WAPE global ponderado por demanda.
        station_scores = []
        for station_id, station in joined.groupby("station_id"):
            station_wape = float(
                (station.demand - station.estimated_value).abs().sum()
                / max(station.demand.sum(), 1)
            )
            station_scores.append({
                "station_id": station_id,
                "wape": station_wape,
                "accuracy": max(0.0, 1.0 - station_wape),
            })
        accuracy = float(pd.DataFrame(station_scores).accuracy.mean())
        wape = 1.0 - accuracy
        drift_detected = accuracy < THRESHOLD
        retrained = False
        if drift_detected:
            subprocess.run([sys.executable, str(ROOT / "scripts/train_extratrees_api.py")], check=True)
            retrained = True
        joined["horizon_minutes"] = (joined.target_at - pd.to_datetime(recent.data_cutoff.iloc[0], utc=True)).dt.total_seconds() / 60 if "data_cutoff" in recent else None
        by_horizon = {}
        for horizon, horizon_rows in joined.groupby("horizon_minutes"):
            horizon_station_scores = []
            for _, station in horizon_rows.groupby("station_id"):
                station_wape = (station.demand - station.estimated_value).abs().sum() / max(station.demand.sum(), 1)
                horizon_station_scores.append(max(0.0, 1.0 - station_wape))
            by_horizon[str(int(horizon))] = float(pd.Series(horizon_station_scores).mean())
        metric = {"prediction_batch_at": recent.created_at.max().isoformat(), "accuracy": accuracy, "wape": wape, "matched_predictions": int(len(joined)), "threshold": THRESHOLD, "drift_detected": drift_detected, "retrained": retrained, "status": "ok", "details": {"cycle_id": latest_cycle, "expected_predictions": expected, "stations": int(joined.station_id.nunique()), "accuracy_by_horizon": by_horizon}}
        record_metric(client, metric)
        print(f"accuracy={accuracy:.4f} threshold={THRESHOLD:.4f} matched={len(joined)} drift={drift_detected} retrained={retrained}")

if __name__ == "__main__":
    main()
