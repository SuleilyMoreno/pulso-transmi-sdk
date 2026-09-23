"""Entrena ExtraTrees y registra el experimento localmente con MLflow."""

from pathlib import Path

import joblib
import mlflow
import mlflow.sklearn
import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error


ROOT = Path(__file__).resolve().parents[1]
FEATURES = ["lag_1", "lag_4", "lag_96", "lag_672", "rolling_96", "slot", "dow", "is_weekend", "rain_mm", "rain_forecast", "temperature_c", "temperature_forecast", "event_intensity"]


def prepare() -> tuple[pd.DataFrame, dict[str, int]]:
    obs = pd.read_csv(ROOT / "artifacts/observations.csv", dtype={"station_id": "string"}, parse_dates=["observed_at"])
    context = pd.read_csv(ROOT / "artifacts/context.csv", parse_dates=["observed_at"])
    df = obs.merge(context, on="observed_at", how="left").sort_values(["station_id", "observed_at"]).reset_index(drop=True)
    df["slot"] = df.observed_at.dt.hour * 4 + df.observed_at.dt.minute // 15
    df["dow"] = df.observed_at.dt.dayofweek
    df["is_weekend"] = (df.dow >= 5).astype(int)
    grouped = df.groupby("station_id", sort=False).demand
    for lag in (1, 4, 96, 672):
        df[f"lag_{lag}"] = grouped.shift(lag)
    df["rolling_96"] = grouped.transform(lambda values: values.shift(1).rolling(96, min_periods=24).mean())
    station_codes = {s: i for i, s in enumerate(sorted(df.station_id.unique()))}
    df["station_code"] = df.station_id.map(station_codes)
    return df, station_codes


def metric_values(y: pd.Series, prediction: np.ndarray) -> dict[str, float]:
    prediction = np.maximum(prediction, 0)
    wape = np.abs(y - prediction).sum() / y.sum()
    return {"mae": mean_absolute_error(y, prediction), "rmse": mean_squared_error(y, prediction) ** 0.5, "wape": wape, "accuracy": max(0.0, 1.0 - wape)}


def main() -> None:
    mlflow.set_tracking_uri(f"sqlite:///{ROOT / 'mlflow.db'}")
    mlflow.set_experiment("pulso-transmi-demand")
    df, station_codes = prepare()
    cutoff = df.observed_at.max() - pd.Timedelta(days=7)
    train = df[df.observed_at <= cutoff].dropna(subset=["station_code"] + FEATURES)
    valid = df[df.observed_at > cutoff].dropna(subset=["station_code"] + FEATURES)
    columns = ["station_code"] + FEATURES
    params = {"n_estimators": 400, "min_samples_leaf": 2, "max_features": 0.9, "random_state": 42}

    with mlflow.start_run(run_name="extratrees_temporal_validation") as run:
        model = ExtraTreesRegressor(n_jobs=-1, **params)
        model.fit(train[columns], train.demand)
        metrics = metric_values(valid.demand, model.predict(valid[columns]))
        mlflow.log_params({**params, "validation_days": 7, "frequency": "15min", "feature_count": len(columns), "train_rows": len(train), "validation_rows": len(valid)})
        mlflow.log_metrics(metrics)
        mlflow.set_tags({"model_family": "ExtraTreesRegressor", "validation": "last_7_days_temporal", "target": "demand"})

        artifact = {"model": model, "features": columns, "station_codes": station_codes, "target": "demand", "frequency": "15min"}
        model_path = ROOT / "artifacts/extratrees_model.joblib"
        joblib.dump(artifact, model_path, compress=3)
        mlflow.log_artifact(str(model_path), artifact_path="joblib")
        mlflow.sklearn.log_model(model, "model", skops_trusted_types=["sklearn.tree._tree.Tree"])
        print(f"run_id={run.info.run_id}")
        print(f"experiment_uri={ROOT / 'mlruns'}")
        print("metrics=", {key: round(value, 6) for key, value in metrics.items()})


if __name__ == "__main__":
    main()
