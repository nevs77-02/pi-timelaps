#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Lux-basierter Preset-Controller für Timelapse
- Arbeitet mit main2.py (reine Timelapse) und tlctl.py (Start/Stop/Status)
- Liest/Schreibt Presets in die config.json
- Bestimmt anhand von Luxwerten das passende Preset und wendet es an
- Startet/Stoppt (bzw. Stop->Start) main2.py über tlctl.py, wenn kritische
  Konfigurationsänderungen vorliegen (z. B. Auflösung, HDR, Kamerawechsel …)

Voraussetzungen:
    - Python 3.9+
    - tlctl.py und main2.py im selben Ordner
    - lux_control.json (Steuerdatei, siehe unten)
    - Sensor-Log (CSV) mit einer Lux-Spalte (Standard: "veml_autolux")

Startbeispiel:
    python3 lux_controller.py            # Endlosschleife
    python3 lux_controller.py --once     # Einen Check durchführen
    python3 lux_controller.py --apply day  # Preset sofort setzen

UI-Hooks:
    - Die Datei lux_control.json ist die Schnittstelle für Buttons/Felder.
      Spätere UI-Events können direkt Werte darin ändern (enabled, mappings,
      check_interval_s, switch_delay_s, cooldown_s, force_preset etc.).
"""
from __future__ import annotations
import argparse
import csv
import json
import logging
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

# ------------------------- Pfade & Defaults -------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_FILE = SCRIPT_DIR / "config.json"
LUX_CONTROL_FILE = SCRIPT_DIR / "lux_control.json"
DEFAULT_PRESETS_DIR = Path("/mnt/hdd/timelapse/presets")
DEFAULT_SENSOR_LOG = SCRIPT_DIR / "sensor_log.csv"
DEFAULT_TLCTL_CMD = [sys.executable, str(SCRIPT_DIR / "tlctl.py")]  # python3 tlctl.py

# Kritische Keys: bei Änderung Timelapse neu starten
CRITICAL_KEYS = [
    "camera_id",
    "use_hdr",
    "resolution",
    "timelapse_folder",
    "raw_folder",
    "duration",
]

# ---------------------------- Utilities ----------------------------

def load_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as e:
        logging.error("JSON-Fehler in %s: %s", path, e)
        return None


def save_json(path: Path, data: Dict[str, Any]) -> bool:
    try:
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        tmp.replace(path)
        return True
    except Exception as e:
        logging.exception("Konnte %s nicht speichern: %s", path, e)
        return False


@dataclass
class LuxCtlConfig:
    enabled: bool = True
    check_interval_s: int = 60
    switch_delay_s: int = 300
    cooldown_s: int = 900
    sensor_log_csv: Path = DEFAULT_SENSOR_LOG
    sensor_lux_column: str = "veml_autolux"
    presets_dir: Path = DEFAULT_PRESETS_DIR
    tlctl_cmd: List[str] = None  # wird unten befüllt
    force_preset: Optional[str] = None  # wenn gesetzt: sofort anwenden & halten

    @staticmethod
    def from_json(d: Optional[Dict[str, Any]]) -> "LuxCtlConfig":
        cfg = LuxCtlConfig()
        if not d:
            cfg.tlctl_cmd = DEFAULT_TLCTL_CMD
            return cfg
        cfg.enabled = bool(d.get("enabled", cfg.enabled))
        cfg.check_interval_s = int(max(1, d.get("check_interval_s", cfg.check_interval_s)))
        cfg.switch_delay_s = int(max(1, d.get("switch_delay_s", cfg.switch_delay_s)))
        cfg.cooldown_s = int(max(1, d.get("cooldown_s", cfg.cooldown_s)))
        cfg.sensor_log_csv = Path(d.get("sensor_log_csv", str(cfg.sensor_log_csv)))
        cfg.sensor_lux_column = str(d.get("sensor_lux_column", cfg.sensor_lux_column))
        cfg.presets_dir = Path(d.get("presets_dir", str(cfg.presets_dir)))
        # tlctl als Liste (Befehl + args) erlauben oder String splitten
        tcmd = d.get("tlctl") or d.get("tlctl_cmd")
        if isinstance(tcmd, list) and tcmd:
            cfg.tlctl_cmd = [str(x) for x in tcmd]
        elif isinstance(tcmd, str) and tcmd.strip():
            cfg.tlctl_cmd = tcmd.strip().split()
        else:
            cfg.tlctl_cmd = DEFAULT_TLCTL_CMD
        cfg.force_preset = d.get("force_preset")
        return cfg


# ---------------------- TLCTL Interaktion ----------------------

def _tlctl(args: List[str], *, config_path: Path) -> subprocess.CompletedProcess:
    cmd = args + ["--config", str(config_path)]
    return subprocess.run(cmd, cwd=str(SCRIPT_DIR), capture_output=True, text=True)


def tl_status(tlctl_cmd: List[str], *, config_path: Path) -> bool:
    cp = _tlctl(tlctl_cmd + ["status"], config_path=config_path)
    return cp.returncode == 0


def tl_start(tlctl_cmd: List[str], *, config_path: Path, foreground: bool = False) -> bool:
    cmd = tlctl_cmd + ["start", "--config", str(config_path)]
    if foreground:
        cmd.append("--foreground")
    cp = subprocess.run(cmd, cwd=str(SCRIPT_DIR))
    return cp.returncode == 0


def tl_stop(tlctl_cmd: List[str], *, config_path: Path) -> bool:
    cp = _tlctl(tlctl_cmd + ["stop"], config_path=config_path)
    return cp.returncode == 0


def tl_restart(tlctl_cmd: List[str], *, config_path: Path, sleep_s: float = 0.4) -> bool:
    # Nutzt bewusst Stop->Start, um keine Abhängigkeit von tlctl "restart" zu haben
    tl_stop(tlctl_cmd, config_path=config_path)
    if sleep_s:
        time.sleep(sleep_s)
    return tl_start(tlctl_cmd, config_path=config_path)


# ---------------------- Lux-Auswertung ----------------------

def get_last_lux_avg(csv_path: Path, column: str, num_samples: int) -> Optional[float]:
    try:
        with csv_path.open("r", encoding="utf-8") as f:
            reader = csv.reader(f)
            header = next(reader)
            try:
                idx = header.index(column)
            except ValueError:
                logging.error("Lux-Spalte '%s' nicht gefunden in %s (Header: %s)", column, csv_path, header)
                return None
            rows = list(reader)
            if not rows:
                return None
            last = rows[-num_samples:] if len(rows) >= num_samples else rows
            vals: List[float] = []
            for r in last:
                try:
                    vals.append(float(r[idx]))
                except (ValueError, IndexError):
                    continue
            if not vals:
                return None
            return sum(vals) / len(vals)
    except FileNotFoundError:
        logging.warning("Sensor-CSV fehlt: %s", csv_path)
        return None
    except Exception:
        logging.exception("Fehler beim Lesen von %s", csv_path)
        return None


# --------------------- Preset-Anwendung ----------------------

def apply_preset_to_config(preset: str, presets_dir: Path, config_path: Path) -> bool:
    # Erlaube Namen ("day") oder absolute Pfade
    ppath = Path(preset) if os.path.isabs(preset) else (presets_dir / f"{preset}.json")
    if not ppath.exists():
        logging.error("Preset nicht gefunden: %s", ppath)
        return False
    pdata = load_json(ppath)
    if pdata is None:
        logging.error("Preset JSON ungültig: %s", ppath)
        return False
    ok = save_json(config_path, pdata)
    if ok:
        logging.info("Preset angewendet: %s → %s", ppath, config_path)
    return ok


def needs_restart(old_cfg: Dict[str, Any], new_cfg: Dict[str, Any]) -> bool:
    for k in CRITICAL_KEYS:
        if old_cfg.get(k) != new_cfg.get(k):
            logging.info("Kritische Änderung erkannt (%s): %r -> %r", k, old_cfg.get(k), new_cfg.get(k))
            return True
    return False


# ----------------------- Kern-Controller ----------------------

def load_lux_ctl_config() -> LuxCtlConfig:
    return LuxCtlConfig.from_json(load_json(LUX_CONTROL_FILE))


def choose_preset(avg_lux: float, mappings: List[Dict[str, Any]]) -> Optional[str]:
    for m in mappings:
        try:
            if float(m["min_lux"]) <= avg_lux <= float(m["max_lux"]):
                return str(m["preset"])  # z. B. "night", "dawn", "day"
        except Exception:
            continue
    return None


def controller_loop(args):
    logger = logging.getLogger("luxctl")
    logger.info("Lux-Controller gestartet.")

    current_preset: Optional[str] = None
    last_switch: float = time.time() - 99999  # sofortiger erster Wechsel möglich

    while True:
        cfg = load_lux_ctl_config()
        if not cfg.enabled:
            logger.info("Lux-Kontrolle deaktiviert. Warte 60s…")
            time.sleep(60)
            continue

        # Force-Override
        if cfg.force_preset:
            logger.info("Force-Preset aktiv: %s", cfg.force_preset)
            old = load_json(CONFIG_FILE) or {}
            if apply_preset_to_config(cfg.force_preset, cfg.presets_dir, CONFIG_FILE):
                new = load_json(CONFIG_FILE) or {}
                if needs_restart(old, new) or not tl_status(cfg.tlctl_cmd, config_path=CONFIG_FILE):
                    tl_restart(cfg.tlctl_cmd, config_path=CONFIG_FILE)
                current_preset = cfg.force_preset
            time.sleep(cfg.check_interval_s)
            continue

        # Lux-Fenster
        samples = max(1, round(cfg.switch_delay_s / cfg.check_interval_s))
        avg = get_last_lux_avg(cfg.sensor_log_csv, cfg.sensor_lux_column, samples)
        if avg is None:
            time.sleep(cfg.check_interval_s)
            continue
        logger.info("Durchschnittlicher Lux-Wert (n=%d): %.2f", samples, avg)

        # Mappings laden
        lux_json = load_json(LUX_CONTROL_FILE) or {}
        mappings: List[Dict[str, Any]] = lux_json.get("mappings", [])
        preset = choose_preset(avg, mappings)
        logger.info("Ermitteltes Preset: %s", preset or "—")

        # Wechsel?
        now = time.time()
        if preset and preset != current_preset:
            if now - last_switch >= cfg.cooldown_s:
                old_cfg = load_json(CONFIG_FILE) or {}
                if apply_preset_to_config(preset, cfg.presets_dir, CONFIG_FILE):
                    new_cfg = load_json(CONFIG_FILE) or {}
                    if needs_restart(old_cfg, new_cfg) or not tl_status(cfg.tlctl_cmd, config_path=CONFIG_FILE):
                        logger.info("Starte Timelapse neu (kritische Änderung oder nicht laufend).")
                        tl_restart(cfg.tlctl_cmd, config_path=CONFIG_FILE)
                    else:
                        logger.info("Keine kritischen Änderungen – kein Neustart nötig.")
                    current_preset = preset
                    last_switch = now
            else:
                left = int(cfg.cooldown_s - (now - last_switch))
                logger.info("Neues Preset erkannt, aber Cooldown aktiv (%ss).", left)

        if args.once:
            break
        time.sleep(cfg.check_interval_s)


# ------------------------------ CLI ------------------------------

def setup_logging(log_path: Optional[Path] = None, *, verbose: bool = True):
    handlers = []
    fmt = logging.Formatter("%(asctime)s %(levelname)s: %(message)s")
    if verbose:
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        handlers.append(sh)
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setFormatter(fmt)
        handlers.append(fh)
    logging.basicConfig(level=logging.INFO, handlers=handlers)


def main():
    ap = argparse.ArgumentParser(description="Lux-basierter Preset-Controller für Timelapse")
    ap.add_argument("--once", action="store_true", help="Nur einen Durchlauf ausführen und beenden")
    ap.add_argument("--apply", metavar="PRESET", help="Preset sofort anwenden (Name oder absoluter Pfad)")
    ap.add_argument("--log", metavar="PFAD", help="Pfad zur Logdatei (optional)")
    ap.add_argument("--quiet", action="store_true", help="Kein Logging auf Stdout")
    args = ap.parse_args()

    # Logging konfigurieren (optional in config.json -> log_folder)
    cfg_json = load_json(CONFIG_FILE) or {}
    log_folder = Path(cfg_json.get("log_folder", SCRIPT_DIR / "logs"))
    log_path = Path(args.log) if args.log else (log_folder / "lux_controller.log")
    setup_logging(log_path, verbose=not args.quiet)

    # Einmaliges Anwenden eines Presets (z. B. aus UI)
    if args.apply:
        cfg = load_lux_ctl_config()
        old_cfg = load_json(CONFIG_FILE) or {}
        if apply_preset_to_config(args.apply, cfg.presets_dir, CONFIG_FILE):
            new_cfg = load_json(CONFIG_FILE) or {}
            if needs_restart(old_cfg, new_cfg) or not tl_status(cfg.tlctl_cmd, config_path=CONFIG_FILE):
                tl_restart(cfg.tlctl_cmd, config_path=CONFIG_FILE)
        return

    # Signal-Handling (sauber beenden)
    stop_flag = {"stop": False}

    def _stop(*_):
        logging.getLogger("luxctl").info("Beende lux_controller…")
        stop_flag["stop"] = True

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    # Hauptschleife
    try:
        while not stop_flag["stop"]:
            controller_loop(args)
            if args.once:
                break
    finally:
        logging.getLogger("luxctl").info("lux_controller beendet.")


if __name__ == "__main__":
    main()
