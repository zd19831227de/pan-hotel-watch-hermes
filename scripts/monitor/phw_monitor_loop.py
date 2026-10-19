#!/usr/bin/env python3
"""Background monitor loop for Pan Hotel Watch.

Runs scripts/monitor/phw_check.py every INTERVAL_MIN minutes until STOP_HOUR (local).
Appends each reading as one JSON line to the readings log (JSONL).
Records each anomaly into a separate anomalies log with full reading context.
Never starts/stops/kills any process or edits any code.

Env knobs:
  PHW_API_BASE, PHW_FRONTEND_URL, PHW_BACKEND_PID, PHW_SCHEDULER_PID,
  PHW_HERMES_PID, PHW_FRONTEND_PID  -> forwarded to phw_check.py
  PHW_MONITOR_DIR  -> base dir (default: <repo>/scripts/monitor)
  PHW_RUN_DIR      -> run/ (default: <repo>/.runtime/monitor)
  PHW_INTERVAL_MIN -> tick interval (default 10)
  PHW_STOP_HOUR    -> local hour to stop at (default 8)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
RUN_DIR = Path(os.environ.get("PHW_RUN_DIR", REPO / ".runtime" / "monitor"))
RUN_DIR.mkdir(parents=True, exist_ok=True)
CHECK = HERE / "phw_check.py"

READINGS_LOG = RUN_DIR / "readings.jsonl"
ANOMALIES_LOG = RUN_DIR / "anomalies.jsonl"
CONSOLE_LOG = RUN_DIR / "monitor_console.log"
INTERVAL_MIN = int(os.environ.get("PHW_INTERVAL_MIN", "10"))
STOP_HOUR = int(os.environ.get("PHW_STOP_HOUR", "8"))


def _log_console(msg: str) -> None:
    line = f"[{datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with CONSOLE_LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _next_stop_dt(now: datetime) -> datetime:
    stop = now.replace(hour=STOP_HOUR, minute=0, second=0, microsecond=0)
    if stop <= now:
        from datetime import timedelta
        stop = stop + timedelta(days=1)
    return stop


def _run_check() -> dict:
    env = dict(os.environ)
    py = env.get("PHW_PYTHON", str(REPO / ".venv" / "Scripts" / "python.exe"))
    proc = subprocess.run(
        [py, str(CHECK)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, timeout=120,
    )
    out = (proc.stdout or "").strip().splitlines()
    reading = None
    if out:
        # last non-empty JSON line is the reading
        for line in reversed(out):
            line = line.strip()
            if line.startswith("{"):
                try:
                    reading = json.loads(line)
                except Exception:
                    pass
                if reading:
                    break
    if reading is None:
        reading = {
            "checked_at_local": datetime.now().astimezone().isoformat(),
            "overall_status": "fail",
            "anomalies": [f"collector produced no JSON (exit={proc.returncode})"],
            "collector_stderr": (proc.stderr or "")[-2000:],
        }
    return reading


def main() -> int:
    stop_dt = _next_stop_dt(datetime.now())
    _log_console(
        f"monitor start: interval={INTERVAL_MIN}m stop_at={stop_dt.isoformat()} "
        f"readings={READINGS_LOG} anomalies={ANOMALIES_LOG}"
    )
    tick = 0
    while True:
        now = datetime.now()
        if now >= stop_dt:
            _log_console(f"reached stop time {stop_dt.isoformat()}; exiting after {tick} ticks")
            break
        tick += 1
        try:
            reading = _run_check()
        except subprocess.TimeoutExpired:
            reading = {
                "checked_at_local": now.astimezone().isoformat(),
                "overall_status": "fail",
                "anomalies": ["collector timed out (120s)"],
            }
        except Exception as e:
            reading = {
                "checked_at_local": now.astimezone().isoformat(),
                "overall_status": "fail",
                "anomalies": [f"loop error: {type(e).__name__}:{e}"],
            }

        with READINGS_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(reading, ensure_ascii=False) + "\n")
        if reading.get("anomalies"):
            with ANOMALIES_LOG.open("a", encoding="utf-8") as f:
                f.write(json.dumps(reading, ensure_ascii=False) + "\n")
        status = reading.get("overall_status", "?")
        anomalies = reading.get("anomalies", [])
        note = f" ({len(anomalies)} anomaly) " + "; ".join(anomalies[:3]) if anomalies else ""
        _log_console(f"tick #{tick} status={status}{note}")

        # sleep until next tick boundary (aligned to interval) but cap so we don't overshoot stop
        sleep_for = INTERVAL_MIN * 60
        # align to wall-clock interval boundaries for clean timestamps
        secs_into = now.minute * 60 + now.second
        boundary = INTERVAL_MIN * 60
        sleep_for = boundary - (secs_into % boundary)
        if sleep_for <= 0:
            sleep_for = boundary
        remaining = (stop_dt - datetime.now()).total_seconds()
        if remaining <= 0:
            _log_console(f"stop time reached during sleep; exiting after {tick} ticks")
            break
        time.sleep(min(sleep_for, max(remaining, 0)))
    _log_console("monitor finished")
    return 0


if __name__ == "__main__":
    sys.exit(main())
