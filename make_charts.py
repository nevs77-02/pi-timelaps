#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_charts.py - Erzeugt Zeitreihen-Diagramme aus CSV-Daten
- Nutzt Pandas, Plotly und Loguru
- Erzeugt eine HTML-Datei mit interaktiven Graphen
"""
import pandas as pd
import numpy as np
from pathlib import Path
from plotly.offline import plot
import plotly.graph_objs as go
import os
import sys
from loguru import logger

# --- Pfade ---
CSV_PATH = "sensor_log.csv"
OUT_HTML = "web/static/charts.html"
LOG_ROOT = Path(os.environ.get("LOG_ROOT") or "/mnt/hdd/timelapse/logs")
LOG_PATH = LOG_ROOT / "make_charts.log"

def setup_logger():
    """Konfiguriert den Loguru-Logger für dieses Skript."""
    logger.remove()
    logger.add(sys.stderr, format="<green>{time}</green> <level>{level}</level>: <level>{message}</level>")
    LOG_ROOT.mkdir(parents=True, exist_ok=True)
    logger.add(LOG_PATH, rotation="10 MB", compression="zip", retention="10 days")

def mk_ts(name, x, y):
    return go.Scatter(x=x, y=y, mode="lines", name=name)

def main():
    setup_logger()

    try:
        logger.info(f"Lese Daten aus {CSV_PATH}")
        df = pd.read_csv(CSV_PATH, parse_dates=["timestamp"])
        df.sort_values("timestamp", inplace=True)
    except FileNotFoundError:
        logger.error(f"Fehler: Die Datei '{CSV_PATH}' wurde nicht gefunden.")
        sys.exit(1)
    except Exception as e:
        logger.exception(f"Fehler beim Lesen der CSV-Datei: {e}")
        sys.exit(1)

    try:
        # AWB-Gains aus TCS RGBC (G=1.0, R/B relativ zu G)
        eps = 1e-9
        df["awb_r"] = (df["tcs_g"] / (df["tcs_r"] + eps)).clip(0.2, 8.0)
        df["awb_b"] = (df["tcs_g"] / (df["tcs_b"] + eps)).clip(0.2, 8.0)

        # Optionale Robustheitsglättung
        df["awb_r"] = df["awb_r"].rolling(5, min_periods=1).median()
        df["awb_b"] = df["awb_b"].rolling(5, min_periods=1).median()
        figs = []

        # 1) VEML Lux
        fig1 = go.Figure()
        fig1.add_trace(mk_ts("veml_lux", df["timestamp"], df["veml_lux"]))
        fig1.add_trace(mk_ts("veml_autolux", df["timestamp"], df["veml_autolux"]))
        fig1.update_layout(title="VEML7700 – Lux (fix) vs. Autolux", xaxis_title="Zeit", yaxis_title="Lux")
        figs.append(fig1)

        # 2) VEML White & ALS (raw)
        fig2 = go.Figure()
        fig2.add_trace(mk_ts("veml_white", df["timestamp"], df["veml_white"]))
        fig2.add_trace(mk_ts("veml_light (ALS)", df["timestamp"], df["veml_light"]))
        fig2.update_layout(title="VEML7700 – White & ALS (raw)", xaxis_title="Zeit")
        figs.append(fig2)

        # 3) TCS Lux
        fig3 = go.Figure()
        fig3.add_trace(mk_ts("tcs_lux", df["timestamp"], df["tcs_lux"]))
        fig3.update_layout(title="TCS34725 – Lux", xaxis_title="Zeit", yaxis_title="Lux")
        figs.append(fig3)

        # 4) TCS RGBC
        fig4 = go.Figure()
        for col in ["tcs_r", "tcs_g", "tcs_b", "tcs_clear"]:
            fig4.add_trace(mk_ts(col, df["timestamp"], df[col]))
        fig4.update_layout(title="TCS34725 – RGBC (raw 16-bit)", xaxis_title="Zeit")
        figs.append(fig4)

        # 5) TCS Farbtemperatur
        fig5 = go.Figure()
        fig5.add_trace(mk_ts("tcs_ctK", df["timestamp"], df["tcs_ctK"]))
        fig5.update_layout(title="TCS34725 – Farbtemperatur (K)", xaxis_title="Zeit", yaxis_title="Kelvin")
        figs.append(fig5)

        # 6) AWB-Gains (r/b, G=1.0)
        fig6 = go.Figure()
        fig6.add_trace(mk_ts("awb_r", df["timestamp"], df["awb_r"]))
        fig6.add_trace(mk_ts("awb_b", df["timestamp"], df["awb_b"]))
        fig6.update_layout(title="AWB-Gains aus TCS34725 (G=1.0)", xaxis_title="Zeit", yaxis_title="Gain")
        figs.append(fig6)

        # 7) Optional: aktuelle Sensor-Einstellungen (als Linien)
        fig7 = go.Figure()
        fig7.add_trace(mk_ts("veml_gain", df["timestamp"], df["veml_gain"]))
        fig7.add_trace(mk_ts("veml_integration_ms", df["timestamp"], df["veml_integration_ms"]))
        fig7.add_trace(mk_ts("tcs_gain", df["timestamp"], df["tcs_gain"]))
        fig7.add_trace(mk_ts("tcs_integration_ms", df["timestamp"], df["tcs_integration_ms"]))
        fig7.update_layout(title="Sensor-Einstellungen über die Zeit", xaxis_title="Zeit")
        figs.append(fig7)

        # Alle Diagramme in eine HTML-Seite schreiben
        html_parts = []
        for i, fig in enumerate(figs, start=1):
            html_parts.append(plot(fig, include_plotlyjs=(i==1), output_type="div"))
        tpl = """<!doctype html><html><head><meta charset="utf-8"><title>Sensor Charts</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
        body{{font-family:system-ui,Segoe UI,Roboto,Arial,sans-serif;margin:20px}}
        .chart{{margin-bottom:40px}}
        </style>
        </head><body>
        <h1>VEML7700 & TCS34725 – Zeitreihen</h1>
        <p>Datenquelle: <code>{csv}</code></p>
        {divs}
        </body></html>"""
        html = tpl.format(csv=CSV_PATH, divs="\n".join(f'<div class="chart">{d}</div>' for d in html_parts))
        Path(OUT_HTML).write_text(html, encoding="utf-8")
        logger.info(f"Fertig: {OUT_HTML}")

    except Exception as e:
        logger.exception(f"Fehler beim Erzeugen der Diagramme: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()