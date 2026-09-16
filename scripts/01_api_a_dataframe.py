"""Descarga datos de Pulso TransMi y genera una visualización geográfica."""

from pathlib import Path

import matplotlib.pyplot as plt

from pulso_transmi import PulsoTransmiClient


API_URL = "https://pulso-transmi.72-60-245-2.sslip.io"
OUTPUT_DIR = Path("artifacts")


def main() -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)

    with PulsoTransmiClient(base_url=API_URL, timeout=60) as client:
        stations_df = client.stations()
        observations_df = client.observations_dataframe(page_size=5000)
        context_df = client.context_dataframe(page_size=5000)

    stations_df.to_csv(OUTPUT_DIR / "stations.csv", index=False)
    observations_df.to_csv(OUTPUT_DIR / "observations.csv", index=False)
    context_df.to_csv(OUTPUT_DIR / "context.csv", index=False)

    fig, ax = plt.subplots(figsize=(10, 8))
    for corridor, group in stations_df.groupby("corridor"):
        ax.scatter(
            group["longitude"],
            group["latitude"],
            s=90,
            alpha=0.85,
            label=corridor,
        )
        for _, station in group.iterrows():
            ax.annotate(
                station["station_name"],
                (station["longitude"], station["latitude"]),
                xytext=(5, 5),
                textcoords="offset points",
                fontsize=8,
            )

    ax.set_title("Estaciones de Pulso TransMi")
    ax.set_xlabel("Longitud")
    ax.set_ylabel("Latitud")
    ax.grid(True, alpha=0.3)
    ax.legend(title="Corredor", bbox_to_anchor=(1.02, 1), loc="upper left")
    fig.tight_layout()
    output_path = OUTPUT_DIR / "estaciones_mapa.png"
    fig.savefig(output_path, dpi=180, bbox_inches="tight")
    plt.close(fig)

    print(f"Estaciones: {len(stations_df):,}")
    print(f"Observaciones: {len(observations_df):,}")
    print(f"Contexto: {len(context_df):,}")
    print(f"Visualización: {output_path}")


if __name__ == "__main__":
    main()
