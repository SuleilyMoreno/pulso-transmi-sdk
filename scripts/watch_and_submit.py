"""
Monitorea el ciclo y envía predicciones automáticamente cuando se abre.

Uso:
    PULSO_API_KEY=<tu_key> python scripts/watch_and_submit.py

Opciones de entorno:
    PULSO_DRY_RUN=1        → muestra el payload pero NO envía
    PULSO_POLL_SECS=30     → intervalo de polling (default: 30s)
    PULSO_MAX_WAIT_MINS=60 → máximo tiempo esperando ciclo (default: 60 min)
"""

import os
import subprocess
import time
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
    "lag_1", "lag_2", "lag_3", "lag_4", "lag_5", "lag_8", "lag_12", "lag_16",
    "lag_96", "lag_192", "lag_288", "lag_672", "lag_1344",
    "rolling_4", "rolling_12", "rolling_24", "rolling_96", "rolling_672",
    "slot", "dow", "is_weekend",
    "rain_mm", "rain_forecast", "temperature_c", "temperature_forecast", "event_intensity",
]

# ─────────────────────────────────────────────────────────────────────────────
# Helpers de API
# ─────────────────────────────────────────────────────────────────────────────

def make_client(api_key: str) -> httpx.Client:
    """Cliente httpx con timeout generoso y reintentos implícitos."""
    return httpx.Client(
        base_url=API_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "pulso-transmi-watch/1.0",
        },
        timeout=httpx.Timeout(connect=30.0, read=120.0, write=60.0, pool=30.0),
    )


def get_cycle(client: httpx.Client) -> dict | None:
    """Devuelve el ciclo abierto o None si no existe."""
    try:
        r = client.get("/v1/forecast-cycles/current")
        if r.status_code == 404:
            return None
        r.raise_for_status()
        data = r.json()
        if data.get("state") != "open":
            print(f"  Ciclo existe pero estado={data.get('state')} — esperando...")
            return None
        return data
    except httpx.ConnectTimeout:
        print("  Timeout conectando a la API — reintentando...")
        return None
    except httpx.HTTPStatusError as e:
        print(f"  Error HTTP {e.response.status_code}: {e.response.text[:200]}")
        return None


def get_all_pages(client: httpx.Client, endpoint: str, params: dict = {}) -> list[dict]:
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
    return str(value).strip().zfill(5)


# ─────────────────────────────────────────────────────────────────────────────
# Feature engineering
# ─────────────────────────────────────────────────────────────────────────────

