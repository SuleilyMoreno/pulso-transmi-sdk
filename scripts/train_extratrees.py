"""Entrena ExtraTrees con todos los datos disponibles y guarda el modelo."""

from pathlib import Path

import joblib
import pandas as pd
from sklearn.ensemble import ExtraTreesRegressor


ROOT = Path(__file__).resolve().parents[1]
FEATURES = [
    "lag_1", "lag_4", "lag_96", "lag_672", "rolling_96", "slot", "dow",
    "is_weekend", "rain_mm", "rain_forecast", "temperature_c",
    "temperature_forecast", "event_intensity",
]


def build_frame() -> pd.DataFrame:
    observations = pd.read_csv(ROOT / "artifacts/observations.csv", dtype={"station_id": "string"}, parse_dates=["observed_at"])
    context = pd.read_csv(ROOT / "artifacts/context.csv", parse_dates=["observed_at"])
    frame = observations.merge(context, on="observed_at", how="left").sort_values(["station_id", "observed_at"]).reset_index(drop=True)
    frame["slot"] = frame["observed_at"].dt.hour * 4 + frame["observed_at"].dt.minute // 15
    frame["dow"] = frame["observed_at"].dt.dayofweek
    frame["is_weekend"] = (frame["dow"] >= 5).astype(int)
    grouped = frame.groupby("station_id", sort=False)["demand"]
    for lag in (1, 4, 96, 672):
        frame[f"lag_{lag}"] = grouped.shift(lag)
    frame["rolling_96"] = grouped.transform(lambda values: values.shift(1).rolling(96, min_periods=24).mean())
    station_codes = {station: index for index, station in enumerate(sorted(frame["station_id"].unique()))}
    frame["station_code"] = frame["station_id"].map(station_codes)
    frame.attrs["station_codes"] = station_codes
    return frame


def main() -> None:
    frame = build_frame()
    columns = ["station_code"] + FEATURES
    train = frame.dropna(subset=columns + ["demand"])
    model = ExtraTreesRegressor(
        n_estimators=400,
        min_samples_leaf=2,
        max_features=0.9,
        n_jobs=-1,
        random_state=42,
    )
    model.fit(train[columns], train["demand"])
    artifact = {
        "model": model,
        "features": columns,
        "station_codes": frame.attrs["station_codes"],
        "trained_rows": len(train),
        "target": "demand",
        "frequency": "15min",
    }
    output = ROOT / "artifacts/extratrees_model.joblib"
    joblib.dump(artifact, output, compress=3)
    print(f"Filas de entrenamiento: {len(train):,}")
    print(f"Estaciones: {len(artifact['station_codes'])}")
    print(f"Modelo guardado: {output}")


if __name__ == "__main__":
    main()
