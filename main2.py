#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
main2.py — Minimaler Timelapse-Recorder für Raspberry Pi (Picamera2/libcamera)
- Liest alle Parameter aus config.json
- Nur Timelapse (keine anderen Modi)
- Start/Stop sauber via Signal (von tlctl.py)
- Autofokus wird nur genutzt, wenn die Kamera AF unterstützt
- Nutzt Loguru für robustes Logging
"""
from __future__ import annotations
import argparse
import json
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path
from loguru import logger

# Picamera2/libcamera
try:
    from picamera2 import Picamera2
    try:
        from libcamera import controls as libcam_ctrls
    except Exception:
        libcam_ctrls = None
except Exception as e:
    print("Fehler: Picamera2 ist nicht installiert oder libcamera fehlt:", e, file=sys.stderr)
    sys.exit(1)

# --- Log-Pfade (Loguru-Konfiguration) ---
LOG_ROOT = Path(os.environ.get("LOG_ROOT") or "/mnt/hdd/timelapse/logs")
LOG_PATH = LOG_ROOT / "main2.log"

stop_flag = False

def _handle_stop(signum, frame):
    global stop_flag
    stop_flag = True

def load_config(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return data

AWB_MODE_MAP = {
    "auto": "Auto", "tungsten": "Tungsten", "incandescent": "Tungsten",
    "fluorescent": "Fluorescent", "indoor": "Indoor", "daylight": "Daylight",
    "sun": "Daylight", "cloudy": "Cloudy", "shade": "Shade", "custom": "Manual",
}

NR_MODE_MAP = {
    "off": "Off", "minimal": "Minimal", "fast": "Fast",
    "hq": "HighQuality", "high_quality": "HighQuality",
}

def choose_camera(picam2: Picamera2, wanted_id: str | None) -> int:
    if not wanted_id: return 0
    wanted_id = wanted_id.lower()
    infos = Picamera2.global_camera_info()
    for idx, info in enumerate(infos):
        txt = " ".join([str(v) for v in info.values()]).lower()
        if wanted_id in txt: return idx
    return 0

def set_if_supported(picam2: Picamera2, **controls):
    if not controls: return
    try:
        picam2.set_controls(controls)
    except Exception as e:
        logger.warning("Konnte Controls %s nicht setzen: %s", controls, e)

def map_awb_mode(mode_str: str | None):
    if not mode_str: return None
    s = str(mode_str).strip().lower()
    name = AWB_MODE_MAP.get(s)
    if not name: return None
    if libcam_ctrls is not None and hasattr(libcam_ctrls, "AwbMode") and hasattr(libcam_ctrls.AwbMode, name):
        return getattr(libcam_ctrls.AwbMode, name)
    return None

def map_nr_mode(mode_str: str | None):
    if not mode_str: return None
    s = str(mode_str).strip().lower()
    name = NR_MODE_MAP.get(s)
    if not name: return None
    if libcam_ctrls is not None and hasattr(libcam_ctrls, "NoiseReductionMode") and hasattr(libcam_ctrls.NoiseReductionMode, name):
        return getattr(libcam_ctrls.NoiseReductionMode, name)
    return None

def supports_autofocus(picam2: Picamera2) -> bool:
    try:
        return "AfMode" in picam2.camera_controls
    except Exception:
        return False

def apply_focus(picam2, cfg):
    af_enable = bool(cfg.get("af_enable", True))
    focus_val = cfg.get("focus", None)
    if af_enable and supports_autofocus(picam2):
        try:
            can_afmode = (libcam_ctrls is not None and hasattr(libcam_ctrls, "AfMode") and hasattr(libcam_ctrls.AfMode, "Continuous"))
            if can_afmode:
                set_if_supported(picam2, AfMode=libcam_ctrls.AfMode.Continuous)
                if hasattr(libcam_ctrls, "AfTrigger") and hasattr(libcam_ctrls.AfTrigger, "Start"):
                    set_if_supported(picam2, AfTrigger=libcam_ctrls.AfTrigger.Start)
                logger.info("Autofokus (Continuous) aktiviert.")
                return
        except Exception as e:
            logger.warning("AF fehlgeschlagen (%s) – versuche manuellen Fokus (Fallback).", e)
    if focus_val is not None:
        try:
            if (libcam_ctrls is not None and hasattr(libcam_ctrls, "AfMode") and hasattr(libcam_ctrls.AfMode, "Manual")):
                set_if_supported(picam2, AfMode=libcam_ctrls.AfMode.Manual)
            set_if_supported(picam2, LensPosition=float(focus_val))
            logger.info("Manueller Fokus aktiviert (LensPosition=%s).", focus_val)
            return
        except Exception as e:
            logger.warning("Manueller Fokus konnte nicht gesetzt werden: %s", e)
    if not supports_autofocus(picam2):
        logger.info("Kamera hat keinen AF; nutze festen optischen Fokus.")
    elif not af_enable:
        logger.info("AF per 'af_enable': false deaktiviert und kein 'focus' gesetzt – nutze festen Fokus.")

def configure_camera(picam2: Picamera2, cfg: dict):
    width, height = cfg.get("resolution", [1920, 1080])
    save_raw = bool(cfg.get("save_raw", False))
    still_cfg = picam2.create_still_configuration(
        main={"size": (int(width), int(height))},
        raw={"size": (int(width), int(height))} if save_raw else None,
    )
    picam2.configure(still_cfg)
    ae_enable = bool(cfg.get("ae_enable", True))
    awb_enable = bool(cfg.get("awb_enable", True))
    set_if_supported(picam2, AeEnable=ae_enable)
    set_if_supported(picam2, AwbEnable=awb_enable)
    if awb_enable:
        awb_mode = map_awb_mode(cfg.get("awb_mode"))
        if awb_mode is not None:
            set_if_supported(picam2, AwbMode=awb_mode)
    else:
        r = float(cfg.get("awb_gain_r", 1.0))
        b = float(cfg.get("awb_gain_b", 1.0))
        set_if_supported(picam2, ColourGains=(r, b))
    if not ae_enable:
        if "shutter" in cfg:
            set_if_supported(picam2, ExposureTime=int(cfg.get("shutter")))
        if "gain" in cfg:
            set_if_supported(picam2, AnalogueGain=float(cfg.get("gain")))
    if "ev" in cfg:
        ev = float(cfg.get("ev", 0.0))
        try:
            set_if_supported(picam2, ExposureCompensation=int(round(ev * 16)))
        except Exception:
            logger.debug("ExposureCompensation wird von dieser Version evtl. nicht unterstützt.")
    for key_json, key_ctrl in [
        ("brightness", "Brightness"), ("contrast", "Contrast"),
        ("saturation", "Saturation"), ("sharpness", "Sharpness"),
    ]:
        if key_json in cfg:
            set_if_supported(picam2, **{key_ctrl: float(cfg[key_json])})
    nr = map_nr_mode(cfg.get("noise_reduction"))
    if nr is not None:
        set_if_supported(picam2, NoiseReductionMode=nr)
    if cfg.get("use_hdr"):
        try:
            if libcam_ctrls and hasattr(libcam_ctrls, "HdrMode"):
                set_if_supported(picam2, HdrMode=libcam_ctrls.HdrMode.Auto)
        except Exception:
            logger.debug("HDR nicht unterstützt – übersprungen.")
    apply_focus(picam2, cfg)

def write_pidfile(pidfile: Path):
    try:
        pidfile.parent.mkdir(parents=True, exist_ok=True)
        pidfile.write_text(str(os.getpid()), encoding="utf-8")
    except Exception as e:
        logger.warning("Konnte PID-Datei nicht schreiben (%s): %s", pidfile, e)

def remove_pidfile(pidfile: Path):
    try:
        if pidfile.exists():
            pidfile.unlink()
    except Exception as e:
        logger.warning("Konnte PID-Datei nicht löschen (%s): %s", pidfile, e)

def ensure_folder(path: Path):
    path.mkdir(parents=True, exist_ok=True)

def main():
    parser = argparse.ArgumentParser(description="Timelapse Recorder (config.json-basiert)")
    parser.add_argument("--config", default="config_tl.json", help="Pfad zur Timelapse-Config")
    parser.add_argument("--pidfile", default=None, help="Pfad zur PID-Datei (optional)")
    parser.add_argument("--foreground", action="store_true", help="Nicht daemonisieren, Logs auf Stdout")
    args = parser.parse_args()

    # --- Loguru-Konfiguration ---
    logger.remove()
    logger.add(LOG_PATH, rotation="10 MB", compression="zip", retention="10 days")
    if args.foreground:
        logger.add(sys.stderr)
    
    cfg_path = Path(args.config)
    cfg = load_config(cfg_path)
    
    tl_folder = Path(cfg.get("timelapse_folder", "./timelapse"))
    log_folder = Path(cfg.get("log_folder", "./logs"))
    ensure_folder(tl_folder)
    ensure_folder(log_folder)
    pidfile = Path(args.pidfile) if args.pidfile else (log_folder / "timelapse.pid")
    
    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)
    
    wanted = cfg.get("camera_id")
    cam_index = choose_camera(Picamera2, wanted)
    picam2 = Picamera2(camera_num=cam_index)

    try:
        configure_camera(picam2, cfg)
        picam2.start()
        time.sleep(0.5)
        write_pidfile(pidfile)
        logger.info("Timelapse gestartet (Kamera %s, Auflösung %sx%s)", wanted or cam_index, *cfg.get("resolution", [1920, 1080]))
        min_interval = float(cfg.get("min_interval", 10.0))
        raw_delay = float(cfg.get("raw_delay", 0.0))
        duration = float(cfg.get("duration", 0))
        jpeg_quality = int(cfg.get("jpeg_quality", 90))
        try:
            picam2.options["quality"] = int(jpeg_quality)
        except Exception:
            logger.debug("JPEG-Qualität konnte nicht gesetzt werden (options['quality']).")
        save_raw = bool(cfg.get("save_raw", False))
        raw_format = str(cfg.get("raw_format", "dng")).strip().lower()
        raw_folder = Path(cfg.get("raw_folder", str(Path(cfg.get("timelapse_folder", "./timelapse")).parent / "raw")))
        if save_raw:
            ensure_folder(raw_folder)
        start_mono = time.monotonic()
        end_mono = start_mono + duration if duration > 0 else None
        shot = 0
        next_due = start_mono
        while not stop_flag and (end_mono is None or time.monotonic() < end_mono):
            now = time.monotonic()
            if now < next_due:
                time.sleep(min(0.2, next_due - now)); continue
            shot += 1
            ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
            date_str = datetime.now().strftime("%Y-%m-%d")
            jpg_dir = tl_folder / "lux" / date_str
            ensure_folder(jpg_dir)
            jpg_path = jpg_dir / f"{ts}.jpg"
            try:
                live_cfg = load_config(cfg_path)
                if not bool(live_cfg.get("ae_enable", True)):
                    if "shutter" in live_cfg:
                        set_if_supported(picam2, ExposureTime=int(live_cfg["shutter"]))
                    if "gain" in live_cfg:
                        set_if_supported(picam2, AnalogueGain=float(live_cfg["gain"]))
                if not bool(live_cfg.get("awb_enable", True)):
                    r = float(live_cfg.get("awb_gain_r", 1.0))
                    b = float(live_cfg.get("awb_gain_b", 1.0))
                    set_if_supported(picam2, ColourGains=(r, b))
            except Exception as e:
                logger.warning("Konnte Live-Belichtung nicht neu laden: %s", e)
            try:
                if save_raw and raw_format in ("dng", "dng8", "dng12"):
                    raw_date_dir = raw_folder / "lux" / date_str
                    ensure_folder(raw_date_dir)
                    dng_path = raw_date_dir / f"{ts}.dng"
                    try:
                        picam2.capture_files({"main": str(jpg_path), "raw": str(dng_path)})
                        logger.info("Bild %d gespeichert: %s (+ DNG: %s)", shot, jpg_path, dng_path)
                    except Exception as e:
                        logger.warning("capture_files fehlgeschlagen (%s) – versuche Fallback.", e)
                        picam2.capture_file(str(jpg_path))
                        if raw_delay > 0: time.sleep(raw_delay)
                        try:
                            picam2.capture_file(str(dng_path))
                            logger.info("DNG gespeichert (Fallback): %s", dng_path)
                        except Exception as e2:
                            logger.error("DNG-Fallback fehlgeschlagen: %s", e2)
                else:
                    picam2.capture_file(str(jpg_path))
                    logger.info("Bild %d gespeichert: %s", shot, jpg_path)
                    if raw_delay > 0: time.sleep(raw_delay)
            except Exception as e:
                logger.error("Fehler beim Aufnehmen: %s", e)
            next_due += max(min_interval, 0.0)
        logger.info("Timelapse wird beendet…")
    finally:
        try: picam2.stop()
        except Exception: pass
        remove_pidfile(pidfile)
        logger.info("Timelapse gestoppt.")

if __name__ == "__main__":
    main()