def build_features(obs_df: pd.DataFrame, context_df: pd.DataFrame, station_codes: dict) -> pd.DataFrame:
    df = obs_df.copy().sort_values(["station_id", "observed_at"]).reset_index(drop=True)
    df["slot"] = df.observed_at.dt.hour * 4 + df.observed_at.dt.minute // 15
    df["dow"] = df.observed_at.dt.dayofweek
    df["is_weekend"] = (df.dow >= 5).astype(int)
    df["station_code"] = df.station_id.map(station_codes)

    grouped = df.groupby("station_id", sort=False).demand
    for lag in (1, 2, 3, 4, 5, 8, 12, 16, 96, 192, 288, 672, 1344):
        df[f"lag_{lag}"] = grouped.shift(lag)
    for window in (4, 12, 24, 96, 672):
        df[f"rolling_{window}"] = grouped.transform(
            lambda s, w=window: s.shift(1).rolling(w, min_periods=max(2, w // 4)).mean()
        )

    ctx = context_df[["observed_at", "rain_mm", "rain_forecast",
                       "temperature_c", "temperature_forecast", "event_intensity"]]
    df = pd.merge_asof(
        df.sort_values("observed_at"),
        ctx.sort_values("observed_at"),
        on="observed_at",
        direction="backward",
    )
    return df


def predict_single(station_id: str, target_at: pd.Timestamp,
                   obs_by_station: dict, context_df: pd.DataFrame,
                   station_codes: dict, model) -> tuple[float, dict]:
    station_id = normalize_station_id(station_id)
    history = obs_by_station.get(station_id, pd.Series([], dtype=float))
    n = len(history)

    def safe_lag(i: int) -> float:
        idx = n - i
        return float(history.iloc[idx]) if 0 <= idx < n else 0.0

    lags = {f"lag_{i}": safe_lag(i) for i in (1, 2, 3, 4, 5, 8, 12, 16, 96, 192, 288, 672, 1344)}
    rolling = {
        f"rolling_{w}": float(history.iloc[max(0, n - w):n].mean()) if n else 0.0
        for w in (4, 12, 24, 96, 672)
    }

    ctx = context_df[context_df.observed_at <= target_at]
    ctx_row = ctx.iloc[-1] if not ctx.empty else context_df.iloc[0]

    row = {
        "station_code": station_codes.get(station_id, 0),
        **lags,
        **rolling,
        "slot": target_at.hour * 4 + target_at.minute // 15,
        "dow": target_at.dayofweek,
        "is_weekend": int(target_at.dayofweek >= 5),
        "rain_mm": float(ctx_row.rain_mm),
        "rain_forecast": float(ctx_row.rain_forecast),
        "temperature_c": float(ctx_row.temperature_c),
        "temperature_forecast": float(ctx_row.temperature_forecast),
        "event_intensity": float(ctx_row.event_intensity),
    }

    cols = ["station_code"] + FEATURES
    value = float(np.maximum(model.predict(pd.DataFrame([row])[cols])[0], 0))
    return value, row


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline principal
# ─────────────────────────────────────────────────────────────────────────────

def run_pipeline(api_key: str, cycle: dict, dry_run: bool) -> None:
    print(f"\n{'='*60}")
    print(f"CICLO ABIERTO: {cycle['cycle_id']}")
    print(f"  cutoff  : {cycle['data_cutoff']}")
    print(f"  cierra  : {cycle['closes_at']}")
    print(f"  targets : {len(cycle.get('targets', []))}")
    print(f"{'='*60}")

    with make_client(api_key) as client:
        # 1. Descargar datos frescos de la API
        print("\n[1/5] Descargando observaciones...")
        historical = get_all_pages(client, "/v1/observations")
        try:
            released = get_all_pages(client, "/v1/stream/observations")
            all_obs = historical + [
                {k: r[k] for k in ("station_id", "observed_at", "demand")}
                for r in released
            ]
        except Exception:
            print("  Stream no disponible, usando solo historial.")
            all_obs = historical

        print("[2/5] Descargando contexto...")
        context_raw = get_all_pages(client, "/v1/context")

    obs = pd.DataFrame(all_obs)
    obs["observed_at"] = pd.to_datetime(obs["observed_at"], utc=True)
    obs["station_id"] = obs["station_id"].map(normalize_station_id)
    obs = obs.drop_duplicates(["station_id", "observed_at"], keep="last")

    ctx = pd.DataFrame(context_raw)
    ctx["observed_at"] = pd.to_datetime(ctx["observed_at"], utc=True)

    cutoff = pd.Timestamp(cycle["data_cutoff"])
    if cutoff.tzinfo is None:
        cutoff = cutoff.tz_localize("UTC")

    obs_train = obs[obs.observed_at <= cutoff].sort_values(["station_id", "observed_at"])
    print(f"  Observaciones hasta cutoff: {len(obs_train):,} | Contexto: {len(ctx):,}")

    if obs_train.empty:
        raise RuntimeError("Sin observaciones hasta el cutoff — verifica la API.")

    station_codes = {s: i for i, s in enumerate(sorted(obs.station_id.unique()))}

    # 2. Entrenar modelo por horizonte
    print("[3/5] Entrenando modelos por horizonte...")
    feature_df = build_features(obs_train, ctx, station_codes)
    cols = ["station_code"] + FEATURES
    models: dict[int, ExtraTreesRegressor] = {}

    targets_list = cycle.get("targets", [])
    horizons = sorted({
        int(round(
            (pd.Timestamp(t["target_at"], tz="UTC") - cutoff).total_seconds() / 60
        ))
        for t in targets_list
    })
    if not horizons:
        raise RuntimeError("El ciclo no tiene targets.")

    for horizon in horizons:
        steps = horizon // 15
        train_df = feature_df.copy()
        train_df["target"] = train_df.groupby("station_id", sort=False).demand.shift(-steps)
        train_df = train_df[train_df.observed_at <= cutoff].dropna(subset=cols + ["target"])
        model = ExtraTreesRegressor(
            n_estimators=400, min_samples_leaf=2, max_features=0.9,
            n_jobs=-1, random_state=42 + steps,
        )
        model.fit(train_df[cols], train_df["target"])
        models[horizon] = model
        print(f"  Horizonte {horizon} min → {len(train_df):,} filas de entrenamiento")

    # Guardar modelo
    joblib.dump(models, ROOT / "artifacts" / "extratrees_model.joblib")
    print("  Modelos guardados en artifacts/extratrees_model.joblib")

    # 3. Predecir
    print("[4/5] Generando predicciones...")
    obs_by_station = {
        sid: grp.sort_values("observed_at")["demand"].reset_index(drop=True)
        for sid, grp in obs_train.groupby("station_id")
    }

    predictions, feature_rows = [], []
    for target in targets_list:
        sid = normalize_station_id(target["station_id"])
        t_at = pd.Timestamp(target["target_at"])
        if t_at.tzinfo is None:
            t_at = t_at.tz_localize("UTC")
        horizon = int(round((t_at - cutoff).total_seconds() / 60))

        if horizon not in models:
            raise RuntimeError(f"Horizonte {horizon} min no tiene modelo entrenado.")

        value, feats = predict_single(sid, t_at, obs_by_station, ctx, station_codes, models[horizon])
        predictions.append({
            "station_id": sid,
            "target_at": target["target_at"],
            "value": round(value, 4),
        })
        feature_rows.append({"station_id": sid, "target_at": target["target_at"], "features": feats})

    print(f"  {len(predictions)} predicciones generadas para {len(horizons)} horizonte(s)")

    # Validar cobertura
    expected = {(normalize_station_id(t["station_id"]), str(t["target_at"])) for t in targets_list}
    actual = {(p["station_id"], p["target_at"]) for p in predictions}
    missing = expected - actual
    if missing:
        raise RuntimeError(f"Faltan {len(missing)} predicciones: {list(missing)[:5]}")

    # 4. Enviar
    git_commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()

    client_run_id = f"extratrees-watch-{cycle['cycle_id']}-{uuid.uuid4().hex[:8]}"
    payload = {
        "schema_version": "1.0",
        "cycle_id": cycle["cycle_id"],
        "client_run_id": client_run_id,
        "data_cutoff": cycle["data_cutoff"],
        "model": {
            "version": "extratrees-watch-2.1",
            "trained_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "training_data_end": cycle["data_cutoff"],
            "git_commit": git_commit,
        },
        "predictions": predictions,
    }

    if dry_run:
        print("\n[DRY RUN] Payload que se enviaría:")
        import json
        print(json.dumps({**payload, "predictions": payload["predictions"][:3]}, indent=2))
        print(f"  ... ({len(predictions)} predicciones en total)")
        return

    print("[5/5] Enviando submission...")
    with make_client(api_key) as client:
        response = client.post(
            "/v1/submissions",
            headers={"Idempotency-Key": client_run_id},
            json=payload,
        )
        if response.status_code == 409:
            print(f"  ⚠️  Ciclo {cycle['cycle_id']} ya tiene submission — se omite duplicado.")
            print(f"  {response.text}")
            return
        response.raise_for_status()
        body = response.json()

    if body.get("status") != "accepted":
        raise RuntimeError(f"API no aceptó la submission: {body}")
    if body.get("predictions_received") != len(predictions):
        raise RuntimeError(
            f"API recibió {body.get('predictions_received')} preds, se enviaron {len(predictions)}"
        )
    if body.get("is_official") is not True:
        raise RuntimeError("Submission no marcada como oficial.")

    print(f"\n✅ SUBMISSION ACEPTADA")
    print(f"   submission_id : {body.get('submission_id')}")
    print(f"   predictions   : {body.get('predictions_received')}")
    print(f"   is_official   : {body.get('is_official')}")
    print(f"   cycle_id      : {cycle['cycle_id']}")


# ─────────────────────────────────────────────────────────────────────────────
# Modo watch: espera el ciclo y ejecuta
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    api_key = os.environ.get("PULSO_API_KEY")
    if not api_key:
        raise SystemExit("ERROR: Falta la variable PULSO_API_KEY.\n"
                         "Usa: PULSO_API_KEY=<tu_key> python scripts/watch_and_submit.py")

    dry_run = os.getenv("PULSO_DRY_RUN", "0") == "1"
    poll_secs = int(os.getenv("PULSO_POLL_SECS", "30"))
    max_wait = int(os.getenv("PULSO_MAX_WAIT_MINS", "60")) * 60

    if dry_run:
        print(">>> MODO DRY RUN: no se enviará ninguna submission <<<\n")

    print(f"Conectando a {API_URL}")
    print(f"Polling cada {poll_secs}s | Timeout máximo: {max_wait//60} min\n")

    start = time.time()
    with make_client(api_key) as probe:
        # Verificar conectividad
        try:
            probe.get("/health").raise_for_status()
            print("✓ API reachable\n")
        except Exception as e:
            raise SystemExit(f"No se puede conectar a la API: {e}")

    submitted_cycles: set[str] = set()
    while True:
        elapsed = time.time() - start
        if elapsed > max_wait:
            print(f"Tiempo máximo de espera alcanzado ({max_wait//60} min). Saliendo.")
            break

        print(f"[{datetime.now().strftime('%H:%M:%S')}] Verificando ciclo abierto...", end=" ", flush=True)

        with make_client(api_key) as client:
            cycle = get_cycle(client)

        if cycle is None:
            print(f"Sin ciclo. Reintentando en {poll_secs}s...")
            time.sleep(poll_secs)
            continue

        cycle_id = cycle["cycle_id"]
        if cycle_id in submitted_cycles:
            print(f"Ciclo {cycle_id} ya procesado. Esperando próximo ciclo...")
            time.sleep(poll_secs)
            continue

        print(f"¡Ciclo {cycle_id} ABIERTO!")
        try:
            run_pipeline(api_key, cycle, dry_run)
            submitted_cycles.add(cycle_id)
        except Exception as e:
            print(f"\n❌ ERROR en pipeline: {e}")
            print("Reintentando en 60s...")
            time.sleep(60)
            continue

        print(f"\nEsperando próximo ciclo (polling cada {poll_secs}s)...")
        time.sleep(poll_secs)


if __name__ == "__main__":
    main()
