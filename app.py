from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path
from statistics import mean

import requests
from flask import Flask, jsonify, render_template

app = Flask(__name__)

LOG_FILE = Path("connectivity_log.txt")
TEST_URL = "https://www.google.com/generate_204"
TIMEOUT_SECONDS = 5


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

    by_day: dict[str, dict[str, int]] = {}
    by_mode: dict[str, int] = {"manual": 0, "auto": 0}

    for row in rows:
        day = row["timestamp"].split(" ")[0]
        if day not in by_day:
            by_day[day] = {"success": 0, "failed": 0}
        by_day[day][row["status"]] += 1

        mode = row["mode"] if row["mode"] in by_mode else "manual"
        by_mode[mode] += 1

    last_50 = rows[-50:]

    return jsonify(
        {
            "summary": {
                "total": total,
                "success": success,
                "failed": failed,
                "success_rate": success_rate,
                "avg_latency_ms": avg_latency,
            },
            "mode_count": by_mode,
            "daily": by_day,
            "recent": last_50,
        }
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
