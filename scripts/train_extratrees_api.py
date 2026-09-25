"""Entrena ExtraTrees usando directamente los datos de la API Pulso TransMi."""

import os
from datetime import datetime, timezone
from pathlib import Path

import httpx
import joblib
import pandas as pd
from sklearn.ensemble import ExtraTreesRegressor


ROOT = Path(__file__).resolve().parents[1]
API_URL = "https://pulso-transmi.72-60-245-2.sslip.io"
FEATURES = [
    "lag_1", "lag_4", "lag_96", "lag_672", "rolling_96", "slot", "dow",
    "is_weekend", "rain_mm", "rain_forecast", "temperature_c",
    "temperature_forecast", "event_intensity",
]


def get_all(client: httpx.Client, endpoint: str) -> list[dict]:
    rows = []
    cursor = None
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


def main() -> None:
    api_key = os.environ["PULSO_API_KEY"]
    headers = {
        "Authorization": f"Bearer {api_key}",
        "User-Agent": "pulso-transmi-model-versioning/1.0",
    }

    with httpx.Client(base_url=API_URL, headers=headers, timeout=90) as client:
        observations = pd.DataFrame(get_all(client, "/v1/observations"))
        context = pd.DataFrame(get_all(client, "/v1/context"))

    observations["observed_at"] = pd.to_datetime(observations["observed_at"], utc=True)
    observations["station_id"] = observations["station_id"].astype("string")
    context["observed_at"] = pd.to_datetime(context["observed_at"], utc=True)

    frame = observations.merge(context, on="observed_at", how="left")
    frame = frame.sort_values(["station_id", "observed_at"]).reset_index(drop=True)
    frame["slot"] = frame["observed_at"].dt.hour * 4 + frame["observed_at"].dt.minute // 15
    frame["dow"] = frame["observed_at"].dt.dayofweek
    frame["is_weekend"] = (frame["dow"] >= 5).astype(int)

    grouped = frame.groupby("station_id", sort=False)["demand"]
    for lag in (1, 4, 96, 672):
        frame[f"lag_{lag}"] = grouped.shift(lag)
    frame["rolling_96"] = grouped.transform(
        lambda values: values.shift(1).rolling(96, min_periods=24).mean()
    )

    station_codes = {
        station: index for index, station in enumerate(sorted(frame["station_id"].unique()))
    }
    frame["station_code"] = frame["station_id"].map(station_codes)
    columns = ["station_code"] + FEATURES
    train = frame.dropna(subset=columns + ["demand"])
    if train.empty:
        raise RuntimeError("La API no devolvió suficientes datos para entrenar el modelo.")

    model = ExtraTreesRegressor(
        n_estimators=400,
        min_samples_leaf=2,
        max_features=0.9,
        n_jobs=-1,
        random_state=42,
    )
    model.fit(train[columns], train["demand"])

    output = ROOT / "artifacts" / "extratrees_model.joblib"
    output.parent.mkdir(parents=True, exist_ok=True)
    artifact = {
        "model": model,
        "features": columns,
        "station_codes": station_codes,
        "trained_rows": len(train),
        "target": "demand",
        "frequency": "15min",
        "source": API_URL,
        "trained_at": datetime.now(timezone.utc).isoformat(),
    }
    joblib.dump(artifact, output, compress=3)
    print(f"Filas de entrenamiento: {len(train):,}")
    print(f"Estaciones: {len(station_codes)}")
    print(f"Modelo guardado: {output}")


if __name__ == "__main__":
    main()
