from __future__ import annotations

import os
import threading
import time
from datetime import datetime
from pathlib import Path
from statistics import mean

import requests
from flask import Flask, jsonify, render_template, request

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
    status, latency_ms, detail = run_connectivity_test()
    mode = "manual"
    ts = append_log(mode, status, latency_ms, detail)

    return jsonify(
        {
            "timestamp": ts,
            "mode": mode,
            "status": status,
            "latency_ms": round(latency_ms, 2) if latency_ms is not None else None,
            "detail": detail,
        }
    )


@app.post("/api/test/auto")
def api_test_auto():
    status, latency_ms, detail = run_connectivity_test()
    mode = "auto"
    ts = append_log(mode, status, latency_ms, detail)

    return jsonify(
        {
            "timestamp": ts,
            "mode": mode,
            "status": status,
            "latency_ms": round(latency_ms, 2) if latency_ms is not None else None,
            "detail": detail,
        }
    )


@app.post("/api/auto/start")
def api_auto_start():
    global _auto_thread, _auto_interval_seconds

    payload = request.get_json(silent=True) or {}
    interval_seconds = payload.get("interval_seconds", DEFAULT_AUTO_INTERVAL_SECONDS)
    try:
        interval_seconds = max(MIN_AUTO_INTERVAL_SECONDS, int(interval_seconds))
    except (TypeError, ValueError):
        interval_seconds = DEFAULT_AUTO_INTERVAL_SECONDS

    with _auto_lock:
        _auto_interval_seconds = interval_seconds
        already_running = _auto_thread is not None and _auto_thread.is_alive()
        if already_running:
            return jsonify(
                {
                    "running": True,
                    "interval_seconds": _auto_interval_seconds,
                    "message": "auto test already running",
                }
            )

        _auto_stop_event.clear()
        _auto_thread = threading.Thread(target=_auto_test_loop, daemon=True)
        _auto_thread.start()

    status, latency_ms, detail = run_connectivity_test()
    ts = append_log("auto", status, latency_ms, detail)
    return jsonify(
        {
            "running": True,
            "interval_seconds": _auto_interval_seconds,
            "last_result": {
                "timestamp": ts,
                "mode": "auto",
                "status": status,
                "latency_ms": round(latency_ms, 2) if latency_ms is not None else None,
                "detail": detail,
            },
        }
    )


@app.post("/api/auto/stop")
def api_auto_stop():
    global _auto_thread

    with _auto_lock:
        was_running = _auto_thread is not None and _auto_thread.is_alive()
        _auto_stop_event.set()
        _auto_thread = None

    return jsonify({"running": False, "stopped": was_running})


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

    by_hour: dict[str, dict[str, int]] = {}
    for row in rows:
        hour = row["timestamp"][:13]
        if hour not in by_hour:
            by_hour[hour] = {"success": 0, "failed": 0}
        by_hour[hour][row["status"]] += 1

    return jsonify(
        {
            "summary": {
                "total": total,
                "success": success,
                "failed": failed,
                "success_rate": success_rate,
                "avg_latency_ms": avg_latency,
            },
            "hourly": by_hour,
        }
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
