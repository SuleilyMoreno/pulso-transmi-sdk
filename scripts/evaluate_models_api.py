"""Evalúa varios modelos con historia y stream actuales de Pulso TransMi."""

import os
from datetime import datetime, timezone
from pathlib import Path

import httpx
import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesRegressor, HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error

ROOT = Path(__file__).resolve().parents[1]
API_URL = "https://pulso-transmi.72-60-245-2.sslip.io"
FEATURES = ["lag_1", "lag_4", "lag_96", "lag_672", "rolling_96", "slot", "dow", "is_weekend", "rain_mm", "rain_forecast", "temperature_c", "temperature_forecast", "event_intensity"]


def get_all(client, endpoint):
    rows, cursor = [], None
    while True:
        params = {"limit": 5000}
        if cursor:
            params["cursor"] = cursor
        response = client.get(endpoint, params=params)
        response.raise_for_status()
        payload = response.json()
        rows.extend(payload["data"])
        cursor = payload.get("next_cursor")
        if cursor is None:
            return rows


def load_data(client):
    history = get_all(client, "/v1/observations")
    stream = get_all(client, "/v1/stream/observations")
    observations = pd.DataFrame(history + [{k: row[k] for k in ("station_id", "observed_at", "demand")} for row in stream])
    observations = observations.drop_duplicates(["station_id", "observed_at"], keep="last")
    context = pd.DataFrame(get_all(client, "/v1/context"))
    observations["observed_at"] = pd.to_datetime(observations.observed_at, utc=True)
    context["observed_at"] = pd.to_datetime(context.observed_at, utc=True)
    frame = pd.merge_asof(observations.sort_values("observed_at"), context.sort_values("observed_at"), on="observed_at", direction="backward")
    frame = frame.sort_values(["station_id", "observed_at"]).reset_index(drop=True)
    frame["slot"] = frame.observed_at.dt.hour * 4 + frame.observed_at.dt.minute // 15
    frame["dow"] = frame.observed_at.dt.dayofweek
    frame["is_weekend"] = (frame.dow >= 5).astype(int)
    grouped = frame.groupby("station_id", sort=False).demand
    for lag in (1, 4, 96, 672):
        frame[f"lag_{lag}"] = grouped.shift(lag)
    frame["rolling_96"] = grouped.transform(lambda values: values.shift(1).rolling(96, min_periods=24).mean())
    codes = {station: i for i, station in enumerate(sorted(frame.station_id.unique()))}
    frame["station_code"] = frame.station_id.map(codes)
    frame = frame.dropna(subset=["station_code", *FEATURES, "demand"])
    cutoff = frame.observed_at.max() - pd.Timedelta(days=7)
    return frame[frame.observed_at <= cutoff], frame[frame.observed_at > cutoff], codes


def score(y, prediction):
    prediction = np.maximum(prediction, 0)
    wape = float(np.abs(y - prediction).sum() / max(y.sum(), 1))
    return {"MAE": float(mean_absolute_error(y, prediction)), "RMSE": float(mean_squared_error(y, prediction) ** 0.5), "WAPE": wape, "Accuracy": max(0.0, 1.0 - wape)}


def main():
    key = os.environ["PULSO_API_KEY"]
    headers = {"Authorization": f"Bearer {key}", "User-Agent": "pulso-transmi-model-selection/1.0"}
    with httpx.Client(base_url=API_URL, headers=headers, timeout=120) as client:
        train, valid, codes = load_data(client)
    columns = ["station_code", *FEATURES]
    models = {
        "ExtraTrees": ExtraTreesRegressor(n_estimators=400, min_samples_leaf=2, max_features=0.9, n_jobs=-1, random_state=42),
        "RandomForest": RandomForestRegressor(n_estimators=300, min_samples_leaf=2, max_features=0.8, n_jobs=-1, random_state=42),
        "HGB15": HistGradientBoostingRegressor(max_iter=400, learning_rate=0.05, max_leaf_nodes=15, l2_regularization=2.0, random_state=42),
        "HGB31": HistGradientBoostingRegressor(max_iter=400, learning_rate=0.05, max_leaf_nodes=31, l2_regularization=2.0, random_state=42),
        "HGB63": HistGradientBoostingRegressor(max_iter=300, learning_rate=0.05, max_leaf_nodes=63, l2_regularization=2.0, random_state=42),
    }
    results, fitted = [], {}
    for name, model in models.items():
        model.fit(train[columns], train.demand)
        results.append({"model": name, **score(valid.demand, model.predict(valid[columns]))})
        fitted[name] = model
    metrics = pd.DataFrame(results).sort_values("WAPE").reset_index(drop=True)
    winner = metrics.iloc[0]["model"]
    output = ROOT / "artifacts"
    output.mkdir(exist_ok=True)
    metrics.to_csv(output / "model_comparison_api.csv", index=False)
    joblib.dump({"model": fitted[winner], "features": columns, "station_codes": codes, "model_name": winner, "trained_rows": len(train), "trained_at": datetime.now(timezone.utc).isoformat()}, output / "best_model_api.joblib", compress=3)
    print(f"train={len(train):,} validation={len(valid):,} cutoff={train.observed_at.max().isoformat()}")
    print(metrics.to_string(index=False, formatters={"MAE": "{:.2f}".format, "RMSE": "{:.2f}".format, "WAPE": "{:.4f}".format, "Accuracy": "{:.2%}".format}))
    print(f"WINNER={winner}")


if __name__ == "__main__":
    main()
