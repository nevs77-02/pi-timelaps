#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
lux_exposured.py – Regler-Daemon für Shutter & Gain nach Lux (v2.3.0)
- EMA, Step-Limiter, optionale 50 Hz-Quantisierung
- Schreibt NUR 'shutter' & 'gain' in config_tl.json (atomar)
- NEUE FUNKTIONALITÄT: Robuster Astro-Modus mit Hysterese und Haltezeiten.
- Nutzt Loguru für robustes Logging
"""
import argparse, csv, json, time, math
from pathlib import Path
from loguru import logger
import sys
import fcntl, os
from contextlib import contextmanager

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
# --- Defaults / Pfade ---
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CFG_PATH = SCRIPT_DIR / "config_tl.json"
DEFAULT_CTL_PATH = SCRIPT_DIR / "lux_exposured.json"
DEFAULT_CSV_PATH = SCRIPT_DIR / "sensor_log.csv"
DEFAULT_LUX_COLUMN = "veml_autolux"
LOG_ROOT = Path(os.environ.get("LOG_ROOT") or "/mnt/hdd/timelapse/logs")
LOG_PATH = LOG_ROOT / "lux_exposured.log"

# --- Utilities ---
def load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}

def save_json_atomic(path: Path, data: dict) -> bool:
    try:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
        return True
    except Exception:
        logger.exception(f"Save failed for {path}")
        return False

def read_lux_avg(csv_path: Path, column: str, samples: int):
    try:
        with csv_path.open("r", encoding="utf-8") as f:
            rows = list(csv.reader(f))
        if not rows or len(rows) < 2:
            return None
        header = rows[0]
        idx = header.index(column) if column in header else None
        if idx is None:
            logger.error(f"Lux column '{column}' not in CSV header {header}")
            return None
        data = rows[1:]
        take = data[-samples:] if len(data) >= samples else data
        vals = []
        for r in take:
            try: vals.append(float(r[idx]))
            except: pass
        if not vals: return None
        return sum(vals)/len(vals)
    except FileNotFoundError:
        logger.warning(f"Sensor CSV missing: {csv_path}")
        return None
    except Exception:
        logger.exception("CSV read error")
        return None

def clamp(x, lo, hi):
    return lo if x < lo else hi if x > hi else x

def quantize(x, step):
    if step and step > 1:
        return int(round(x / step) * step)
    return int(round(x))

def loglog_interp_exposure(lux, table):
    if lux <= 0: lux = 1e-6
    t = sorted(table, key=lambda e: e["lux"], reverse=True)
    if lux >= t[0]["lux"]: return float(t[0]["et_us"])
    if lux <= t[-1]["lux"]: return float(t[-1]["et_us"])
    lx = math.log(lux)
    for i in range(len(t) - 1):
        hi, lo = t[i], t[i + 1]
        if lo["lux"] <= lux <= hi["lux"]:
            lhi, llo = math.log(hi["lux"]), math.log(lo["lux"])
            thi, tlo = math.log(float(hi["et_us"])), math.log(float(lo["et_us"]))
            u = (lx - llo) / (lhi - llo)
            return math.exp(tlo + u * (thi - tlo))
    return float(t[-1]["et_us"])

# --- Main Logic ---
def compute_targets(lux_avg, camera_id, ctl, live_cfg, ema_et_prev):
    table = (ctl.get("tables") or {}).get(str(camera_id).lower()) or ctl.get("table")
    if not table:
        logger.warning(f"No exposure table found for camera '{camera_id}' or globally. Using fallback.")
        table = [
            {"lux": 2000, "et_us": 4000}, {"lux": 200, "et_us": 40000},
            {"lux": 20, "et_us": 400000}, {"lux": 2, "et_us": 1200000},
            {"lux": 0.2, "et_us": 8000000}
        ]
    et = loglog_interp_exposure(lux_avg, table)
    alpha = float(ctl.get("smoothing_et", 0.7))
    ema_et = alpha * ema_et_prev + (1.0 - alpha) * et if ema_et_prev > 0 else et
    min_interval = float(live_cfg.get("min_interval", 10.0))
    raw_delay = float(live_cfg.get("raw_delay", 0.0))
    overhead = float(ctl.get("interval_overhead_s", 0.5))
    max_by_interval = max(0.0, (min_interval - raw_delay - overhead)) * 1_000_000.0
    min_s = int(ctl.get("min_shutter_us", 100))
    max_s = int(min(float(ctl.get("max_shutter_us", 9_000_000)), max_by_interval))
    min_g = float(ctl.get("min_gain", 1.0))
    max_g_global = float(ctl.get("max_gain", 16.0))
    max_g_cam = float((ctl.get("max_gain_by_camera") or {}).get(str(camera_id).lower(), max_g_global))
    max_g = min(max_g_cam, max_g_global)
    tgt_s = clamp(ema_et, min_s, max_s)
    tgt_g = clamp(ema_et / max(tgt_s, 1.0), min_g, max_g)
    qstep = int(ctl.get("quantize_shutter_us", 0))
    qmin = int(ctl.get("quantize_min_us", 8000))
    if qstep > 0 and tgt_s >= qmin: tgt_s = quantize(tgt_s, qstep)
    return int(tgt_s), float(tgt_g), float(ema_et), float(et)

def main():
    ap = argparse.ArgumentParser(description="Lux-Regler für Shutter & Gain (v2.3.0)")
    ap.add_argument("--config", default=str(DEFAULT_CFG_PATH), help="Path to config_tl.json")
    ap.add_argument("--ctl", default=str(DEFAULT_CTL_PATH), help="Path to lux_exposured.json")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    
    # Loguru-Konfiguration
    LOG_ROOT.mkdir(parents=True, exist_ok=True)
    logger.remove()
    logger.add(LOG_PATH, rotation="10 MB", compression="zip", retention="10 days")
    if not args.quiet:
        logger.add(sys.stderr)

    cfg_path = Path(args.config)
    ctl_path = Path(args.ctl)
    ctl = load_json(ctl_path)
    
    astro_enter_lux = float(ctl.get("astro_enter_lux", 0.05))
    astro_exit_lux = float(ctl.get("astro_exit_lux", astro_enter_lux * 2.0))
    hold_on_s = float(ctl.get("astro_enter_hold_s", 60.0))
    hold_off_s = float(ctl.get("astro_exit_hold_s", 60.0))
    astro_shutter_us = int(ctl.get("astro_shutter_us", 8000000))
    astro_gain = float(ctl.get("astro_gain", 8.0))
    astro_min_interval_s = float(ctl.get("astro_min_interval_s", 60.0))

    live_cfg = load_json(cfg_path)
    camera_id = str(live_cfg.get("camera_id", "imx708")).lower()
    shutter = int(live_cfg.get("shutter", 4000))
    gain = float(live_cfg.get("gain", 1.0))
    ema_et = max(1.0, shutter * max(gain, 1.0))
    last_cam = camera_id
    hold_until = 0.0
    astro_active = False
    astro_written = False
    below_threshold_since = None
    above_threshold_since = None
    
    logger.info(f"Regler gestartet. Config={cfg_path}, Ctl={ctl_path}")

    while True:
        lux = read_lux_avg(Path(ctl["sensor_csv"]), str(ctl["sensor_column"]), int(ctl["avg_samples"]))
        if lux is None:
            time.sleep(float(ctl["interval_s"]))
            continue

        live_cfg = load_json(cfg_path)
        shutter = int(live_cfg.get("shutter", shutter))
        gain = float(live_cfg.get("gain", gain))
        camera_id = str(live_cfg.get("camera_id", "imx708")).lower()
        ae_on = bool(live_cfg.get("ae_enable", True))
        now = time.monotonic()

        if lux <= astro_enter_lux:
            if below_threshold_since is None: below_threshold_since = now
        else:
            below_threshold_since = None
        if lux >= astro_exit_lux:
            if above_threshold_since is None: above_threshold_since = now
        else:
            above_threshold_since = None

        if not astro_active and below_threshold_since and (now - below_threshold_since) >= hold_on_s:
            astro_active = True
            logger.info(f"ASTRO-Modus AKTIV (Lux={lux:.3f}).")
        
        if astro_active and above_threshold_since and (now - above_threshold_since) >= hold_off_s:
            astro_active = False
            astro_written = False
            logger.info(f"ASTRO-Modus DEAKTIVIERT (Lux={lux:.3f}).")

        if astro_active:
            if not astro_written and not args.dry_run:
                with cfg_lock():
                    new_cfg = load_json(cfg_path)  # frisch laden!
                    new_cfg["shutter"] = int(astro_shutter_us)
                    new_cfg["gain"] = float(round(astro_gain, 3))
                    # ggf. min_interval …
                    if save_json_atomic(cfg_path, new_cfg):
                        astro_written = True
                        logger.info(f"Astro-Werte geschrieben: shutter={astro_shutter_us}us, gain={astro_gain:.2f}.")
            logger.info(f"Lux={lux:.3f}  [Astro-Modus aktiv] – Werte sind fest")
            time.sleep(float(ctl["interval_s"]))
            continue

        if camera_id != last_cam:
            hold_s = float(ctl.get("hold_after_cam_switch_s", 0.0))
            if hold_s > 0:
                hold_until = now + hold_s
                logger.info(f"Kamerawechsel erkannt ({last_cam} -> {camera_id}). Pausiere {hold_s}s.")
            last_cam = camera_id
        if hold_until and now < hold_until:
            time.sleep(float(ctl["interval_s"])); continue
        
        tgt_s, tgt_g, ema_et, et_raw = compute_targets(lux, camera_id, ctl, live_cfg, ema_et)

        if bool(ctl.get("write_only_if_ae_off", True)) and ae_on:
            logger.info(
                f"Lux={lux:.3f}  et≈{int(ema_et)}us (raw≈{int(et_raw)}us)  [AE ON] → skip  (target_shutter≈{int(tgt_s)}us, target_gain≈{float(tgt_g):.2f})")
            time.sleep(float(ctl["interval_s"])); continue

        max_up_s = shutter * (1.0 + float(ctl["max_step_shutter_pct"]))
        max_dn_s = shutter * (1.0 - float(ctl["max_step_shutter_pct"]))
        prop_s = int(clamp(tgt_s, max_dn_s, max_up_s))
        max_up_g = gain * (1.0 + float(ctl["max_step_gain_pct"]))
        max_dn_g = gain * (1.0 - float(ctl["max_step_gain_pct"]))
        prop_g = float(clamp(tgt_g, max_dn_g, max_up_g))

        min_interval = float(live_cfg.get("min_interval", 10.0))
        raw_delay = float(live_cfg.get("raw_delay", 0.0))
        overhead = float(ctl.get("interval_overhead_s", 0.5))
        max_by_interval = max(0.0, (min_interval - raw_delay - overhead)) * 1_000_000.0
        prop_s = int(clamp(prop_s, int(ctl["min_shutter_us"]), int(min(ctl["max_shutter_us"], max_by_interval))))
        qstep = int(ctl.get("quantize_shutter_us", 0))
        qmin = int(ctl.get("quantize_min_us", 8000))
        if qstep > 0 and prop_s >= qmin: prop_s = quantize(prop_s, qstep)
        prop_g = float(clamp(prop_g, float(ctl["min_gain"]), min(float(ctl["max_gain"]), float((ctl.get("max_gain_by_camera") or {}).get(camera_id, ctl["max_gain"])))))

        s_thr = int(ctl["min_write_delta_shutter_us"])
        g_thr = float(ctl["min_write_delta_gain"])
        write_s = abs(prop_s - int(live_cfg.get("shutter", prop_s))) >= s_thr
        write_g = abs(prop_g - float(live_cfg.get("gain", prop_g))) >= g_thr
        
        wrote = False
        if (write_s or write_g) and not args.dry_run:
            with cfg_lock():
                new_cfg = load_json(cfg_path)  # frisch laden!
                if write_s:
                    new_cfg["shutter"] = int(prop_s)
                if write_g:
                    new_cfg["gain"] = float(round(prop_g, 3))
                wrote = save_json_atomic(cfg_path, new_cfg)

        
        logger.info(f"Lux={lux:.3f}  et≈{int(ema_et)}us (raw≈{int(et_raw)}us)  shutter→{prop_s}us{' *' if write_s and wrote else ''}  gain→{prop_g:.2f}{' *' if write_g and wrote else ''}")
        time.sleep(float(ctl["interval_s"]))

if __name__ == "__main__":
    main()