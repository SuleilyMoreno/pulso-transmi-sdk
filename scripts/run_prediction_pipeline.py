"""Pipeline reproducible: ingesta, entrena ExtraTrees con todos los datos y envía submission."""

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
FEATURES = [
    "lag_1", "lag_4", "lag_96", "lag_672", "rolling_96",
    "slot", "dow", "is_weekend",
    "rain_mm", "rain_forecast", "temperature_c", "temperature_forecast", "event_intensity",
]


def get_all(client: httpx.Client, endpoint: str, params: dict) -> list[dict]:
    rows, cursor = [], None
    while True:
        p = {**params, "limit": 5000}
        if cursor:
            p["cursor"] = cursor
        page = client.get(endpoint, params=p)
        page.raise_for_status()
        payload = page.json()
        rows.extend(payload["data"])
        cursor = payload.get("next_cursor")
        if cursor is None:
            return rows


def normalize_station_id(value: object) -> str:
    """Conserva los IDs de estación como texto de cinco dígitos."""
    return str(value).strip().zfill(5)


def get_observations(client: httpx.Client) -> pd.DataFrame:
    """Combina historia y observaciones reales liberadas por el stream."""
    historical = get_all(client, "/v1/observations", {})
    released = get_all(client, "/v1/stream/observations", {})
    rows = historical + [
        {key: row[key] for key in ("station_id", "observed_at", "demand")}
        for row in released
    ]
    return pd.DataFrame(rows).drop_duplicates(["station_id", "observed_at"], keep="last")


def build_features(
    obs_df: pd.DataFrame,
    context_df: pd.DataFrame,
    station_codes: dict,
) -> pd.DataFrame:
    """Construye el DataFrame de features para entrenamiento."""
    df = obs_df.copy()
    df["slot"] = df.observed_at.dt.hour * 4 + df.observed_at.dt.minute // 15
    df["dow"] = df.observed_at.dt.dayofweek
    df["is_weekend"] = (df.dow >= 5).astype(int)
    df["station_code"] = df.station_id.map(station_codes)

    grouped = df.groupby("station_id", sort=False).demand
    for lag in (1, 4, 96, 672):
        df[f"lag_{lag}"] = grouped.shift(lag)
    df["rolling_96"] = grouped.transform(
        lambda s: s.shift(1).rolling(96, min_periods=24).mean()
    )

    # Merge con contexto por timestamp exacto
    context = context_df[["observed_at", "rain_mm", "rain_forecast",
                          "temperature_c", "temperature_forecast", "event_intensity"]]
    df = pd.merge_asof(
        df.sort_values("observed_at"),
        context.sort_values("observed_at"),
        on="observed_at",
        direction="backward",
    )
    return df


