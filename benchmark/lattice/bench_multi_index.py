#!/usr/bin/env python3
"""A/B: the multi-filter index scan (the TableScanInitGlobal FIXME) against the scan.

The FIXME path scans one single-column ART per filtered column and intersects
the row-ID sets. It activates when every filtered column carries a
single-column ART and each individual filter stays below the index-scan
threshold. The bench table is a random-order 2D lattice (E = 10,000, 100M
rows) where the scan cannot prune, so the index path is the only selective
access path; the per-dimension selectivity (0.01 percent) is below the 0.1
percent threshold, so the path activates at the default settings.

The scan side is measured on the same table with the index scan forced off
(index_scan_percentage = 0), so both sides run the identical query.

Usage:
    bench_multi_index.py --build <cli> <db_path>
    bench_multi_index.py --warm  <cli> <db_path>
    bench_multi_index.py --cold  <cli> <db_path> [samples]
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
DB_PATH = sys.argv[3] if len(sys.argv) > 3 else "benchmark/lattice/results/multi_index.duckdb"
SAMPLES = int(sys.argv[4]) if len(sys.argv) > 4 else 3
E = 10_000
N = E * E  # 100M rows
WARM_REPEATS = 7

SETUP = f"""
DROP TABLE IF EXISTS t_random;
CREATE TABLE t_random AS
  SELECT (x // {E})::INTEGER AS d1, (x % {E})::INTEGER AS d2, x AS payload
  FROM range(0, {N}) t(x)
  ORDER BY random();
CREATE INDEX i1 ON t_random(d1);
CREATE INDEX i2 ON t_random(d2);
CHECKPOINT;
"""

QUERIES = [
    ("count", f"SELECT count(*) FROM t_random WHERE d1 = {E // 2} AND d2 = {E // 2}"),
    ("payload", f"SELECT payload FROM t_random WHERE d1 = {E // 2} AND d2 = {E // 2}"),
]
SCAN_SETUP = "SET index_scan_percentage=0;"


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
        # the CLI timer reports every statement; the query is the last one
        times.append(ts[-1] * 1000)
    return statistics.median(times)


def do_build():
    if os.path.exists(DB_PATH) and os.path.getsize(DB_PATH) > 0:
        return
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    run(SETUP, timer=False)
    print(json.dumps({"phase": "build", "rows": N, "extent": E, "db_path": DB_PATH,
                      "file_bytes": os.path.getsize(DB_PATH)}))


def do_warm():
    result = {"phase": "warm", "rows": N, "extent": E, "repeats": WARM_REPEATS}
    for label, query in QUERIES:
        result[f"{label}_scan_ms"] = median_ms(SCAN_SETUP + "\n" + query)
        result[f"{label}_multi_index_ms"] = median_ms(query)
        result[f"{label}_ratio_scan_over_index"] = (
            result[f"{label}_scan_ms"] / result[f"{label}_multi_index_ms"])
    print(json.dumps(result, indent=2))


def do_cold():
    result = {"phase": "cold", "rows": N, "extent": E, "samples": SAMPLES,
              "method": "purge + 24GB memory pressure before each query", "purge_ok": True}
    for label, query in QUERIES:
        scan_vals, index_vals = [], []
        for _ in range(SAMPLES):
            for vals, sql in ((scan_vals, SCAN_SETUP + "\n" + query), (index_vals, query)):
                if not purge():
                    result["purge_ok"] = False
                pressurize()
                ts, _ = run(sql + ";")
                vals.append(ts[-1] * 1000)
        result[f"{label}_scan_ms"] = scan_vals
        result[f"{label}_multi_index_ms"] = index_vals
        result[f"{label}_scan_median_ms"] = statistics.median(scan_vals)
        result[f"{label}_multi_index_median_ms"] = statistics.median(index_vals)
        result[f"{label}_ratio_scan_over_index"] = (
            result[f"{label}_scan_median_ms"] / result[f"{label}_multi_index_median_ms"])
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
