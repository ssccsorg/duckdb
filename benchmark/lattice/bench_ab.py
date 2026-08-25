#!/usr/bin/env python3
"""Phase 2 A/B: B0 scan vs B1 ART vs B2 lattice_scan on the same query matrix.

Uses a fork build binary (debug or release), which links the lattice extension
statically. The table is a regular lattice (D cubed rows, one row per cell),
stored in lattice order, payload = the cell linear index.

Measurements:
- matrix: median wall time per (path, query) over repeats, via the CLI timer
- volume: total wall time for point queries per path, one statement each,
  batched per CLI session

Usage: bench_ab.py [path-to-duckdb-cli] [dims] [volume]
"""
from __future__ import annotations

import os
import re
import statistics
import subprocess
import sys
import tempfile
import time

CLI = sys.argv[1] if len(sys.argv) > 1 else "build/release/duckdb"
D = int(sys.argv[2]) if len(sys.argv) > 2 else 215
VOLUME = int(sys.argv[3]) if len(sys.argv) > 3 else 20_000
_fd, _path = tempfile.mkstemp(suffix=".duckdb")
os.close(_fd)
os.unlink(_path)
DB_PATH = _path
N = D * D * D
REPEATS = 7
VOLUME_BATCH = 1_000

SETUP = f"""
CREATE TABLE t_sorted AS
  SELECT d1, d2, d3, x AS payload FROM (
    SELECT (x / {D * D})::INTEGER % {D} AS d1, (x / {D})::INTEGER % {D} AS d2,
           (x % {D})::INTEGER AS d3, x
    FROM range(0, {N}) t(x)
  ) ORDER BY d1, d2, d3;
"""

MATRIX = [
    ("P3 point", "SELECT count(*) FROM t_sorted WHERE d1 = 7 AND d2 = 9 AND d3 = 11",
     f"SELECT count(*) FROM lattice_scan('t_sorted','d1,d2,d3','payload',{D},{D},{D},7,7,9,9,11,11)"),
    ("R3 range", "SELECT count(*), sum(payload) FROM t_sorted WHERE d1 BETWEEN 10 AND 20 AND d2 BETWEEN 10 AND 20 AND d3 BETWEEN 10 AND 20",
     f"SELECT count(*), sum(payload) FROM lattice_scan('t_sorted','d1,d2,d3','payload',{D},{D},{D},10,20,10,20,10,20)"),
    ("S1 point", "SELECT count(*) FROM t_sorted WHERE d1 = 7",
     f"SELECT count(*) FROM lattice_scan('t_sorted','d1,d2,d3','payload',{D},{D},{D},7,7,NULL,NULL,NULL,NULL)"),
]


def run(sql: str, timer: bool = True, devnull: bool = False):
    body = (".timer on\n" if timer else "") + sql
    t0 = time.perf_counter()
    p = subprocess.run([CLI, DB_PATH], input=body.encode(), stdout=subprocess.DEVNULL if devnull else None,
                       capture_output=devnull is False)
    wall = time.perf_counter() - t0
    if p.returncode != 0:
        raise RuntimeError(f"CLI failed: {p.stderr.decode()[:500]}")
    if devnull:
        return [], wall, ""
    out = p.stdout.decode()
    times = [float(m) for m in re.findall(r"Run Time \(s\): real ([0-9.]+)", out)]
    return times, wall, out


def median_ms(fn, repeats=REPEATS):
    times = []
    for _ in range(repeats):
        ts, _, _ = run(fn + ";")
        times.append(ts[0] * 1000)
    return statistics.median(times)


def main():
    run(SETUP, timer=False)
    print(f"environment: rows={N}, dims={D}, cli={CLI}")

    rows = []

    for label, sql_scan, sql_lattice in MATRIX:
        b0 = median_ms(sql_scan)
        b2 = median_ms(sql_lattice)
        rows.append((label, "B0 scan", b0))
        rows.append((label, "B2 lattice", b2))

    # B1: single-dimension ART, forced above the default threshold
    b1_setup = "SET index_scan_percentage=1.0;"
    b1 = median_ms(b1_setup + "\n" + "SELECT count(*) FROM t_sorted WHERE d1 = 7;")
    rows.append(("S1 point", "B1 ART (forced)", b1))

    print()
    print("matrix (median wall ms over %d runs):" % REPEATS)
    print(f"{'query':<12}{'path':<16}{'median ms':>10}")
    for label, path, ms in rows:
        print(f"{label:<12}{path:<16}{ms:>10.3f}")

    # Volume: point queries per path, one statement each, output discarded
    def volume_sql(fmt):
        lines = []
        for i in range(VOLUME):
            x = (i * 7) % D
            y = (i * 11) % D
            z = (i * 13) % D
            lines.append(fmt(x, y, z))
        return ";\n".join(lines) + ";\n"

    vol_scan = volume_sql(lambda x, y, z: f"SELECT count(*) FROM t_sorted WHERE d1 = {x} AND d2 = {y} AND d3 = {z}")
    vol_lattice = volume_sql(
        lambda x, y, z: f"SELECT count(*) FROM lattice_scan('t_sorted','d1,d2,d3','payload',{D},{D},{D},{x},{x},{y},{y},{z},{z})")

    print()
    print("volume workload (%d point queries, one statement each, %d per CLI session):" % (VOLUME, VOLUME_BATCH))
    for label, sql in (("B0 scan", vol_scan), ("B2 lattice", vol_lattice)):
        total = 0.0
        for start in range(0, VOLUME, VOLUME_BATCH):
            chunk = ";\n".join(sql.split(";\n")[start:start + VOLUME_BATCH]) + ";\n"
            _, wall, _ = run(chunk, timer=False, devnull=True)
            total += wall
        print(f"  {label}: {total:.2f} s total, {total * 1000 / VOLUME:.3f} ms per query")


if __name__ == "__main__":
    main()
