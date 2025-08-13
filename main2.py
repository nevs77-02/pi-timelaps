#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
main2.py — Minimaler Timelapse-Recorder für Raspberry Pi (Picamera2/libcamera)
- Liest alle Parameter aus config.json
- Nur Timelapse (keine anderen Modi)
- Start/Stop sauber via Signal (von tlctl.py)
- Autofokus wird nur genutzt, wenn die Kamera AF unterstützt

Voraussetzungen: sudo apt install -y python3-picamera2 python3-libcamera python3-numpy
"""
from __future__ import annotations
import argparse
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

# Picamera2/libcamera
try:
    from picamera2 import Picamera2
    try:
        # libcamera controls sind optional getrennt installiert
        from libcamera import controls as libcam_ctrls  # type: ignore
    except Exception:  # libcamera nicht als separates Modul verfügbar
        libcam_ctrls = None  # wir fallen auf Stringwerte zurück, wo möglich
except Exception as e:
    print("Fehler: Picamera2 ist nicht installiert oder libcamera fehlt:", e, file=sys.stderr)
    sys.exit(1)


stop_flag = False

def _handle_stop(signum, frame):
    global stop_flag
    stop_flag = True


def load_config(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return data


AWB_MODE_MAP = {
    # bekannte UI-Strings -> libcamera enums
    "auto": "Auto",
    "tungsten": "Tungsten",
    "incandescent": "Tungsten",
    "fluorescent": "Fluorescent",
    "indoor": "Indoor",
    "daylight": "Daylight",
    "sun": "Daylight",
    "cloudy": "Cloudy",
    "shade": "Shade",
    "custom": "Manual",
}

NR_MODE_MAP = {
    "off": "Off",
    "minimal": "Minimal",
    "fast": "Fast",
    "hq": "HighQuality",
    "high_quality": "HighQuality",
}


def choose_camera(picam2: Picamera2, wanted_id: str | None) -> int:
    """Wählt die Kamera anhand von Teilstrings aus (z. B. "imx708").
    Gibt den Kameraindex zurück, der benutzt werden soll.
    """
    if not wanted_id:
        return 0
    wanted_id = wanted_id.lower()
    infos = Picamera2.global_camera_info()
    for idx, info in enumerate(infos):
        # info ist dictartig: {"Model": "imx708", ...}
        txt = " ".join([str(v) for v in info.values()]).lower()
        if wanted_id in txt:
            return idx
    return 0


def set_if_supported(picam2: Picamera2, **controls):
    """Setzt Controls robust mit try/except (unbekannte werden ignoriert)."""
    if not controls:
        return
    try:
        picam2.set_controls(controls)
    except Exception as e:
        logging.getLogger("timelapse").warning("Konnte Controls %s nicht setzen: %s", controls, e)


def map_awb_mode(mode_str: str | None):
    if not mode_str:
        return None
    s = str(mode_str).strip().lower()
    name = AWB_MODE_MAP.get(s)
    if not name:
        return None
    # Nur setzen, wenn das Enum vorhanden ist – KEIN String-Fallback
    if libcam_ctrls is not None and hasattr(libcam_ctrls, "AwbMode") and hasattr(libcam_ctrls.AwbMode, name):
        return getattr(libcam_ctrls.AwbMode, name)
    return None



def map_nr_mode(mode_str: str | None):
    if not mode_str:
        return None
    s = str(mode_str).strip().lower()
    name = NR_MODE_MAP.get(s)
    if not name:
        return None
    # Nur setzen, wenn das Enum vorhanden ist – KEIN String-Fallback
    if libcam_ctrls is not None and hasattr(libcam_ctrls, "NoiseReductionMode") and hasattr(libcam_ctrls.NoiseReductionMode, name):
        return getattr(libcam_ctrls.NoiseReductionMode, name)
    return None


def supports_autofocus(picam2: Picamera2) -> bool:
    try:
        return "AfMode" in picam2.camera_controls
    except Exception:
        return False

def apply_focus(picam2, cfg, logger):
    """
    Fokus-Strategie:
      1) AF (Continuous), wenn erlaubt & die Enums existieren.
      2) Sonst manueller Fokus via LensPosition (wenn konfiguriert).
      WICHTIG: Keine String-Enums senden.
    """
    af_enable = bool(cfg.get("af_enable", True))
    focus_val = cfg.get("focus", None)

    # 1) AF zuerst – nur wenn Enum-Werte wirklich existieren
    if af_enable and supports_autofocus(picam2):
        try:
            can_afmode = (libcam_ctrls is not None and
                          hasattr(libcam_ctrls, "AfMode") and
                          hasattr(libcam_ctrls.AfMode, "Continuous"))
            if can_afmode:
                set_if_supported(picam2, AfMode=libcam_ctrls.AfMode.Continuous)
                if (hasattr(libcam_ctrls, "AfTrigger") and
                    hasattr(libcam_ctrls.AfTrigger, "Start")):
                    set_if_supported(picam2, AfTrigger=libcam_ctrls.AfTrigger.Start)
                logger.info("Autofokus (Continuous) aktiviert.")
                return
        except Exception as e:
            logger.warning("AF fehlgeschlagen (%s) – versuche manuellen Fokus (Fallback).", e)

    # 2) Manueller Fokus – AfMode nur setzen, wenn 'Manual' existiert
    if focus_val is not None:
        try:
            if (libcam_ctrls is not None and hasattr(libcam_ctrls, "AfMode") and
                hasattr(libcam_ctrls.AfMode, "Manual")):
                set_if_supported(picam2, AfMode=libcam_ctrls.AfMode.Manual)
            # LensPosition geht (wenn unterstützt) auch ohne AfMode-Set
            set_if_supported(picam2, LensPosition=float(focus_val))
            logger.info("Manueller Fokus aktiviert (LensPosition=%s).", focus_val)
            return
        except Exception as e:
            logger.warning("Manueller Fokus konnte nicht gesetzt werden: %s", e)

    # Weder AF noch manueller Wert erfolgreich
    if not supports_autofocus(picam2):
        logger.info("Kamera hat keinen AF; nutze festen optischen Fokus.")
    elif not af_enable:
        logger.info("AF per 'af_enable': false deaktiviert und kein 'focus' gesetzt – nutze festen Fokus.")


def configure_camera(picam2: Picamera2, cfg: dict, logger: logging.Logger):
    # Auflösung
    width, height = cfg.get("resolution", [1920, 1080])
    save_raw = bool(cfg.get("save_raw", False))

    still_cfg = picam2.create_still_configuration(
        main={"size": (int(width), int(height))},
        raw={"size": (int(width), int(height))} if save_raw else None,
    )
    picam2.configure(still_cfg)

    # AE/AWB
    ae_enable = bool(cfg.get("ae_enable", True))
    awb_enable = bool(cfg.get("awb_enable", True))

    set_if_supported(picam2, AeEnable=ae_enable)
    set_if_supported(picam2, AwbEnable=awb_enable)

    # AWB Mode / Gains
    if awb_enable:
        awb_mode = map_awb_mode(cfg.get("awb_mode"))
        if awb_mode is not None:
            set_if_supported(picam2, AwbMode=awb_mode)
    else:
        r = float(cfg.get("awb_gain_r", 1.0))
        b = float(cfg.get("awb_gain_b", 1.0))
        set_if_supported(picam2, ColourGains=(r, b))

    # Belichtung / ISO
    if not ae_enable:
        # Nur setzen, wenn AE aus ist
        if "shutter" in cfg:
            set_if_supported(picam2, ExposureTime=int(cfg.get("shutter")))
        if "gain" in cfg:
            set_if_supported(picam2, AnalogueGain=float(cfg.get("gain")))
    # EV/ExposureCompensation (falls vorhanden)
    if "ev" in cfg:
        ev = float(cfg.get("ev", 0.0))
        # libcamera nutzt 1/16 EV Schritte
        try:
            set_if_supported(picam2, ExposureCompensation=int(round(ev * 16)))
        except Exception:
            logger.debug("ExposureCompensation wird von dieser Version evtl. nicht unterstützt.")

    # Bildparameter
    for key_json, key_ctrl in [
        ("brightness", "Brightness"),
        ("contrast", "Contrast"),
        ("saturation", "Saturation"),
        ("sharpness", "Sharpness"),
    ]:
        if key_json in cfg:
            set_if_supported(picam2, **{key_ctrl: float(cfg[key_json])})

    # Noise Reduction
    nr = map_nr_mode(cfg.get("noise_reduction"))
    if nr is not None:
        set_if_supported(picam2, NoiseReductionMode=nr)

    # HDR (nicht jede Cam unterstützt das; best effort)
    if cfg.get("use_hdr"):
        try:
            if libcam_ctrls and hasattr(libcam_ctrls, "HdrMode"):
                set_if_supported(picam2, HdrMode=libcam_ctrls.HdrMode.Auto)
        except Exception:
            logger.debug("HDR nicht unterstützt – übersprungen.")

    # Fokus: AF zuerst, sonst manueller Fokus
    apply_focus(picam2, cfg, logger)



def write_pidfile(pidfile: Path, logger: logging.Logger):
    try:
        pidfile.parent.mkdir(parents=True, exist_ok=True)
        pidfile.write_text(str(os.getpid()), encoding="utf-8")
    except Exception as e:
        logger.warning("Konnte PID-Datei nicht schreiben (%s): %s", pidfile, e)


def remove_pidfile(pidfile: Path, logger: logging.Logger):
    try:
        if pidfile.exists():
            pidfile.unlink()
    except Exception as e:
        logger.warning("Konnte PID-Datei nicht löschen (%s): %s", pidfile, e)


def ensure_folder(path: Path):
    path.mkdir(parents=True, exist_ok=True)


def main():
    parser = argparse.ArgumentParser(description="Timelapse Recorder (config.json-basiert)")
    parser.add_argument("--config", default="config.json", help="Pfad zur config.json")
    parser.add_argument("--pidfile", default=None, help="Pfad zur PID-Datei (optional)")
    parser.add_argument("--foreground", action="store_true", help="Nicht daemonisieren, Logs auf Stdout")
    args = parser.parse_args()

    cfg_path = Path(args.config)
    cfg = load_config(cfg_path)

    # Ordner
    tl_folder = Path(cfg.get("timelapse_folder", "./timelapse"))
    log_folder = Path(cfg.get("log_folder", "./logs"))
    ensure_folder(tl_folder)
    ensure_folder(log_folder)

    # Logging
    log_file = log_folder / "timelapse.log"
    logger = logging.getLogger("timelapse")
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s: %(message)s")
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    if args.foreground:
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        logger.addHandler(sh)

    # PID-Datei
    pidfile = Path(args.pidfile) if args.pidfile else (log_folder / "timelapse.pid")

    # Signale
    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)

    # Kameraauswahl
    wanted = cfg.get("camera_id")
    cam_index = choose_camera(Picamera2, wanted)
    picam2 = Picamera2(camera_num=cam_index)

    try:
        configure_camera(picam2, cfg, logger)

        # Start Kamera
        picam2.start()
        # kurze Aufwärmzeit
        time.sleep(0.5)

        write_pidfile(pidfile, logger)
        logger.info("Timelapse gestartet (Kamera %s, Auflösung %sx%s)", wanted or cam_index, *cfg.get("resolution", [1920, 1080]))

        min_interval = float(cfg.get("min_interval", 10.0))
        raw_delay = float(cfg.get("raw_delay", 0.0))
        duration = float(cfg.get("duration", 0))
        jpeg_quality = int(cfg.get("jpeg_quality", 90))
                # JPEG-Qualität für den Encoder setzen (statt quality= in capture_*)
        try:
            picam2.options["quality"] = int(jpeg_quality)
        except Exception:
            logger.debug("JPEG-Qualität konnte nicht gesetzt werden (options['quality']).")

        # RAW/DNG-Export
        
        save_raw = bool(cfg.get("save_raw", False))
        raw_format = str(cfg.get("raw_format", "dng")).strip().lower()  # "dng" empfohlen
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
                time.sleep(min(0.2, next_due - now))
                continue

            shot += 1
            ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
            date_str = datetime.now().strftime("%Y-%m-%d")
            jpg_dir = tl_folder / "lux" / date_str
            ensure_folder(jpg_dir)
            jpg_path = jpg_dir / f"{ts}.jpg"
            try:
                if save_raw and raw_format in ("dng", "dng8", "dng12"):
                    raw_date_dir = raw_folder / "lux" / date_str
                    ensure_folder(raw_date_dir)
                    dng_path = raw_date_dir / f"{ts}.dng"

                    try:
                        # JPEG + DNG gleichzeitig
                        picam2.capture_files({"main": str(jpg_path), "raw": str(dng_path)})
                        logger.info("Bild %d gespeichert: %s (+ DNG: %s)", shot, jpg_path, dng_path)
                    except Exception as e:
                        # Fallback: erst JPEG, dann DNG versuchen
                        logger.warning("capture_files fehlgeschlagen (%s) – versuche Fallback.", e)
                        picam2.capture_file(str(jpg_path))
                        if raw_delay > 0:
                            time.sleep(raw_delay)
                        try:
                            picam2.capture_file(str(dng_path))  # viele Picamera2-Versionen erkennen .dng automatisch
                            logger.info("DNG gespeichert (Fallback): %s", dng_path)
                        except Exception as e2:
                            logger.error("DNG-Fallback fehlgeschlagen: %s", e2)
                else:
                    # Nur JPEG
                    picam2.capture_file(str(jpg_path))
                    logger.info("Bild %d gespeichert: %s", shot, jpg_path)
                    if raw_delay > 0:
                        time.sleep(raw_delay)
            except Exception as e:
                logger.error("Fehler beim Aufnehmen: %s", e)


            # Nächsten Zeitpunkt setzen
            next_due += max(min_interval, 0.0)

        logger.info("Timelapse wird beendet…")
    finally:
        try:
            picam2.stop()
        except Exception:
            pass
        remove_pidfile(pidfile, logger)
        logger.info("Timelapse gestoppt.")


if __name__ == "__main__":
    main()