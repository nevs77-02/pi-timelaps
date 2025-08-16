#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
awb_adjuster.py — Nacht-AWB-Gain-Regler

Zweck:
- Passt NUR nachts und NUR bei ausgeschaltetem AWB (awb_enable:false) die manuellen Gains
  'awb_gain_r' und 'awb_gain_b' in config_tl.json an.
- Tagsüber (awb_enable:true) wird nichts verändert.
- Nacht wird über Lux-Gate erkannt (Durchschnitt <= night_max_lux).

Abhängigkeiten:
    pip install loguru
"""

from __future__ import annotations
import argparse
import csv
import json
import signal
import sys
import time
from pathlib import Path
from typing import Optional, Dict, Any
from loguru import logger
import fcntl, os
from contextlib import contextmanager

# Gemeinsamer Config-Write-Lock
LOCK_PATH = "/tmp/tlcfg.lock"

@contextmanager
def cfg_lock():
    fd = os.open(LOCK_PATH, os.O_CREAT | os.O_RDWR, 0o666)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
        
SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_JSON = SCRIPT_DIR / "awb_adjuster.json"

# ------------------------------- Defaults --------------------------------

DEFAULTS = {
    "config_path": str(SCRIPT_DIR / "config_tl.json"),
    "color_csv": str(SCRIPT_DIR / "color_log.csv"),
    "lux_csv": str(SCRIPT_DIR / "sensor_log.csv"),
    "log_path": str(SCRIPT_DIR / "logs" / "awb_adjuster.log"),

    "color_red_col": "tcs_red",
    "color_green_col": "tcs_green",
    "color_blue_col": "tcs_blue",
    "lux_col": "veml_autolux",

    "interval_s": 5,
    "lux_window_samples": 10,
    "require_awb_disabled": True,
    "use_lux_gate": True,
    "night_max_lux": 1.0,

    "deadband": 0.03,
    "k_p": 0.5,
    "step_max": 0.05,
    "smoothing_alpha": 0.3,

    "gain_min": 0.5,
    "gain_max": 8.0,
}

# ------------------------------- Utils -----------------------------------

def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)

def load_json_optional(path: Path) -> Dict[str, Any]:
    try:
        return load_json(path)
    except FileNotFoundError:
        return {}
    except Exception as e:
        logger.warning(f"Konnte {path} nicht lesen: {e}")
        return {}

def save_json_atomic(path: Path, data: Dict[str, Any]) -> bool:
    tmp = Path(str(path) + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    tmp.replace(path)
    return True

def ensure_parent(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)

def read_last_rgb(csv_path: Path, r_col: str, g_col: str, b_col: str) -> Optional[tuple[float,float,float]]:
    try:
        with csv_path.open("r", encoding="utf-8") as f:
            rdr = csv.DictReader(f)
            rows = list(rdr)
            if not rows:
                return None
            last = rows[-1]
            r = float(last[r_col]); g = float(last[g_col]); b = float(last[b_col])
            if g <= 0 or r <= 0 or b <= 0:
                return None
            return (r,g,b)
    except Exception:
        return None

def get_last_lux_avg(csv_path: Path, column: str, num_samples: int) -> Optional[float]:
    try:
        with Path(csv_path).open("r", encoding="utf-8") as f:
            rdr = csv.reader(f)
            header = next(rdr)
            try:
                idx = header.index(column)
            except ValueError:
                return None
            rows = list(rdr)
            if not rows:
                return None
            chunk = rows[-num_samples:] if len(rows) >= num_samples else rows
            vals = []
            for r in chunk:
                try:
                    vals.append(float(r[idx]))
                except Exception:
                    pass
            return sum(vals)/len(vals) if vals else None
    except Exception:
        return None

def clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x

# ------------------------------- Core ------------------------------------

def compute_target_gain(current_gain: float, ratio: float,
                        deadband: float, k_p: float,
                        step_max: float, alpha: float,
                        gmin: float, gmax: float) -> float:
    err = 1.0 - ratio
    if abs(err) <= deadband:
        return current_gain
    rel = k_p * err
    rel = clamp(rel, -step_max, step_max)
    proposed = current_gain * (1.0 + rel)
    target = (1.0 - alpha) * current_gain + alpha * proposed
    return clamp(target, gmin, gmax)

def adjust_once(cfg: Dict[str,Any]) -> bool:
    # Lux-Gate
    if cfg["use_lux_gate"]:
        avg = get_last_lux_avg(Path(cfg["lux_csv"]), cfg["lux_col"], cfg["lux_window_samples"])
        if avg is None or avg > cfg["night_max_lux"]:
            return False

    # AWB-Gate
    if cfg["require_awb_disabled"]:
        live_cfg = load_json(Path(cfg["config_path"]))
        if live_cfg.get("awb_enable", True):
            return False
        current_r = float(live_cfg.get("awb_gain_r", 1.0))
        current_b = float(live_cfg.get("awb_gain_b", 1.0))
    else:
        live_cfg = load_json(Path(cfg["config_path"]))
        current_r = float(live_cfg.get("awb_gain_r", 1.0))
        current_b = float(live_cfg.get("awb_gain_b", 1.0))

    rgb = read_last_rgb(Path(cfg["color_csv"]),
                        cfg["color_red_col"], cfg["color_green_col"], cfg["color_blue_col"])
    if not rgb:
        return False
    r,g,b = rgb

    ratio_r = r/g
    ratio_b = b/g

    new_r = compute_target_gain(current_r, ratio_r,
                                cfg["deadband"], cfg["k_p"],
                                cfg["step_max"], cfg["smoothing_alpha"],
                                cfg["gain_min"], cfg["gain_max"])
    new_b = compute_target_gain(current_b, ratio_b,
                                cfg["deadband"], cfg["k_p"],
                                cfg["step_max"], cfg["smoothing_alpha"],
                                cfg["gain_min"], cfg["gain_max"])

    if abs(new_r - current_r) < 1e-3 and abs(new_b - current_b) < 1e-3:
        return False

# ...
    with cfg_lock():
        latest = load_json(Path(cfg["config_path"]))
        latest["awb_gain_r"] = round(new_r, 3)
        latest["awb_gain_b"] = round(new_b, 3)
        save_json_atomic(Path(cfg["config_path"]), latest)
    logger.info(f"→ AWB-Gains angepasst: R {current_r:.3f}->{new_r:.3f}, B {current_b:.3f}->{new_b:.3f}")
    return True

# ------------------------------- Main ------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=DEFAULTS["config_path"], help="Pfad zu config_tl.json")
    args = ap.parse_args()

    overrides = load_json_optional(CONFIG_JSON)
    cfg = DEFAULTS.copy()
    cfg.update(overrides)
    cfg["config_path"] = args.config

    log_path = Path(cfg["log_path"])
    ensure_parent(log_path)
    logger.add(log_path, rotation="1 MB", retention=5, encoding="utf-8")

    stop_flag = {"stop": False}
    def _stop(*_): stop_flag["stop"] = True
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    logger.info("awb_adjuster gestartet.")
    try:
        while not stop_flag["stop"]:
            try:
                adjust_once(cfg)
            except Exception as e:
                logger.error(f"Fehler im adjust_once: {e}")
            time.sleep(cfg["interval_s"])
    finally:
        logger.info("awb_adjuster beendet.")

if __name__ == "__main__":
    main()
