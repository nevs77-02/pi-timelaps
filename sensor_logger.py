#!/usr/bin/env python3
import os, csv, time, math, signal, sys
from datetime import datetime, timezone
import board
import adafruit_veml7700
import adafruit_tcs34725

CSV_PATH = "sensor_log.csv"
INTERVAL_S = 5  # Messintervall

running = True
def handle_sig(sig, frame):
    global running
    running = False
signal.signal(signal.SIGINT, handle_sig)
signal.signal(signal.SIGTERM, handle_sig)

def iso_local_now():
    # Lokale Zeit als ISO 8601 ohne Mikrosekunden
    return datetime.now().astimezone().replace(microsecond=0).isoformat()

def ensure_header(path):
    header = [
        "timestamp",
        # VEML7700
        "veml_lux","veml_autolux","veml_white","veml_light",
        "veml_gain","veml_integration_ms",
        # TCS34725
        "tcs_lux","tcs_ctK","tcs_r","tcs_g","tcs_b","tcs_clear",
        "tcs_gain","tcs_integration_ms"
    ]
    exists = os.path.isfile(path)
    f = open(path, "a", newline="")
    writer = csv.writer(f)
    if not exists or os.stat(path).st_size == 0:
        writer.writerow(header)
    return f, writer

def main():
    i2c = board.I2C()  # SCL/SDA auf dem Pi
    veml = adafruit_veml7700.VEML7700(i2c)     # Default-Adresse 0x10
    tcs  = adafruit_tcs34725.TCS34725(i2c)     # Default-Adresse 0x29

    # Optionale, robuste Defaults
    veml.light_gain = veml.ALS_GAIN_1          # 2, 1, 1/4, 1/8 verfügbar
    veml.light_integration_time = veml.ALS_100MS
    # TCS: 1, 4, 16, 60; Integration in ms (2.4 … 614.4)
    tcs.gain = 4
    tcs.integration_time = 154  # typischer Kompromiss (ms)

    f, writer = ensure_header(CSV_PATH)

    try:
        while running:
            ts = iso_local_now()

            # --- VEML7700 ---
            try:
                veml_lux       = float(veml.lux)        # Lux (fixe Einstellungen)
                veml_autolux   = float(veml.autolux)    # Lux mit Auto-Gain/-Integration
                veml_white     = int(veml.white)        # "White" Kanal (roh)
                veml_light     = int(veml.light)        # ALS (roh)
                # aktuelle Einstellungen
                # Map Gain/Integration in gut lesbare Zahlen:
                gain_map = {
                    veml.ALS_GAIN_2: 2.0, veml.ALS_GAIN_1: 1.0,
                    veml.ALS_GAIN_1_4: 0.25, veml.ALS_GAIN_1_8: 0.125
                }
                veml_gain = gain_map.get(veml.light_gain, math.nan)
                it_map = {
                    veml.ALS_25MS: 25, veml.ALS_50MS: 50, veml.ALS_100MS: 100,
                    veml.ALS_200MS: 200, veml.ALS_400MS: 400, veml.ALS_800MS: 800
                }
                veml_it_ms = it_map.get(veml.light_integration_time, math.nan)
            except Exception as e:
                veml_lux = veml_autolux = veml_white = veml_light = math.nan
                veml_gain = veml_it_ms = math.nan

            # --- TCS34725 ---
            try:
                r, g, b, c = tcs.color_raw       # 16-bit RGBC
                tcs_lux    = float(tcs.lux) if tcs.lux is not None else math.nan
                tcs_ct     = float(tcs.color_temperature) if tcs.color_temperature is not None else math.nan
                tcs_gain   = int(tcs.gain)
                tcs_it_ms  = int(tcs.integration_time)
            except Exception as e:
                r = g = b = c = 0
                tcs_lux = tcs_ct = math.nan
                tcs_gain = tcs_it_ms = math.nan

            writer.writerow([
                ts,
                veml_lux, veml_autolux, veml_white, veml_light,
                veml_gain, veml_it_ms,
                tcs_lux, tcs_ct, r, g, b, c,
                tcs_gain, tcs_it_ms
            ])
            f.flush()
            time.sleep(INTERVAL_S)
    finally:
        f.close()

if __name__ == "__main__":
    main()
