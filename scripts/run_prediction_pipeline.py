"""Pipeline reproducible: ingesta, entrena ExtraTrees y envía una submission."""

import os
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesRegressor


ROOT = Path(__file__).resolve().parents[1]
API_URL = "https://pulso-transmi.72-60-245-2.sslip.io"
FEATURES = ["lag_1", "lag_4", "lag_96", "lag_672", "rolling_96", "slot", "dow", "is_weekend", "rain_mm", "rain_forecast", "temperature_c", "temperature_forecast", "event_intensity"]


def get_all(client: httpx.Client, endpoint: str, params: dict) -> list[dict]:
    rows, cursor = [], None
    while True:
        page = client.get(endpoint, params={**params, "limit": 5000, "cursor": cursor} if cursor else {**params, "limit": 5000})
        page.raise_for_status()
        payload = page.json()
        rows.extend(payload["data"])
        cursor = payload.get("next_cursor")
        if cursor is None:
            return rows


def features_for_history(history: pd.Series, target: pd.Timestamp, context_row: pd.Series, station_code: int) -> dict:
    return {"station_code": station_code, "lag_1": history.iloc[-1], "lag_4": history.iloc[-4], "lag_96": history.iloc[-96], "lag_672": history.iloc[-672], "rolling_96": history.iloc[-96:].mean(), "slot": target.hour * 4 + target.minute // 15, "dow": target.dayofweek, "is_weekend": int(target.dayofweek >= 5), "rain_mm": context_row.rain_mm, "rain_forecast": context_row.rain_forecast, "temperature_c": context_row.temperature_c, "temperature_forecast": context_row.temperature_forecast, "event_intensity": context_row.event_intensity}


def persist_estimates(payload: dict, response_body: dict) -> None:
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        print("Supabase no configurado; no se guardan prediction_estimates.")
        return
    submission_id = response_body.get("submission_id")
    rows = [
        {
            "run_id": payload["client_run_id"],
            "cycle_id": payload["cycle_id"],
            "submission_id": submission_id,
            "station_id": item["station_id"],
            "target_at": item["target_at"],
            "estimated_value": item["value"],
            "model_version": payload["model"]["version"],
            "data_cutoff": payload["data_cutoff"],
            "status": response_body.get("status", "accepted"),
        }
        for item in payload["predictions"]
    ]
    with httpx.Client(
        base_url=url.rstrip("/"),
        headers={"apikey": key, "Authorization": f"Bearer {key}"},
        timeout=60,
    ) as client:
        result = client.post(
            "/rest/v1/prediction_estimates",
            params={"on_conflict": "run_id,station_id,target_at"},
            headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
            json=rows,
        )
        result.raise_for_status()
    print(f"Predicciones guardadas en Supabase: {len(rows)}")

def main() -> None:
    api_key = os.environ["PULSO_API_KEY"]
    dry_run = os.getenv("PULSO_DRY_RUN", "0") == "1"
    headers = {"Authorization": f"Bearer {api_key}", "User-Agent": "pulso-transmi-pipeline/1.0"}
    with httpx.Client(base_url=API_URL, headers=headers, timeout=60) as client:
        cycle = client.get("/v1/forecast-cycles/current"); cycle.raise_for_status(); cycle = cycle.json()
        if cycle.get("state") != "open":
            raise RuntimeError(f"El ciclo no está abierto: {cycle.get('state')}")
        cutoff = pd.Timestamp(cycle["data_cutoff"])
        observations = pd.DataFrame(get_all(client, "/v1/observations", {}))
        context = pd.DataFrame(get_all(client, "/v1/context", {}))

    observations["observed_at"] = pd.to_datetime(observations["observed_at"], utc=True)
    observations["station_id"] = observations["station_id"].astype("string")
    context["observed_at"] = pd.to_datetime(context["observed_at"], utc=True)
    observations = observations[observations.observed_at <= cutoff].sort_values(["station_id", "observed_at"])
    context = context[context.observed_at <= cutoff].sort_values("observed_at")
    station_codes = {s: i for i, s in enumerate(sorted(observations.station_id.unique()))}

    train = observations.copy()
    train["slot"] = train.observed_at.dt.hour * 4 + train.observed_at.dt.minute // 15
    train["dow"] = train.observed_at.dt.dayofweek
    train["is_weekend"] = (train.dow >= 5).astype(int)
    grouped = train.groupby("station_id", sort=False).demand
    for lag in (1, 4, 96, 672): train[f"lag_{lag}"] = grouped.shift(lag)
    train["rolling_96"] = grouped.transform(lambda values: values.shift(1).rolling(96, min_periods=24).mean())
    train = train.merge(context, on="observed_at", how="left")
    train["station_code"] = train.station_id.map(station_codes)
    columns = ["station_code"] + FEATURES
    train = train.dropna(subset=columns + ["demand"])
    model = ExtraTreesRegressor(n_estimators=400, min_samples_leaf=2, max_features=0.9, n_jobs=-1, random_state=42)
    model.fit(train[columns], train.demand)

    latest_context = context.iloc[-1]
    predictions = []
    for target in cycle["targets"]:
        station_id = target["station_id"]
        target_at = pd.Timestamp(target["target_at"])
        history = observations[observations.station_id == station_id].sort_values("observed_at").demand
        row = features_for_history(history, target_at, latest_context, station_codes[station_id])
        value = float(np.maximum(model.predict(pd.DataFrame([row])[columns])[0], 0))
        predictions.append({"station_id": station_id, "target_at": target["target_at"], "value": round(value, 4)})

    client_run_id = f"extratrees-pipeline-{cycle['cycle_id']}"
    payload = {"schema_version": "1.0", "cycle_id": cycle["cycle_id"], "client_run_id": client_run_id, "data_cutoff": cycle["data_cutoff"], "model": {"version": "extratrees-pipeline-1.0", "trained_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), "training_data_end": cycle["data_cutoff"], "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()}, "predictions": predictions}
    if dry_run:
        print({"status": "dry_run", "cycle_id": cycle["cycle_id"], "predictions": len(predictions)})
        return
    with httpx.Client(base_url=API_URL, headers=headers, timeout=60) as client:
        response = client.post("/v1/submissions", headers={"Idempotency-Key": payload["client_run_id"]}, json=payload)
        if response.status_code == 409:
            print(f"El ciclo {cycle['cycle_id']} ya tiene una submission; se omite el duplicado.")
            print(response.text)
            return
        response.raise_for_status()
        response_body = response.json()
        print(response.text)
        persist_estimates(payload, response_body)

if __name__ == "__main__":
    main()
