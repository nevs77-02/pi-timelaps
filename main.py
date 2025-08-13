# main.py

import os
import sys
import time
import json
import numpy as np
from picamera2 import Picamera2
from datetime import datetime
from loguru import logger

# --- Konfiguration & Pfade ---
CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'config.json')
PID_PATH = os.path.join(os.path.dirname(__file__), 'web/timelapse.pid')
STATUS_PATH = os.path.join(os.path.dirname(__file__), 'web/status.json')
TEMP_PATH = os.path.join(os.path.dirname(__file__), 'temp.jpg')
LOG_ROOT = "/mnt/hdd/timelapse/logs" # Dies muss mit app.py übereinstimmen

AWB_MAP = {
    "auto": 0,
    "daylight": 1,
    "cloudy": 2,
    "tungsten": 3,
    "fluorescent": 4,
    "indoor": 5,
    "custom": 0
}

def map_awb_mode(value):
    """Akzeptiert String oder Zahl und gibt den passenden int-Code zurück."""
    if isinstance(value, str):
        return AWB_MAP.get(value.lower(), 0)
    try:
        return int(value)
    except (ValueError, TypeError):
        return 0

# Loguru Konfiguration
def setup_logging(log_folder):
    os.makedirs(log_folder, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    log_file = os.path.join(log_folder, f"timelapse_{today}.log")
    
    logger.remove() # Entferne die Standard-Konsole
    logger.add(sys.stderr, format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>")
    logger.add(log_file, rotation="1 day", retention="7 days")
    logger.info("📄 Logging-Konfiguration abgeschlossen. Log-Datei: {}", log_file)


# --- Mapping für String-Werte zu Integer-Werten ---
NOISE_REDUCTION_MAP = {
    "off": 0,
    "fast": 1,
    "high_quality": 2,
    "minimal": 3,
    "auto": 4
}


# --- Helferfunktionen ---
def load_config():
    """Läd die Konfiguration aus config.json"""
    logger.info("⚙️ Lese Konfiguration aus '{}'", CONFIG_PATH)
    with open(CONFIG_PATH, "r") as f:
        return json.load(f)

def get_camera_index_by_model(model_name):
    for idx, info in enumerate(Picamera2.global_camera_info()):
        if info.get('Model', '').lower() == model_name.lower():
            return idx
    raise RuntimeError(f"Keine Kamera mit Modell {model_name} gefunden.")


def safe_float(val, default):
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def safe_int(val, default):
    try:
        return int(val)
    except (ValueError, TypeError):
        return default

def reload_dynamic_config_fields(config):
    """Lädt alle dynamisch steuerbaren Konfigurationsfelder neu, um Live-Anpassungen zu ermöglichen."""
    try:
        with open(CONFIG_PATH, 'r') as f:
            new_config = json.load(f)
            config['shutter']         = new_config.get('shutter', config.get('shutter'))
            config['gain']            = new_config.get('gain', config.get('gain'))
            config['min_interval']    = new_config.get('min_interval', config.get('min_interval'))
            config['raw_delay']       = new_config.get('raw_delay', config.get('raw_delay'))
            config['awb_enable']      = new_config.get('awb_enable', config.get('awb_enable'))
            config['awb_mode']        = new_config.get('awb_mode', config.get('awb_mode'))
            config['awb_gain_r']      = new_config.get('awb_gain_r', config.get('awb_gain_r'))
            config['awb_gain_b']      = new_config.get('awb_gain_b', config.get('awb_gain_b'))
            config['focus']           = new_config.get('focus', config.get('focus'))
            config['noise_reduction'] = new_config.get('noise_reduction', config.get('noise_reduction'))
            config['saturation']      = new_config.get('saturation', config.get('saturation'))
            config['contrast']        = new_config.get('contrast', config.get('contrast'))
            config['brightness']      = new_config.get('brightness', config.get('brightness'))
            config['sharpness']       = new_config.get('sharpness', config.get('sharpness'))
            # Optional: Ablauf-/Automatikwerte, falls du sie live steuern möchtest:
            config['ev']              = new_config.get('ev', config.get('ev'))
        logger.info("🔄 Alle dynamischen Konfigurationsfelder wurden neu geladen.")
    except FileNotFoundError:
        logger.warning("Datei '{}' nicht gefunden, dynamische Felder nicht aktualisiert.", CONFIG_PATH)


def save_sidecar_json(jpeg_path, meta, controls, config, extra):
    """Speichert Metadaten und Controls in einer JSON-Datei neben dem Bild."""
    sidecar_path = os.path.splitext(jpeg_path)[0] + ".json"
    sidecar_data = {
        "metadata": meta,
        "controls": controls,
        "config": config,
        "extra": extra
    }
    with open(sidecar_path, 'w') as f:
        json.dump(sidecar_data, f, indent=2)
    logger.trace("📝 Metadaten für '{}' gespeichert.", os.path.basename(jpeg_path))

def build_controls(config):
    controls = {
        "AeEnable": config.get("ae_enable", False),
        "AwbEnable": config.get("awb_enable", True),
        "AwbMode": map_awb_mode(config.get("awb_mode")),
        "NoiseReductionMode": NOISE_REDUCTION_MAP.get(config.get("noise_reduction"), 4),
        # Fokus nur setzen, wenn vorhanden
        **({"LensPosition": safe_float(config.get("focus"), None)} if config.get("focus") is not None else {}),
        "Saturation": config.get("saturation", 0),
        "Contrast": config.get("contrast", 0),
        "Brightness": config.get("brightness", 0.0),
        "Sharpness": config.get("sharpness", 0),
    }
    if not config.get("ae_enable", False):
        controls["ExposureTime"] = safe_int(config.get("shutter"), 1000)
        controls["AnalogueGain"] = safe_float(config.get("gain"), 1.0)
    if not controls["AwbEnable"]:
        controls["ColourGains"] = (config.get("awb_gain_r"), config.get("awb_gain_b"))
    return controls


def safe_set_controls(picam, controls):
    supported = set(getattr(picam, "camera_controls", {}).keys())
    valid_controls = {k: v for k, v in controls.items()
                      if v is not None and (not supported or k in supported)}
    logger.debug("➡️ Setze Kamera-Controls (gefiltert): {}", valid_controls)
    picam.set_controls(valid_controls)


def log_controls_and_metadata(picam, controls, prefix=""):
    """Loggt die aktuell gesetzten und tatsächlichen Kamera-Werte"""
    meta = picam.capture_metadata()
    shutter_val = controls.get("ExposureTime")
    gain_val = controls.get("AnalogueGain")
    
    shutter_str = f"{shutter_val}µs" if shutter_val is not None else "N/A"
    gain_str = f"{gain_val:.2f}" if gain_val is not None else "N/A"
    
    logger.info(f"{prefix}Shutter={shutter_str}, Gain={gain_str}")


# --- Hauptfunktionen ---
def run_timelapse(config):
    logger.info("🎬 Starte Timelapse-Session...")
    try:
        cam_index = get_camera_index_by_model(config.get("camera_id", "imx708"))
        picam = Picamera2(cam_index)
        # --- HDR-Zwangsauflösung, wenn aktiviert ---
        USE_HDR_RES = (4608, 2592)
        if config.get("use_hdr", False):
            logger.info("🌈 HDR-Modus AKTIV: Auflösung wird auf %s gesetzt!", USE_HDR_RES)
            # Logge bisherige Auflösung, falls sie überschrieben wird
            if "resolution" in config and tuple(config["resolution"]) != USE_HDR_RES:
                logger.info("⚠️ Vorherige Auflösung (%s) wird durch HDR-Mode überschrieben.", config["resolution"])
            config["resolution"] = list(USE_HDR_RES)
        else:
            logger.info("HDR-Modus deaktiviert. Benutzerdefinierte Auflösung: %s", config.get("resolution"))

        still_config = picam.create_still_configuration(
            main={"size": tuple(config["resolution"]), "format": "BGR888"},
            raw={"size": (2304, 1296), "format": "SBGGR10"}
        )
        picam.configure(still_config)
        picam.start()
        time.sleep(0.2)
        logger.info("📷 Kamera konfiguriert und gestartet.")

        # === Session-Unterordner im Tagesordner erzeugen ===
        now = datetime.now()
        date_path = now.strftime("%Y/%m/%d")
        jpeg_root = os.path.join(config["timelapse_folder"], date_path)
        raw_root  = os.path.join(config["raw_folder"], date_path)
        os.makedirs(jpeg_root, exist_ok=True)
        os.makedirs(raw_root, exist_ok=True)

        def get_next_session_folder(root):
            entries = [name for name in os.listdir(root) if os.path.isdir(os.path.join(root, name))]
            nums = []
            for e in entries:
                try:
                    nums.append(int(e))
                except ValueError:
                    continue
            nextnum = 1 if not nums else max(nums) + 1
            return f"{nextnum:03d}"

        session_subfolder = get_next_session_folder(jpeg_root)
        session_jpeg_folder = os.path.join(jpeg_root, session_subfolder)
        session_raw_folder  = os.path.join(raw_root, session_subfolder)
        os.makedirs(session_jpeg_folder, exist_ok=True)
        os.makedirs(session_raw_folder, exist_ok=True)

        logger.info(f"📁 Session-Ordner (JPEG): {session_jpeg_folder}")
        logger.info(f"📁 Session-Ordner (RAW):  {session_raw_folder}")

        duration = config.get("duration", 60)
        end_time = time.time() + duration
        shot = 1
        last_jpeg = None
        exp_seconds = None

        while time.time() < end_time:
            reload_dynamic_config_fields(config)

            controls = build_controls(config)

            min_interval = safe_float(config.get("min_interval"), 10)
            raw_delay = safe_float(config.get("raw_delay"), 3)

            shutter_limit = int((min_interval - raw_delay - 0.5) * 1_000_000)
            exposure_time = controls.get("ExposureTime", None)

            if exposure_time is not None:
                MAX_SHUTTER_EFF = min(exposure_time, shutter_limit)
                if exposure_time > MAX_SHUTTER_EFF:
                    logger.warning(f"⚠️ Shutter ({exposure_time}µs) > Intervall-Maximum ({MAX_SHUTTER_EFF}µs)! Begrenze auf Intervall.")
                    exposure_time = MAX_SHUTTER_EFF
                    exp_seconds = exposure_time / 1_000_000
            else:
                exp_seconds = None  # Kennzeichnet AE-Betrieb


            safe_set_controls(picam, controls)
            log_controls_and_metadata(picam, controls, prefix=f"Timelapse Bild {shot:04d}: ")

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

            filename_jpeg = os.path.join(session_jpeg_folder, f"timelapse_{timestamp}_{shot:04d}.jpg")
            filename_raw  = os.path.join(session_raw_folder,  f"timelapse_{timestamp}_{shot:04d}.raw")
            filename_npy  = os.path.join(session_raw_folder,  f"timelapse_{timestamp}_{shot:04d}.npy")

            t0 = time.time()
            try:
                picam.capture_file(filename_jpeg)
                last_jpeg = filename_jpeg
                logger.success(f"📸 JPEG gespeichert: {filename_jpeg}")
                if config.get("save_raw", False):
                    raw_array = picam.capture_array("raw")
                    raw_array.tofile(filename_raw)
                    np.save(filename_npy, raw_array)
                    logger.success(f"💾 RAW gespeichert: {filename_raw} und {filename_npy}")

                meta = picam.capture_metadata()
                save_sidecar_json(
                    filename_jpeg,
                    meta,
                    controls,
                    config,
                    extra={
                        "frame_number": shot,
                        "hdr_mode": config.get("use_hdr", False)
                    }
                )

            except Exception as e:
                logger.error(f"❌ Fehler bei Timelapse-Bild {shot}: {e}")
            t1 = time.time()
            speicher_zeit = t1 - t0

            if exp_seconds is not None:
                actual_interval = max(min_interval, exp_seconds + raw_delay, speicher_zeit + 0.5)
            else:
                actual_interval = max(min_interval, speicher_zeit + 0.5)

            logger.info(
                f"⏱️ Intervall: {actual_interval:.2f}s (nächste Aufnahme)"
            )

            shot += 1
            status = {
                "running": True,
                "mode": "timelapse",
                "current_shot": shot,
                "last_file": last_jpeg,
                "last_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "error": str(e) if 'e' in locals() else ""
            }
            with open(STATUS_PATH, "w") as f:
                json.dump(status, f, indent=2)
            time.sleep(actual_interval)

        picam.close()
        print("DEBUG: Timelapse-Loop Ende erreicht.")
        logger.info(f"✅ Timelapse beendet: {shot-1} Bilder gespeichert.")
        logger.info(f"✅ Timelapse beendet: {shot-1} Bilder gespeichert.")

    except Exception as e:
        logger.error(f"❌ Unerwarteter Fehler im Timelapse-Loop: {e}")
    finally:
        print("DEBUG: Finally erreicht.")
        logger.info("ℹ️ Aufräumarbeiten...")
        status = {
            "running": False,
            "mode": "timelapse",
            "current_shot": shot - 1 if 'shot' in locals() else 0,
            "last_file": last_jpeg,
            "last_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "error": str(e) if 'e' in locals() else ""
        }
        with open(STATUS_PATH, "w") as f:
            json.dump(status, f, indent=2)
        if os.path.exists(PID_PATH):
            os.remove(PID_PATH)

def capture_single_image(config):
    logger.info("🖼️ Starte Testbild-Aufnahme...")
    # --- HDR-Zwangsauflösung, wenn aktiviert ---
    USE_HDR_RES = (4608, 2592)
    if config.get("use_hdr", False):
        logger.info("🌈 HDR-Modus AKTIV: Auflösung wird auf %s gesetzt!", USE_HDR_RES)
        # Logge bisherige Auflösung, falls sie überschrieben wird
        if "resolution" in config and tuple(config["resolution"]) != USE_HDR_RES:
            logger.info("⚠️ Vorherige Auflösung (%s) wird durch HDR-Mode überschrieben.", config["resolution"])
        config["resolution"] = list(USE_HDR_RES)
    else:
        logger.info("HDR-Modus deaktiviert. Benutzerdefinierte Auflösung: %s", config.get("resolution"))

    cam_index = get_camera_index_by_model(config.get("camera_id", "imx708"))
    picam = Picamera2(cam_index)
    still_config = picam.create_still_configuration(
        main={"size": tuple(config["resolution"]), "format": "BGR888"},
        raw={"size": (2304, 1296), "format": "SBGGR10"}
    )
    picam.configure(still_config)
    picam.start()
    time.sleep(0.2)
    
    controls = build_controls(config)
    safe_set_controls(picam, controls)
    
    meta = picam.capture_metadata()
    log_controls_and_metadata(picam, controls, prefix="Testbild: ")
    
    filename = os.path.join(config.get("test_folder", "."), f"testbild_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg")
    try:
        picam.capture_file(filename)
        logger.success(f"✅ Testbild gespeichert: {filename}")
        save_sidecar_json(
            filename,
            meta,
            controls,
            config,
            extra={
                "frame_number": 1,
                "hdr_mode": config.get("use_hdr", False)
            }
        )

    except Exception as e:
        logger.error(f"❌ Fehler beim Speichern des Testbildes: {e}")
    
    picam.close()
    logger.info("✅ Testbild-Aufnahme beendet.")
    
# --- Skript-Einstiegspunkt ---
if __name__ == '__main__':
    try:
        # Konfigurieren Sie das Logging vor dem Laden der Konfiguration
        setup_logging(LOG_ROOT)
        
        mode = sys.argv[1] if len(sys.argv) > 1 else "timelapse"
        logger.info("🚀 Skript gestartet im Modus: {}", mode)
        config = load_config()
        
        if mode == "timelapse":
            run_timelapse(config)
        elif mode == "single":
            capture_single_image(config)
        else:
            logger.error(f"⚠️ Unbekannter Modus: '{mode}'. Verwende 'timelapse' oder 'single'.")

    except FileNotFoundError:
        logger.error(f"❌ Konfigurationsdatei nicht gefunden: {CONFIG_PATH}")
    except IndexError:
        logger.error("❌ Bitte einen Modus angeben ('timelapse' oder 'single').")
    except Exception as e:
        logger.error(f"❌ Ein unerwarteter Fehler ist aufgetreten: {e}")
import atexit
def my_exit():
    print("DEBUG: Skript wird beendet (atexit).")
atexit.register(my_exit)