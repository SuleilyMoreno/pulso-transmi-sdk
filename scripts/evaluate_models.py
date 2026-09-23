"""Compara modelos de pronóstico con una validación temporal de 7 días."""

from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error
from sklearn.preprocessing import OneHotEncoder
from sklearn.compose import ColumnTransformer
from sklearn.pipeline import make_pipeline


ROOT = Path(__file__).resolve().parents[1]


def scores(y_true: pd.Series, y_pred: pd.Series) -> dict[str, float]:
    y_pred = y_pred.clip(lower=0)
    wape = (y_true - y_pred).abs().sum() / y_true.sum()
    return {
        "MAE": mean_absolute_error(y_true, y_pred),
        "RMSE": mean_squared_error(y_true, y_pred) ** 0.5,
        "WAPE": wape,
        "Accuracy": max(0.0, 1 - wape),
    }


def main() -> None:
    obs = pd.read_csv(ROOT / "artifacts/observations.csv", dtype={"station_id": "string"}, parse_dates=["observed_at"])
    context = pd.read_csv(ROOT / "artifacts/context.csv", parse_dates=["observed_at"])
    df = obs.merge(context, on="observed_at", how="left").sort_values(["station_id", "observed_at"]).reset_index(drop=True)
    df["slot"] = df["observed_at"].dt.hour * 4 + df["observed_at"].dt.minute // 15
    df["dow"] = df["observed_at"].dt.dayofweek
    df["is_weekend"] = (df["dow"] >= 5).astype(int)
    group = df.groupby("station_id", sort=False)["demand"]
    for lag in (1, 4, 96, 672):
        df[f"lag_{lag}"] = group.shift(lag)
    df["rolling_96"] = group.transform(lambda s: s.shift(1).rolling(96, min_periods=24).mean())
    cutoff = df["observed_at"].max() - pd.Timedelta(days=7)
    train = df[df["observed_at"] <= cutoff].copy()
    valid = df[df["observed_at"] > cutoff].copy()
    print(f"Entrenamiento: {len(train):,} filas | Validación temporal: {len(valid):,} filas | cutoff: {cutoff}")

    predictions: dict[str, pd.Series] = {}
    predictions["Naive_24h"] = valid["lag_96"]
    slot_mean = train.groupby(["station_id", "slot"])["demand"].mean()
    predictions["Mean_station_slot"] = pd.Series([slot_mean.get((s, sl), train["demand"].mean()) for s, sl in zip(valid.station_id, valid.slot)], index=valid.index)

    features = ["lag_1", "lag_4", "lag_96", "lag_672", "rolling_96", "slot", "dow", "is_weekend", "rain_mm", "rain_forecast", "temperature_c", "temperature_forecast", "event_intensity"]
    model_train = train.dropna(subset=features)
    model_valid = valid.dropna(subset=features)
    transformer = ColumnTransformer([("station", OneHotEncoder(handle_unknown="ignore"), ["station_id"])], remainder="passthrough")
    ridge = make_pipeline(transformer, Ridge(alpha=10.0))
    ridge.fit(model_train[["station_id"] + features], model_train["demand"])
    predictions["Ridge_lags_context"] = pd.Series(ridge.predict(model_valid[["station_id"] + features]), index=model_valid.index)

    hgb = HistGradientBoostingRegressor(max_iter=250, learning_rate=0.06, max_leaf_nodes=31, l2_regularization=1.0, random_state=42)
    hgb.fit(model_train[features], model_train["demand"])
    predictions["HistGradientBoosting"] = pd.Series(hgb.predict(model_valid[features]), index=model_valid.index)

    result_rows = []
    for name, pred in predictions.items():
        aligned = valid.loc[pred.index]
        metric = scores(aligned["demand"], pred)
        result_rows.append({"model": name, **metric})
    result = pd.DataFrame(result_rows).sort_values("WAPE")
    result.to_csv(ROOT / "artifacts/model_metrics.csv", index=False)
    print(result.to_string(index=False, formatters={"MAE": "{:.2f}".format, "RMSE": "{:.2f}".format, "WAPE": "{:.4f}".format, "Accuracy": "{:.2%}".format}))


if __name__ == "__main__":
    main()
