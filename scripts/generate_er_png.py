from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch


tables = {
    "corridors": ["PK corridor_id", "name"],
    "stations": ["PK station_id", "station_name", "FK corridor_id", "latitude", "longitude", "active"],
    "time_dimension": ["PK observed_at", "date_value", "year/month/day", "hour/minute", "day_of_week", "is_weekend"],
    "weather_context": ["PK observed_at", "rain_mm", "rain_forecast", "temperature_c", "temperature_forecast"],
    "event_context": ["PK observed_at", "event_intensity"],
    "demand_observations": ["PK observed_at", "PK station_id", "FK station_id", "demand"],
    "ingestion_runs": ["PK ingestion_id", "resource_name", "started_at", "finished_at", "rows_loaded", "status"],
}
positions = {"corridors": (0.05, 0.70), "stations": (0.05, 0.30), "time_dimension": (0.39, 0.70), "weather_context": (0.72, 0.72), "event_context": (0.72, 0.38), "demand_observations": (0.39, 0.25), "ingestion_runs": (0.72, 0.08)}

fig, ax = plt.subplots(figsize=(16, 10))
ax.axis("off")
ax.set_title("Modelo ER — Pulso TransMi / Supabase", fontsize=22, weight="bold", pad=20)
for name, fields in tables.items():
    x, y = positions[name]
    h = 0.10 + 0.035 * len(fields)
    patch = FancyBboxPatch((x, y), 0.25, h, boxstyle="round,pad=0.012", facecolor="#eff6ff", edgecolor="#2563eb", linewidth=2)
    ax.add_patch(patch)
    ax.text(x + 0.125, y + h - 0.035, name, ha="center", va="center", fontsize=13, weight="bold", color="#1d4ed8")
    for i, field in enumerate(fields):
        ax.text(x + 0.015, y + h - 0.08 - i * 0.035, field, fontsize=9, family="monospace", va="center")
for a, b in [("corridors", "stations"), ("stations", "demand_observations"), ("time_dimension", "demand_observations"), ("time_dimension", "weather_context"), ("time_dimension", "event_context")]:
    xa, ya = positions[a]; xb, yb = positions[b]
    ax.annotate("", xy=(xb + 0.125, yb + 0.10), xytext=(xa + 0.125, ya + 0.10), arrowprops={"arrowstyle": "->", "color": "#64748b", "lw": 1.5})
ax.set_xlim(0, 1); ax.set_ylim(0, 1)
Path("docs").mkdir(exist_ok=True)
fig.savefig("docs/modelo-er-supabase.png", dpi=180, bbox_inches="tight")
print("docs/modelo-er-supabase.png")
