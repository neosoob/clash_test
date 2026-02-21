from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from statistics import mean

import requests
from flask import Flask, jsonify, render_template

app = Flask(__name__)

LOG_FILE = Path("connectivity_log.txt")
TEST_URL = "https://www.google.com/generate_204"
TIMEOUT_SECONDS = 5
DEFAULT_AUTO_INTERVAL_SECONDS = 30
MIN_AUTO_INTERVAL_SECONDS = 1

_auto_lock = threading.Lock()
_auto_stop_event = threading.Event()
_auto_thread: threading.Thread | None = None
_auto_interval_seconds = DEFAULT_AUTO_INTERVAL_SECONDS


def operation_disabled_response():
    return jsonify({"error": "operation disabled in production mode"}), 403


def run_connectivity_test() -> tuple[str, float | None, str]:
    start = time.perf_counter()
    try:
        response = requests.get(TEST_URL, timeout=TIMEOUT_SECONDS)
        latency_ms = (time.perf_counter() - start) * 1000
        if response.status_code in (200, 204):
            return "success", latency_ms, f"HTTP {response.status_code}"
        return "failed", latency_ms, f"HTTP {response.status_code}"
    except requests.RequestException as exc:
        return "failed", None, str(exc)


def _auto_test_loop() -> None:
    while not _auto_stop_event.wait(_auto_interval_seconds):
        status, latency_ms, detail = run_connectivity_test()
        append_log("auto", status, latency_ms, detail)


def start_auto_testing(interval_seconds: int, run_initial_test: bool = True) -> bool:
    global _auto_thread, _auto_interval_seconds

    interval_seconds = max(MIN_AUTO_INTERVAL_SECONDS, int(interval_seconds))
    with _auto_lock:
        _auto_interval_seconds = interval_seconds
        already_running = _auto_thread is not None and _auto_thread.is_alive()
        if already_running:
            return False

        _auto_stop_event.clear()
        _auto_thread = threading.Thread(target=_auto_test_loop, daemon=True)
        _auto_thread.start()

    if run_initial_test:
        status, latency_ms, detail = run_connectivity_test()
        append_log("auto", status, latency_ms, detail)

    return True


def append_log(mode: str, status: str, latency_ms: float | None, detail: str) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    latency_str = f"{latency_ms:.2f}" if latency_ms is not None else ""
    safe_detail = detail.replace("\t", " ").replace("\n", " ")
    line = f"{now}\t{mode}\t{status}\t{latency_str}\t{safe_detail}\n"

    if not LOG_FILE.exists():
        header = "timestamp\tmode\tstatus\tlatency_ms\tdetail\n"
        LOG_FILE.write_text(header, encoding="utf-8")

    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(line)

    return now


def parse_logs() -> list[dict]:
    if not LOG_FILE.exists():
        return []

    rows: list[dict] = []
    with LOG_FILE.open("r", encoding="utf-8") as f:
        next(f, None)
        for raw in f:
            parts = raw.rstrip("\n").split("\t")
            if len(parts) < 5:
                continue
            ts, mode, status, latency, detail = parts[:5]
            latency_val = None
            if latency:
                try:
                    latency_val = float(latency)
                except ValueError:
                    latency_val = None
            rows.append(
                {
                    "timestamp": ts,
                    "mode": mode,
                    "status": status,
                    "latency_ms": latency_val,
                    "detail": detail,
                }
            )
    return rows


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/api/test")
def api_test():
    return operation_disabled_response()


@app.post("/api/test/auto")
def api_test_auto():
    return operation_disabled_response()


@app.post("/api/auto/start")
def api_auto_start():
    return operation_disabled_response()


@app.post("/api/auto/stop")
def api_auto_stop():
    return operation_disabled_response()


@app.get("/api/auto/status")
def api_auto_status():
    running = _auto_thread is not None and _auto_thread.is_alive()
    return jsonify({"running": running, "interval_seconds": _auto_interval_seconds})


@app.get("/stats")
def stats_page():
    return render_template("stats.html")


