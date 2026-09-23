"""Genera y envía la predicción ExtraTrees del ciclo abierto de Pulso TransMi."""

import os
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
import joblib
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
API_URL = "https://pulso-transmi.72-60-245-2.sslip.io"


def main() -> None:
    api_key = os.environ["PULSO_API_KEY"]
    artifact = joblib.load(ROOT / "artifacts/extratrees_model.joblib")
    observations = pd.read_csv(ROOT / "artifacts/observations.csv", dtype={"station_id": "string"}, parse_dates=["observed_at"])
    context = pd.read_csv(ROOT / "artifacts/context.csv", parse_dates=["observed_at"])

    headers = {"Authorization": f"Bearer {api_key}", "User-Agent": "pulso-transmi-extratrees/1.0"}
    with httpx.Client(base_url=API_URL, headers=headers, timeout=30) as client:
        cycle = client.get("/v1/forecast-cycles/current").json()
        cutoff = pd.Timestamp(cycle["data_cutoff"])
        target_at = pd.Timestamp(cycle["targets"][0]["target_at"])

        rows = []
        for station_id in sorted(observations["station_id"].unique()):
            history = observations[observations.station_id == station_id].sort_values("observed_at").set_index("observed_at")
            values = history["demand"]
            latest_context = context[context.observed_at <= cutoff].sort_values("observed_at").iloc[-1]
            feature_row = {
                "station_code": artifact["station_codes"][station_id],
                "lag_1": values.iloc[-1],
                "lag_4": values.iloc[-4],
                "lag_96": values.iloc[-96],
                "lag_672": values.iloc[-672],
                "rolling_96": values.iloc[-96:].mean(),
                "slot": target_at.hour * 4 + target_at.minute // 15,
                "dow": target_at.dayofweek,
                "is_weekend": int(target_at.dayofweek >= 5),
                "rain_mm": latest_context.rain_mm,
                "rain_forecast": latest_context.rain_forecast,
                "temperature_c": latest_context.temperature_c,
                "temperature_forecast": latest_context.temperature_forecast,
                "event_intensity": latest_context.event_intensity,
            }
            x = pd.DataFrame([feature_row])[artifact["features"]]
            value = float(np.maximum(artifact["model"].predict(x)[0], 0))
            rows.append({"station_id": station_id, "target_at": target_at.isoformat().replace("+00:00", "Z"), "value": round(value, 4)})

        payload = {
            "schema_version": "1.0",
            "cycle_id": cycle["cycle_id"],
            "client_run_id": f"extratrees-{uuid.uuid4().hex}",
            "data_cutoff": cutoff.isoformat().replace("+00:00", "Z"),
            "model": {
                "version": "extratrees-2026-09-18",
                "trained_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "training_data_end": cutoff.isoformat().replace("+00:00", "Z"),
                "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            },
            "predictions": rows,
        }
        response = client.post("/v1/submissions", headers={"Idempotency-Key": payload["client_run_id"]}, json=payload)
        response.raise_for_status()
        print(response.text)


if __name__ == "__main__":
    main()
