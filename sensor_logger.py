#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sensor_logger.py – Loggt Sensorwerte mit Loguru
- Liest VEML7700 und TCS34725
- Loggt Daten in eine CSV-Datei
- Passt die Empfindlichkeit des TCS34725 dynamisch an
- Nutzt Loguru für robustes Logging
"""
import os, csv, time, math, signal, sys, json
from datetime import datetime
from loguru import logger
from pathlib import Path

# --- Pfade festlegen: Standard = Verzeichnis dieser Datei
SCRIPT_DIR = Path(__file__).resolve().parent
CSV_PATH = Path(os.environ.get("SENSOR_LOG_PATH") or SCRIPT_DIR / "sensor_log.csv")
CONFIG_PATH = Path(os.environ.get("SENSOR_CONFIG_PATH") or SCRIPT_DIR / "sensor_config.json")
LOG_ROOT = Path(os.environ.get("LOG_ROOT") or "/mnt/hdd/timelapse/logs")
LOG_PATH = LOG_ROOT / "sensor_logger.log"

running = True
def handle_sig(sig, frame):
    global running
    running = False
signal.signal(signal.SIGINT, handle_sig)
signal.signal(signal.SIGTERM, handle_sig)

def iso_local_now():
    return datetime.now().astimezone().replace(microsecond=0).isoformat()

def ensure_header(path, header):
    path.parent.mkdir(parents=True, exist_ok=True)
    existed = path.is_file()
    f = open(path, "a", newline="")
    writer = csv.writer(f)
    if not existed or path.stat().st_size == 0:
        writer.writerow(header)
        f.flush()
    return f, writer

def load_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.warning(f"Konfigurationsdatei fehlt: {path}")
        return {}
    except Exception as e:
        logger.error(f"Konfiguration {path} konnte nicht geladen werden: {e}")
        return {}

def main():
    # Loguru-Konfiguration
    logger.remove() # Entfernt Standard-Handler
    logger.add(sys.stderr, format="<green>{time}</green> <level>{level}</level>: <level>{message}</level>")
    logger.add(LOG_PATH, rotation="10 MB", compression="zip", retention="10 days")

    logger.info(f"Starte… schreibe nach: {CSV_PATH}")

    config = load_json(CONFIG_PATH)
    INTERVAL_S = float(config.get("interval_s", 2.0))
    header = [
        "timestamp",
        "veml_lux","veml_autolux","veml_white","veml_light",
        "veml_gain","veml_integration_ms",
        "tcs_lux","tcs_ctK","tcs_r","tcs_g","tcs_b","tcs_clear",
        "tcs_gain","tcs_integration_ms"
    ]
    f, writer = ensure_header(CSV_PATH, header)

    veml = None
    tcs = None
    try:
        import board
        import adafruit_veml7700
        import adafruit_tcs34725
        i2c = board.I2C()
        veml = adafruit_veml7700.VEML7700(i2c)
        tcs = adafruit_tcs34725.TCS34725(i2c)
        veml.light_gain = veml.ALS_GAIN_1
        veml.light_integration_time = veml.ALS_100MS
        tcs.gain = config.get("tcs_default_gain", 4)
        tcs.integration_time = config.get("tcs_default_integration_time_ms", 154)
        logger.info("Sensor-Init OK (VEML7700 & TCS34725).")
    except Exception as e:
        logger.warning(f"Sensor-Init fehlgeschlagen: {e}")

    last_tcs_gain = tcs.gain if tcs else None
    last_tcs_it_ms = tcs.integration_time if tcs else None

    try:
        while running:
            veml_lux = veml_autolux = veml_white = veml_light = math.nan
            veml_gain = veml_it_ms = math.nan
            if veml is not None:
                try:
                    veml_lux = float(veml.lux)
                    veml_autolux = float(veml.autolux)
                    veml_white = int(veml.white)
                    veml_light = int(veml.light)
                    gain_map = {veml.ALS_GAIN_2: 2.0, veml.ALS_GAIN_1: 1.0, veml.ALS_GAIN_1_4: 0.25, veml.ALS_GAIN_1_8: 0.125}
                    it_map = {veml.ALS_25MS: 25, veml.ALS_50MS: 50, veml.ALS_100MS: 100, veml.ALS_200MS: 200, veml.ALS_400MS: 400, veml.ALS_800MS: 800}
                    veml_gain = gain_map.get(veml.light_gain, math.nan)
                    veml_it_ms = it_map.get(veml.light_integration_time, math.nan)
                except Exception:
                    logger.exception("Fehler beim Lesen des VEML7700-Sensors.")
            
            if tcs is not None:
                new_gain = config.get("tcs_default_gain", 4)
                new_it_ms = config.get("tcs_default_integration_time_ms", 154)
                if veml_autolux < config.get("tcs_low_light_threshold_lux", 0.1):
                    new_gain = config.get("tcs_low_light_gain", 64)
                    new_it_ms = config.get("tcs_low_light_integration_time_ms", 614)
                
                if new_gain != last_tcs_gain:
                    tcs.gain = new_gain
                    logger.info(f"TCS Gain auf {new_gain} gesetzt.")
                    last_tcs_gain = new_gain
                if new_it_ms != last_tcs_it_ms:
                    tcs.integration_time = new_it_ms
                    logger.info(f"TCS Integration Time auf {new_it_ms}ms gesetzt.")
                    last_tcs_it_ms = new_it_ms
            
            r = g = b = c = 0
            tcs_lux = tcs_ct = math.nan
            tcs_gain = tcs_it_ms = math.nan
            if tcs is not None:
                try:
                    r, g, b, c = tcs.color_raw
                    tcs_lux = float(tcs.lux) if tcs.lux is not None else math.nan
                    tcs_ct = float(tcs.color_temperature) if tcs.color_temperature is not None else math.nan
                    tcs_gain = int(tcs.gain)
                    tcs_it_ms = int(tcs.integration_time)
                except Exception:
                    logger.exception("Fehler beim Lesen des TCS34725-Sensors.")

            writer.writerow([
                iso_local_now(),
                veml_lux, veml_autolux, veml_white, veml_light,
                veml_gain, veml_it_ms,
                tcs_lux, tcs_ct, r, g, b, c,
                tcs_gain, tcs_it_ms
            ])
            f.flush()
            time.sleep(INTERVAL_S)
    finally:
        try:
            f.close()
        except Exception:
            pass

if __name__ == "__main__":
    main()