@app.get("/api/stats")
def api_stats():
    rows = parse_logs()

    total = len(rows)
    success = sum(1 for r in rows if r["status"] == "success")
    failed = total - success
    success_rate = round((success / total) * 100, 2) if total else 0.0

    latency_values = [r["latency_ms"] for r in rows if isinstance(r["latency_ms"], float)]
    avg_latency = round(mean(latency_values), 2) if latency_values else None
    last_test_status = rows[-1]["status"] if rows else None
    last_test_time = rows[-1]["timestamp"].split(" ")[1] if rows else "-"
    failed_rows = [r for r in rows if r["status"] == "failed"]
    last_failed_time = failed_rows[-1]["timestamp"].split(" ")[1] if failed_rows else "-"
    sustained_connectivity_minutes: float | None = None
    if rows and rows[-1]["status"] == "success" and failed_rows:
        try:
            last_test_dt = datetime.strptime(rows[-1]["timestamp"], "%Y-%m-%d %H:%M:%S")
            last_failed_dt = datetime.strptime(failed_rows[-1]["timestamp"], "%Y-%m-%d %H:%M:%S")
            sustained_connectivity_minutes = round(
                max(0.0, (last_test_dt - last_failed_dt).total_seconds() / 60), 1
            )
        except ValueError:
            sustained_connectivity_minutes = None
    consecutive_failed_minutes = 0.0
    if rows and rows[-1]["status"] == "failed":
        streak_start: datetime | None = None
        streak_end: datetime | None = None
        for row in reversed(rows):
            if row["status"] != "failed":
                break
            try:
                row_ts = datetime.strptime(row["timestamp"], "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
            streak_start = row_ts
            if streak_end is None:
                streak_end = row_ts
        if streak_start is not None and streak_end is not None:
            duration_minutes = (streak_end - streak_start).total_seconds() / 60
            consecutive_failed_minutes = round(max(0.0, duration_minutes), 1)

    by_hour: dict[str, dict[str, int]] = {}
    for row in rows:
        hour = row["timestamp"][:13]
        if hour not in by_hour:
            by_hour[hour] = {"success": 0, "failed": 0}
        by_hour[hour][row["status"]] += 1

    current_hour = datetime.now().replace(minute=0, second=0, microsecond=0)
    window_start_24 = current_hour - timedelta(hours=23)
    window_start_48 = current_hour - timedelta(hours=47)
    by_hour_24: dict[str, dict[str, int]] = {}
    by_hour_48: dict[str, dict[str, int]] = {}
    for i in range(24):
        hour_key = (window_start_24 + timedelta(hours=i)).strftime("%Y-%m-%d %H")
        by_hour_24[hour_key] = {"success": 0, "failed": 0}
    for i in range(48):
        hour_key = (window_start_48 + timedelta(hours=i)).strftime("%Y-%m-%d %H")
        by_hour_48[hour_key] = {"success": 0, "failed": 0}

    for row in rows:
        try:
            ts = datetime.strptime(row["timestamp"], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        ts_hour = ts.replace(minute=0, second=0, microsecond=0)
        if ts_hour > current_hour:
            continue
        hour_key = ts_hour.strftime("%Y-%m-%d %H")
        if ts_hour >= window_start_24 and hour_key in by_hour_24:
            by_hour_24[hour_key][row["status"]] += 1
        if ts_hour >= window_start_48 and hour_key in by_hour_48:
            by_hour_48[hour_key][row["status"]] += 1

    hourly_24: list[dict] = []
    for hour_key, counts in by_hour_24.items():
        hour_total = counts["success"] + counts["failed"]
        rate = round((counts["success"] / hour_total) * 100, 2) if hour_total else 0.0
        hourly_24.append(
            {
                "hour": hour_key,
                "failed": counts["failed"],
                "connectivity_rate": rate,
            }
        )

    hourly_48: list[dict] = []
    for hour_key, counts in by_hour_48.items():
        hour_total = counts["success"] + counts["failed"]
        rate = round((counts["success"] / hour_total) * 100, 2) if hour_total else 0.0
        hourly_48.append(
            {
                "hour": hour_key,
                "failed": counts["failed"],
                "connectivity_rate": rate,
            }
        )

    return jsonify(
        {
            "summary": {
                "total": total,
                "success": success,
                "failed": failed,
                "success_rate": success_rate,
                "avg_latency_ms": avg_latency,
                "last_test_status": last_test_status,
                "last_test_time": last_test_time,
                "last_failed_time": last_failed_time,
                "sustained_connectivity_minutes": sustained_connectivity_minutes,
                "consecutive_failed_minutes": consecutive_failed_minutes,
            },
            "hourly": by_hour,
            "hourly_24": hourly_24,
            "hourly_48": hourly_48,
        }
    )


@app.get("/api/log")
def api_log():
    if not LOG_FILE.exists():
        return jsonify({"content": ""})
    return jsonify({"content": LOG_FILE.read_text(encoding="utf-8")})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 50003))
    debug = True
    if not debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        start_auto_testing(DEFAULT_AUTO_INTERVAL_SECONDS, run_initial_test=True)
    app.run(host="0.0.0.0", port=port, debug=debug)