def predict_target(
    station_id: str,
    target_at: pd.Timestamp,
    obs_by_station: dict,
    context_df: pd.DataFrame,
    station_codes: dict,
    model,
) -> float:
    """Predice la demanda para un target_at específico de una estación."""
    station_id = normalize_station_id(station_id)
    history = obs_by_station.get(station_id, pd.Series([], dtype=float))

    # Lags: calculados respecto al punto exacto del target
    # Los valores de lag_N son los N periodos antes del cutoff (no del target)
    # porque el modelo se entrena así. Usamos los últimos valores disponibles.
    n = len(history)

    def safe_lag(i):
        idx = n - i
        return float(history.iloc[idx]) if 0 <= idx < n else 0.0

    lag_1 = safe_lag(1)
    lag_4 = safe_lag(4)
    lag_96 = safe_lag(96)
    lag_672 = safe_lag(672)
    rolling_96 = float(history.iloc[max(0, n-96):n].mean()) if n > 0 else 0.0

    # Contexto: buscar el row más cercano al target_at (hacia atrás)
    ctx = context_df[context_df.observed_at <= target_at]
    if ctx.empty:
        ctx_row = context_df.iloc[0]
    else:
        ctx_row = ctx.iloc[-1]

    row = {
        "station_code": station_codes.get(station_id, 0),
        "lag_1": lag_1,
        "lag_4": lag_4,
        "lag_96": lag_96,
        "lag_672": lag_672,
        "rolling_96": rolling_96,
        "slot": target_at.hour * 4 + target_at.minute // 15,
        "dow": target_at.dayofweek,
        "is_weekend": int(target_at.dayofweek >= 5),
        "rain_mm": ctx_row.rain_mm,
        "rain_forecast": ctx_row.rain_forecast,
        "temperature_c": ctx_row.temperature_c,
        "temperature_forecast": ctx_row.temperature_forecast,
        "event_intensity": ctx_row.event_intensity,
    }

    cols = ["station_code"] + FEATURES
    value = float(model.predict(pd.DataFrame([row])[cols])[0])
    return max(value, 0.0)


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

    with httpx.Client(base_url=API_URL, headers=headers, timeout=120) as client:
        # 1. Verificar ciclo abierto
        cycle_resp = client.get("/v1/forecast-cycles/current")
        cycle_resp.raise_for_status()
        cycle = cycle_resp.json()

        if cycle.get("state") != "open":
            raise RuntimeError(f"El ciclo no está abierto: {cycle.get('state')}")

        cutoff = pd.Timestamp(cycle["data_cutoff"]).tz_localize("UTC") \
            if pd.Timestamp(cycle["data_cutoff"]).tzinfo is None \
            else pd.Timestamp(cycle["data_cutoff"])

        print(f"Ciclo: {cycle['cycle_id']} | cutoff: {cutoff} | cierra: {cycle['closes_at']}")

        # 2. Descargar TODOS los datos disponibles (sin filtro de fecha)
        print("Descargando observaciones...")
        observations = get_observations(client)
        print("Descargando contexto...")
        context = pd.DataFrame(get_all(client, "/v1/context", {}))

    # 3. Preparar datos
    observations["observed_at"] = pd.to_datetime(observations["observed_at"], utc=True)
    observations["station_id"] = observations["station_id"].map(normalize_station_id)
    context["observed_at"] = pd.to_datetime(context["observed_at"], utc=True)

    # Filtrar hasta el cutoff del ciclo
    obs_train = observations[observations.observed_at <= cutoff].sort_values(
        ["station_id", "observed_at"]
    )
    ctx_train = context[context.observed_at <= cutoff].sort_values("observed_at")

    print(f"Datos de entrenamiento: {len(obs_train)} observaciones, {len(ctx_train)} contextos")
    print(f"Rango: {obs_train.observed_at.min()} → {obs_train.observed_at.max()}")

    if obs_train.empty or ctx_train.empty:
        raise RuntimeError("La API no devolvió observaciones y contexto hasta el cutoff")

    station_codes = {s: i for i, s in enumerate(sorted(obs_train.station_id.unique()))}

    # 4. Construir features y entrenar
    train_df = build_features(obs_train, ctx_train, station_codes)
    cols = ["station_code"] + FEATURES
    train_df = train_df.dropna(subset=cols + ["demand"])

    print(f"Filas de entrenamiento (sin NaN): {len(train_df)}")

    model = ExtraTreesRegressor(
        n_estimators=400,
        min_samples_leaf=2,
        max_features=0.9,
        n_jobs=-1,
        random_state=42,
    )
    model.fit(train_df[cols], train_df.demand)
    print("Modelo entrenado.")

    # 5. Guardar modelo
    model_path = ROOT / "artifacts" / "extratrees_model.joblib"
    model_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, model_path)

    # 6. Construir historial por estación para predicción
    obs_by_station = {
        sid: grp.sort_values("observed_at").demand.reset_index(drop=True)
        for sid, grp in obs_train.groupby("station_id")
    }

    # 7. Predecir targets del ciclo
    targets = cycle.get("targets") or []
    if not targets:
        raise RuntimeError("El ciclo abierto no contiene targets")

    predictions = []
    for target in cycle["targets"]:
        station_id = normalize_station_id(target["station_id"])
        target_at = pd.Timestamp(target["target_at"]).tz_localize("UTC") \
            if pd.Timestamp(target["target_at"]).tzinfo is None \
            else pd.Timestamp(target["target_at"])

        value = predict_target(
            station_id, target_at, obs_by_station, ctx_train, station_codes, model
        )
        predictions.append({
            "station_id": station_id,
            "target_at": target["target_at"],
            "value": round(value, 4),
        })

    expected_keys = {
        (normalize_station_id(t["station_id"]), str(t["target_at"]))
        for t in targets
    }
    actual_keys = {(p["station_id"], p["target_at"]) for p in predictions}
    if actual_keys != expected_keys or len(predictions) != len(targets):
        raise RuntimeError(
            f"Cobertura de targets inválida: {len(predictions)} predicciones para "
            f"{len(targets)} targets"
        )

    print(f"Predicciones generadas: {len(predictions)}")

    # 8. Construir payload y enviar
    git_commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()

    client_run_id = f"extratrees-pipeline-{cycle['cycle_id']}-{uuid.uuid4().hex}"
    payload = {
        "schema_version": "1.0",
        "cycle_id": cycle["cycle_id"],
        "client_run_id": client_run_id,
        "data_cutoff": cycle["data_cutoff"],
        "model": {
            "version": "extratrees-pipeline-2.0",
            "trained_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "training_data_end": cycle["data_cutoff"],
            "git_commit": git_commit,
        },
        "predictions": predictions,
    }

    if dry_run:
        print({"status": "dry_run", "cycle_id": cycle["cycle_id"], "predictions": len(predictions)})
        return

    with httpx.Client(base_url=API_URL, headers=headers, timeout=60) as client:
        response = client.post(
            "/v1/submissions",
            headers={"Idempotency-Key": payload["client_run_id"]},
            json=payload,
        )
        if response.status_code == 409:
            print(f"El ciclo {cycle['cycle_id']} ya tiene una submission; se omite el duplicado.")
            print(response.text)
            return
        response.raise_for_status()
        response_body = response.json()
        if response_body.get("status") != "accepted":
            raise RuntimeError(f"La API no aceptó la submission: {response_body}")
        if response_body.get("predictions_received") != len(predictions):
            raise RuntimeError(
                f"La API recibió {response_body.get('predictions_received')} predicciones; "
                f"se esperaban {len(predictions)}"
            )
        if response_body.get("is_official") is not True:
            raise RuntimeError("La submission no fue marcada como oficial")
        print(response.text)
        persist_estimates(payload, response_body)


if __name__ == "__main__":
    main()
