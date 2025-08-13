#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tlctl.py — Control-Script zum Starten/Stoppen von main2.py
- Start: erzeugt einen Hintergrundprozess und schreibt PID-Datei
- Stop: sendet SIGTERM an den Prozess
- Status: zeigt an, ob der Prozess läuft

Beispiel:
  python3 tlctl.py start --config ./config.json
  python3 tlctl.py status --config ./config.json
  python3 tlctl.py stop   --config ./config.json
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


THIS_DIR = Path(__file__).resolve().parent
MAIN = THIS_DIR / "main2.py"


def read_config(config_path: Path) -> dict:
    with config_path.open("r", encoding="utf-8") as f:
        return json.load(f)


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
    except Exception:
        return None


def is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def do_start(config_path: Path, foreground: bool = False):
    pidfile = pidfile_from_config(config_path)
    pid = read_pid(pidfile)
    if pid and is_running(pid):
        print(f"Schon gestartet (PID {pid}).")
        return 0

    log_dir = pidfile.parent
    stdout_log = log_dir / "stdout.log"
    stderr_log = log_dir / "stderr.log"

    cmd = [sys.executable, str(MAIN), "--config", str(config_path), "--pidfile", str(pidfile)]
    if foreground:
        cmd.append("--foreground")
        return subprocess.call(cmd)

    with open(stdout_log, "ab", buffering=0) as out, open(stderr_log, "ab", buffering=0) as err:
        # Hintergrundprozess starten (neue Session)
        proc = subprocess.Popen(
            cmd,
            stdout=out,
            stderr=err,
            preexec_fn=os.setsid,
            cwd=str(THIS_DIR),
        )
        print(f"Gestartet: PID {proc.pid}. Logs: {stdout_log} / {stderr_log}")
        return 0


def do_stop(config_path: Path):
    pidfile = pidfile_from_config(config_path)
    pid = read_pid(pidfile)
    if not pid:
        print("Nicht gestartet (keine PID-Datei gefunden).")
        return 0
    if not is_running(pid):
        print("Prozess läuft nicht mehr. PID-Datei wird ignoriert.")
        try:
            pidfile.unlink()
        except Exception:
            pass
        return 0
    os.kill(pid, signal.SIGTERM)
    print(f"Stop-Signal an PID {pid} gesendet.")
    return 0


def do_status(config_path: Path):
    pidfile = pidfile_from_config(config_path)
    pid = read_pid(pidfile)
    if pid and is_running(pid):
        print(f"LÄUFT (PID {pid})")
        return 0
    print("STOPPED")
    return 1


def main():
    p = argparse.ArgumentParser(description="Start/Stop für main2.py")
    p.add_argument("command", choices=["start", "stop", "status", "restart"])
    p.add_argument("--config", default="config.json")
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