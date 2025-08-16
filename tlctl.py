#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tlctl.py — Control-Script zum Starten/Stoppen von main2.py
- Loggt Start/Stop/Status-Infos mit loguru
- Start: erzeugt einen Hintergrundprozess und schreibt PID-Datei
- Stop: sendet SIGTERM an den Prozess
- Status: zeigt an, ob der Prozess läuft
"""
from __future__ import annotations
import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
import json
from loguru import logger

# --- Pfade ---
THIS_DIR = Path(__file__).resolve().parent
MAIN = THIS_DIR / "main2.py"
LOG_ROOT = Path(os.environ.get("LOG_ROOT") or "/mnt/hdd/timelapse/logs")
LOG_PATH = LOG_ROOT / "tlctl.log"

def setup_logger():
    """Konfiguriert den Loguru-Logger für dieses Skript."""
    logger.remove()
    logger.add(sys.stderr, format="<green>{time}</green> <level>{level}</level>: <level>{message}</level>")
    LOG_ROOT.mkdir(parents=True, exist_ok=True)
    logger.add(LOG_PATH, rotation="10 MB", compression="zip", retention="10 days")

def read_config(config_path: Path) -> dict:
    try:
        return json.loads(config_path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error(f"Konnte Konfiguration nicht lesen von {config_path}: {e}")
        return {}

def pidfile_from_config(config_path: Path) -> Path:
    cfg = read_config(config_path)
    log_folder = Path(cfg.get("log_folder", "./logs"))
    log_folder.mkdir(parents=True, exist_ok=True)
    return log_folder / "timelapse.pid"

def read_pid(pidfile: Path) -> int | None:
    try:
        if not pidfile.exists():
            return None
        return int(pidfile.read_text(encoding="utf-8").strip())
    except Exception as e:
        logger.warning(f"Konnte PID-Datei nicht lesen: {e}")
        return None

def wait_for_exit(pid: int, timeout_s: float = 5.0) -> bool:
    end = time.time() + timeout_s
    while time.time() < end:
        try:
            os.kill(pid, 0)
            time.sleep(0.05)
        except ProcessLookupError:
            return True
    try:
        os.kill(pid, 0)
        return False
    except ProcessLookupError:
        return True

def is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False

def do_start(config_path: Path, foreground: bool = False):
    pidfile = pidfile_from_config(config_path)
    pid = read_pid(pidfile)
    if pid and is_running(pid):
        logger.info(f"Prozess läuft bereits (PID {pid}).")
        return 0
    if pid and not is_running(pid):
        logger.warning(f"Veraltete PID {pid} gefunden. Wird entfernt.")
        try:
            pidfile.unlink()
        except Exception:
            pass
    
    cmd = [sys.executable, str(MAIN), "--config", str(config_path)]
    if foreground:
        cmd.append("--foreground")
        logger.info(f"Starte main2.py im Vordergrund: {' '.join(str(c) for c in cmd)}")
        return subprocess.call(cmd)
    
    log_dir = pidfile.parent
    stdout_log = log_dir / "stdout.log"
    stderr_log = log_dir / "stderr.log"
    cmd.extend(["--pidfile", str(pidfile)])
    
    with open(stdout_log, "ab", buffering=0) as out, open(stderr_log, "ab", buffering=0) as err:
        proc = subprocess.Popen(
            cmd,
            stdout=out,
            stderr=err,
            preexec_fn=os.setsid,
            cwd=str(THIS_DIR),
        )
        logger.info(f"Hintergrundprozess gestartet: PID {proc.pid}. Logs: {stdout_log} / {stderr_log}")
        return 0

def do_stop(config_path: Path):
    pidfile = pidfile_from_config(config_path)
    pid = read_pid(pidfile)
    if not pid:
        logger.info("Nicht gestartet (keine PID-Datei gefunden).")
        return 0
    if not is_running(pid):
        logger.info(f"Prozess mit PID {pid} läuft nicht mehr. PID-Datei wird entfernt.")
        try:
            pidfile.unlink()
        except Exception:
            pass
        return 0
    
    os.kill(pid, signal.SIGTERM)
    logger.info(f"Stop-Signal (SIGTERM) an PID {pid} gesendet.")
    if not wait_for_exit(pid, timeout_s=5.0):
        logger.warning(f"PID {pid} beendet sich nicht. Sende SIGKILL...")
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        wait_for_exit(pid, timeout_s=2.0)
    
    try:
        pidfile.unlink()
        logger.info("PID-Datei entfernt.")
    except Exception as e:
        logger.error(f"Konnte PID-Datei nicht löschen: {e}")
    return 0

def do_status(config_path: Path):
    pidfile = pidfile_from_config(config_path)
    pid = read_pid(pidfile)
    if pid and is_running(pid):
        logger.info(f"LÄUFT (PID {pid})")
        return 0
    logger.info("STOPPED")
    return 1

def main():
    setup_logger()
    p = argparse.ArgumentParser(description="Start/Stop für main2.py")
    p.add_argument("command", choices=["start", "stop", "status", "restart"])
    p.add_argument("--config", default="config_tl.json")
    p.add_argument("--foreground", action="store_true", help="main2 im Vordergrund laufen lassen")
    args = p.parse_args()
    cfg_path = Path(args.config).resolve()
    if args.command == "start":
        sys.exit(do_start(cfg_path, foreground=args.foreground))
    elif args.command == "stop":
        sys.exit(do_stop(cfg_path))
    elif args.command == "status":
        sys.exit(do_status(cfg_path))
    elif args.command == "restart":
        do_stop(cfg_path)
        time.sleep(0.3)
        sys.exit(do_start(cfg_path, foreground=args.foreground))

if __name__ == "__main__":
    main()