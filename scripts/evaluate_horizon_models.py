"""Evalua ExtraTrees especifico por horizonte con validacion temporal."""
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error

ROOT=Path(__file__).resolve().parents[1]
FEATURES=["lag_1","lag_4","lag_96","lag_672","rolling_96","slot","dow","is_weekend","rain_mm","rain_forecast","temperature_c","temperature_forecast","event_intensity"]

def prepare():
    obs=pd.read_csv(ROOT/"artifacts/observations.csv",dtype={"station_id":"string"},parse_dates=["observed_at"])
    ctx=pd.read_csv(ROOT/"artifacts/context.csv",parse_dates=["observed_at"])
    df=obs.merge(ctx,on="observed_at",how="left").sort_values(["station_id","observed_at"]).reset_index(drop=True)
    df["slot"]=df.observed_at.dt.hour*4+df.observed_at.dt.minute//15; df["dow"]=df.observed_at.dt.dayofweek; df["is_weekend"]=(df.dow>=5).astype(int)
    g=df.groupby("station_id",sort=False).demand
    for lag in (1,4,96,672): df[f"lag_{lag}"]=g.shift(lag)
    df["rolling_96"]=g.transform(lambda x:x.shift(1).rolling(96,min_periods=24).mean())
    codes={s:i for i,s in enumerate(sorted(df.station_id.unique()))}; df["station_code"]=df.station_id.map(codes)
    return df,codes

def main():
    df,codes=prepare(); x=["station_code"]+FEATURES; cutoff=df.observed_at.max()-pd.Timedelta(days=7); train_end=df.observed_at<=cutoff; rows=[]
    for steps in (1,2,3,4):
        target=df.groupby("station_id",sort=False).demand.shift(-steps)
        train=df[train_end].copy(); train["target"]=target[train.index]
        valid=df[~train_end].copy(); valid["target"]=target[valid.index]
        train=train.dropna(subset=x+["target"]); valid=valid.dropna(subset=x+["target"])
        model=ExtraTreesRegressor(n_estimators=300,min_samples_leaf=2,max_features=.9,n_jobs=-1,random_state=42)
        model.fit(train[x],train.target); pred=np.maximum(model.predict(valid[x]),0); y=valid.target.to_numpy(); wape=np.abs(y-pred).sum()/y.sum()
        rows.append({"horizon_min":steps*15,"MAE":mean_absolute_error(y,pred),"RMSE":mean_squared_error(y,pred)**.5,"WAPE":wape,"Accuracy":max(0,1-wape),"rows":len(valid)})
    out=pd.DataFrame(rows); out.to_csv(ROOT/"artifacts/horizon_metrics.csv",index=False); print(out.to_string(index=False,formatters={"MAE":"{:.2f}".format,"RMSE":"{:.2f}".format,"WAPE":"{:.4f}".format,"Accuracy":"{:.2%}".format}))
if __name__=="__main__": main()
