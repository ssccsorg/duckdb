#!/usr/bin/env python3
"""Cold-cache I/O A/B: B0 scan against B2 lattice_scan on a persistent file-backed table.

The warm phase re-measures the scaling-law point for this table (median wall
time per query through the CLI timer). The cold phase purges the OS page
cache before every single query, so each cold sample reads the table from
disk. The purge requires root; the cold phase is meant to run once through
osascript with administrator privileges (a single authorization prompt) and
prints its results on stdout.

Usage:
    bench_cold_io.py --build <cli> <dims> <db_path>
    bench_cold_io.py --warm  <cli> <dims> <db_path>
    bench_cold_io.py --cold  <cli> <dims> <db_path> [samples]
"""
from __future__ import annotations

import json
import os
import re
import statistics
import subprocess
import sys
from pathlib import Path

CLI = sys.argv[2] if len(sys.argv) > 2 else "build/release/duckdb"
D = int(sys.argv[3]) if len(sys.argv) > 3 else 215
DB_PATH = sys.argv[4] if len(sys.argv) > 4 else "benchmark/lattice/results/cold_io.duckdb"
SAMPLES = int(sys.argv[5]) if len(sys.argv) > 5 else 3
N = D * D * D
WARM_REPEATS = 7

SETUP = f"""
CREATE TABLE t_sorted AS
  SELECT d1, d2, d3, x AS payload FROM (
    SELECT (x / {D * D})::INTEGER % {D} AS d1, (x / {D})::INTEGER % {D} AS d2,
           (x % {D})::INTEGER AS d3, x
    FROM range(0, {N}) t(x)
  ) ORDER BY d1, d2, d3;
"""

# (label, scan sql, lattice sql)
QUERIES = [
    ("P3 point",
     "SELECT count(*) FROM t_sorted WHERE d1 = 7 AND d2 = 9 AND d3 = 11",
     f"SELECT count(*) FROM lattice_scan('t_sorted','d1,d2,d3','payload',{D},{D},{D},7,7,9,9,11,11)"),
    ("R3 range",
     "SELECT count(*), sum(payload) FROM t_sorted WHERE d1 BETWEEN 10 AND 20 AND d2 BETWEEN 10 AND 20 AND d3 BETWEEN 10 AND 20",
     f"SELECT count(*), sum(payload) FROM lattice_scan('t_sorted','d1,d2,d3','payload',{D},{D},{D},10,20,10,20,10,20)"),
    ("S1 point",
     "SELECT count(*) FROM t_sorted WHERE d1 = 7",
     f"SELECT count(*) FROM lattice_scan('t_sorted','d1,d2,d3','payload',{D},{D},{D},7,7,NULL,NULL,NULL,NULL)"),
]


def run(sql: str, timer: bool = True):
    body = (".timer on\n" if timer else "") + sql
    p = subprocess.run([CLI, DB_PATH], input=body.encode(), capture_output=True)
    if p.returncode != 0:
        raise RuntimeError(f"CLI failed: {p.stderr.decode()[:500]}")
    out = p.stdout.decode()
    times = [float(m) for m in re.findall(r"Run Time \(s\): real ([0-9.]+)", out)]
    return times, out


def purge():
    try:
        r = subprocess.run(["purge"], capture_output=True, text=True, timeout=30)
        return r.returncode == 0
    except Exception:  # pragma: no cover
        return False


def pressurize(gb=24):
    # Force the kernel to evict file-backed pages: allocate and touch an
    # anonymous buffer near RAM size, then release it. macOS purge alone
    # only drops inactive pages; pages touched by the warm phase are active
    # and survive it.
    size = gb * 1024**3
    buf = bytearray(size)
    try:
        for i in range(0, size, 4096):
            buf[i] = 1
    finally:
        del buf


def median_ms(sql, repeats=WARM_REPEATS):
    times = []
    for _ in range(repeats):
        ts, _ = run(sql + ";")
        times.append(ts[0] * 1000)
    return statistics.median(times)


def do_build():
    if os.path.exists(DB_PATH) and os.path.getsize(DB_PATH) > 0:
        # Reuse the existing database when the table is already present.
        return
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    run(SETUP, timer=False)
    print(json.dumps({"phase": "build", "rows": N, "dims": D, "db_path": DB_PATH,
                      "file_bytes": os.path.getsize(DB_PATH)}))


def do_warm():
    result = {"phase": "warm", "rows": N, "dims": D, "repeats": WARM_REPEATS}
    for label, sql_scan, sql_lattice in QUERIES:
        result[f"{label}/B0_scan_ms"] = median_ms(sql_scan)
        result[f"{label}/B2_lattice_ms"] = median_ms(sql_lattice)
    print(json.dumps(result, indent=2))


def do_cold():
    result = {"phase": "cold", "rows": N, "dims": D, "samples": SAMPLES,
              "method": "purge + 24GB memory pressure before each query",
              "purge_ok": True}
    samples = {}
    for label, sql_scan, sql_lattice in QUERIES:
        for path, sql in (("B0_scan", sql_scan), ("B2_lattice", sql_lattice)):
            vals = []
            for _ in range(SAMPLES):
                if not purge():
                    result["purge_ok"] = False
                pressurize()
                ts, _ = run(sql + ";")
                vals.append(ts[0] * 1000)
            samples[f"{label}/{path}_ms"] = vals
            samples[f"{label}/{path}_median_ms"] = statistics.median(vals)
    result["samples"] = samples
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "--warm"
    if mode == "--build":
        do_build()
    elif mode == "--warm":
        do_warm()
    elif mode == "--cold":
        do_cold()
    else:
        raise SystemExit(f"unknown mode {mode}")
