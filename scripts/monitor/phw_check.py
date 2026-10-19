#!/usr/bin/env python3
"""Single-pass Pan Hotel Watch health check.

Run once per monitoring tick. Emits one JSON object on stdout (the "reading").
Designed to be cheap (sub-second), non-invasive (read-only), and robust to any
single probe failing. Never starts, stops, or modifies any process.

Exit code is always 0 so a caller loop is never broken by a bad tick; the
`overall_status` field carries pass/warn/fail.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

API_BASE = os.environ.get("PHW_API_BASE", "http://127.0.0.1:8100")
FRONTEND_URL = os.environ.get("PHW_FRONTEND_URL", "http://127.0.0.1:5173/")
# Baseline PIDs captured at monitoring start. None => not verified against a PID.
ENV_PIDS = {
    "backend_api": os.environ.get("PHW_BACKEND_PID"),
    "read_only_scheduler": os.environ.get("PHW_SCHEDULER_PID"),
    "hermes_gateway": os.environ.get("PHW_HERMES_PID"),
    "frontend": os.environ.get("PHW_FRONTEND_PID"),
}
EXPECTED_PROCS = {
    "backend_api": "uvicorn app.main:app",
    "read_only_scheduler": "app.workers.read_only_scheduler",
    "hermes_gateway": "hermes.exe",
    "frontend": "vite.js",
}
HEARTBEAT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    ".runtime",
    "heartbeats",
)
HEARTBEAT_FILES = {
    "ctrip_ebooking_data_center_scheduler": "ctrip_ebooking_data_center_scheduler.json",
    "meituan_ebooking_scheduler": "meituan_ebooking_scheduler.json",
    "fliggy_ebooking_scheduler": "fliggy_ebooking_scheduler.json",
    "competitor_price_scheduler": "competitor_price_scheduler.json",
    "calendar_price_watch_scheduler": "calendar_price_watch_scheduler.json",
    "ebk_global_push_scheduler": "ebk_global_push_scheduler.json",
}
HEARTBEAT_STALE_AFTER = 300  # seconds, matches scheduler stale_after
DB_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "pan_hotel_watch.db",
)
TIMEOUT = 8


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def _http_json(url: str, timeout: int = TIMEOUT):
    """Return (status_code_or_None, json_or_text, elapsed_s, error_or_None)."""
    t0 = time.perf_counter()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "phw-monitor/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            elapsed = time.perf_counter() - t0
            ctype = resp.headers.get("Content-Type", "")
            if "json" in ctype:
                return resp.status, json.loads(body.decode("utf-8", "replace")), elapsed, None
            return resp.status, body.decode("utf-8", "replace")[:500], elapsed, None
    except urllib.error.HTTPError as e:
        return e.code, None, time.perf_counter() - t0, f"HTTP {e.code}"
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError) as e:
        return None, None, time.perf_counter() - t0, type(e).__name__
    except Exception as e:  # last-resort isolation
        return None, None, time.perf_counter() - t0, f"{type(e).__name__}:{e}"


def _wmic_cmdlines() -> dict[int, str]:
    """Map PID -> lowercased command line via wmic (decode-tolerant)."""
    out: dict[int, str] = {}
    try:
        res = subprocess.run(
            ["wmic", "process", "get", "ProcessId,CommandLine", "/format:csv"],
            capture_output=True, timeout=20,
        )
        text = res.stdout.decode("utf-8", "replace")
        for line in text.splitlines():
            line = line.strip()
            if not line or "," not in line or "CommandLine" in line:
                continue
            parts = line.rsplit(",", 1)
            if len(parts) != 2:
                continue
            cmd, pid_s = parts
            try:
                pid = int(pid_s)
            except ValueError:
                continue
            out[pid] = cmd.lower()
    except Exception:
        pass
    return out


def _check_process(key: str, cmdlines: dict[int, str]) -> dict:
    needle = EXPECTED_PROCS[key]
    matches = [pid for pid, cmd in cmdlines.items() if needle in cmd]
    baseline = ENV_PIDS.get(key)
    rec: dict = {"expected_proc": needle, "running_pids": sorted(matches)}
    if not matches:
        rec["status"] = "fail"
        rec["note"] = "process not found"
        return rec
    rec["status"] = "ok"
    if baseline:
        try:
            bpid = int(baseline)
            if bpid in matches:
                rec["baseline_pid_alive"] = True
            else:
                rec["baseline_pid_alive"] = False
                rec["status"] = "warn"
                rec["note"] = f"baseline pid {bpid} gone; running as {matches}"
        except ValueError:
            pass
    return rec


def _heartbeat_age(path: str):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        ts = data.get("last_heartbeat_at") or data.get("updated_at")
        if not ts:
            return None, None, data, "no timestamp field"
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - dt).total_seconds()
        return age, ts, data, None
    except FileNotFoundError:
        return None, None, None, "heartbeat file missing"
    except Exception as e:
        return None, None, None, f"{type(e).__name__}:{e}"


def _check_db() -> dict:
    rec: dict = {"path": os.path.basename(DB_FILE)}
    if not os.path.exists(DB_FILE):
        rec["status"] = "fail"
        rec["note"] = "db file missing"
        return rec
    try:
        st = os.stat(DB_FILE)
        rec["size_mb"] = round(st.st_size / 1024 / 1024, 1)
        rec["mtime_iso"] = datetime.fromtimestamp(st.st_mtime).astimezone().isoformat()
        rec["status"] = "ok"
    except Exception as e:
        rec["status"] = "warn"
        rec["note"] = f"{type(e).__name__}:{e}"
    return rec


def collect() -> dict:
    reading: dict = {"checked_at_local": _now_iso()}
    anomalies: list[str] = []

    cmdlines = _wmic_cmdlines()
    reading["processes"] = {k: _check_process(k, cmdlines) for k in EXPECTED_PROCS}
    for k, rec in reading["processes"].items():
        if rec["status"] == "fail":
            anomalies.append(f"process {k} not running")

    # API health
    code, body, elapsed, err = _http_json(f"{API_BASE}/api/v1/health")
    reading["api_health"] = {"status_code": code, "elapsed_s": round(elapsed, 3), "error": err}
    if code != 200:
        anomalies.append(f"api /health bad: code={code} err={err}")

    # Runtime health (authoritative scheduler heartbeats via API)
    code, body, elapsed, err = _http_json(f"{API_BASE}/api/v1/ops/runtime-health", timeout=12)
    rh: dict = {"status_code": code, "elapsed_s": round(elapsed, 3), "error": err, "summary": None, "modules": {}}
    if isinstance(body, dict):
        rh["summary"] = body.get("summary")
        for mk, mv in (body.get("modules") or {}).items():
            rh["modules"][mk] = {
                "freshness": mv.get("freshness_status"),
                "heartbeat_status": mv.get("heartbeat_status"),
                "process_confirmed": mv.get("process_confirmed"),
                "heartbeat_age_s": mv.get("heartbeat_age_seconds"),
                "last_error": mv.get("last_error"),
            }
            if mv.get("last_error"):
                anomalies.append(f"module {mk} last_error={mv['last_error']}")
        summ = body.get("summary") or {}
        if summ.get("status") not in (None, "fresh", "healthy"):
            anomalies.append(f"runtime-health summary={summ.get('status')}")
        for m in summ.get("stale_or_missing_modules", []) or []:
            anomalies.append(f"runtime-health stale/missing: {m}")
    else:
        anomalies.append(f"api /ops/runtime-health unreadable: code={code} err={err}")
    reading["runtime_health"] = rh

    # Calendar scheduler status (separate PID + heartbeat signal)
    code, body, _, err = _http_json(f"{API_BASE}/api/v1/calendar-price-watch/scheduler-status")
    cs: dict = {"status_code": code, "error": err, "status": None, "pid": None, "heartbeat_age_s": None}
    if isinstance(body, dict):
        sch = body.get("scheduler") or {}
        cs.update({
            "status": sch.get("status"),
            "pid": sch.get("pid"),
            "heartbeat_age_s": sch.get("heartbeat_age_seconds"),
            "process_confirmed": sch.get("process_confirmed"),
        })
        if sch.get("status") not in (None, "healthy"):
            anomalies.append(f"calendar scheduler status={sch.get('status')}")
    reading["calendar_scheduler"] = cs

    # Frontend
    code, _, elapsed, err = _http_json(FRONTEND_URL)
    reading["frontend"] = {"status_code": code, "elapsed_s": round(elapsed, 3), "error": err}
    if code != 200:
        anomalies.append(f"frontend bad: code={code} err={err}")

    # On-disk heartbeat freshness (independent of API)
    reading["disk_heartbeats"] = {}
    for name, fname in HEARTBEAT_FILES.items():
        age, ts, _data, herr = _heartbeat_age(os.path.join(HEARTBEAT_DIR, fname))
        rec = {"last_heartbeat_at": ts, "age_s": None if age is None else round(age, 1), "error": herr}
        if age is None:
            rec["status"] = "warn" if herr else "unknown"
            anomalies.append(f"heartbeat {name}: {herr}")
        elif age > HEARTBEAT_STALE_AFTER:
            rec["status"] = "stale"
            anomalies.append(f"heartbeat {name} stale: age={int(age)}s")
        else:
            rec["status"] = "fresh"
        reading["disk_heartbeats"][name] = rec

    # DB file
    reading["db"] = _check_db()

    # Overall
    if any(r["status"] == "fail" for r in reading["processes"].values()) or any(
        "process" in a and "not running" in a for a in anomalies
    ):
        overall = "fail"
    elif anomalies:
        overall = "warn"
    else:
        overall = "ok"
    reading["overall_status"] = overall
    reading["anomalies"] = anomalies
    return reading


def main() -> int:
    reading = collect()
    print(json.dumps(reading, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
