"""Busca modelos alternativos usando exactamente la misma partición temporal."""

from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesRegressor, HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error


ROOT = Path(__file__).resolve().parents[1]
FEATURES = ["lag_1", "lag_2", "lag_3", "lag_4", "lag_5", "lag_8", "lag_12", "lag_16", "lag_96", "lag_192", "lag_288", "lag_672", "lag_1344", "rolling_4", "rolling_12", "rolling_24", "rolling_96", "rolling_672", "slot", "dow", "is_weekend", "rain_mm", "rain_forecast", "temperature_c", "temperature_forecast", "event_intensity"]


def make_data():
    obs = pd.read_csv(ROOT / "artifacts/observations.csv", dtype={"station_id": "string"}, parse_dates=["observed_at"])
    context = pd.read_csv(ROOT / "artifacts/context.csv", parse_dates=["observed_at"])
    df = obs.merge(context, on="observed_at").sort_values(["station_id", "observed_at"]).reset_index(drop=True)
    df["slot"] = df.observed_at.dt.hour * 4 + df.observed_at.dt.minute // 15
    df["dow"] = df.observed_at.dt.dayofweek
    df["is_weekend"] = (df.dow >= 5).astype(int)
    group = df.groupby("station_id", sort=False).demand
    for lag in (1, 2, 3, 4, 5, 8, 12, 16, 96, 192, 288, 672, 1344):
        df[f"lag_{lag}"] = group.shift(lag)
    for window in (4, 12, 24, 96, 672):
        df[f"rolling_{window}"] = group.transform(lambda s, w=window: s.shift(1).rolling(w, min_periods=max(2, w // 4)).mean())
    cutoff = df.observed_at.max() - pd.Timedelta(days=7)
    train = df[df.observed_at <= cutoff].dropna(subset=FEATURES)
    valid = df[df.observed_at > cutoff].dropna(subset=FEATURES)
    # One-hot station identity without leaking validation targets.
    station_codes = {s: i for i, s in enumerate(sorted(df.station_id.unique()))}
    train = train.copy(); valid = valid.copy()
    train["station_code"] = train.station_id.map(station_codes)
    valid["station_code"] = valid.station_id.map(station_codes)
    return train, valid, cutoff


def metrics(y, p):
    p = np.maximum(p, 0)
    wape = np.abs(y - p).sum() / y.sum()
    return {"MAE": mean_absolute_error(y, p), "RMSE": mean_squared_error(y, p) ** 0.5, "WAPE": wape, "Accuracy": max(0, 1 - wape)}


def main():
    train, valid, cutoff = make_data()
    xcols = ["station_code"] + FEATURES
    models = {
        "ExtraTrees": ExtraTreesRegressor(n_estimators=250, min_samples_leaf=2, max_features=0.9, n_jobs=-1, random_state=42),
        "RandomForest": RandomForestRegressor(n_estimators=200, min_samples_leaf=2, max_features=0.8, n_jobs=-1, random_state=42),
        "HGB_leaf15": HistGradientBoostingRegressor(max_iter=400, learning_rate=0.05, max_leaf_nodes=15, l2_regularization=2.0, random_state=42),
        "HGB_leaf63": HistGradientBoostingRegressor(max_iter=300, learning_rate=0.05, max_leaf_nodes=63, l2_regularization=2.0, random_state=42),
        "HGB_fast": HistGradientBoostingRegressor(max_iter=250, learning_rate=0.10, max_leaf_nodes=31, l2_regularization=1.0, random_state=42),
    }
    out = []
    for name, model in models.items():
        model.fit(train[xcols], train.demand)
        out.append({"model": name, **metrics(valid.demand, model.predict(valid[xcols]))})
    result = pd.DataFrame(out).sort_values("WAPE")
    result.to_csv(ROOT / "artifacts/model_metrics_extended.csv", index=False)
    print(f"cutoff={cutoff} | train={len(train):,} | validation={len(valid):,}")
    print(result.to_string(index=False, formatters={"MAE": "{:.2f}".format, "RMSE": "{:.2f}".format, "WAPE": "{:.4f}".format, "Accuracy": "{:.2%}".format}))


if __name__ == "__main__":
    main()
