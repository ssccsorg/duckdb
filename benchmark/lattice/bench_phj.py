#!/usr/bin/env python3
"""A/B: multi-dimension perfect hash join (PHJ) against the regular hash join.

The same dense bounded lattice join runs through two builds:
- the modified build (this branch): the PHJ activates for D equality conditions
- the original build (main): multi-condition joins always use the regular HT

Usage: bench_phj.py [path-to-duckdb-cli] [repeats]
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
REPEATS = int(sys.argv[2]) if len(sys.argv) > 2 else 7
_fd, _path = tempfile.mkstemp(suffix=".duckdb")
os.close(_fd)
os.unlink(_path)
DB_PATH = _path


def run(sql: str, timer: bool = True):
    body = (".timer on\n" if timer else "") + sql
    p = subprocess.run([CLI, DB_PATH], input=body.encode(), capture_output=True)
    if p.returncode != 0:
        raise RuntimeError(f"CLI failed: {p.stderr.decode()[:500]}")
    out = p.stdout.decode()
    times = [float(m) for m in re.findall(r"Run Time \(s\): real ([0-9.]+)", out)]
    return times


def median_ms(fn, repeats=REPEATS):
    times = []
    for _ in range(repeats):
        ts = run(fn + ";")
        times.append(ts[0] * 1000)
    return statistics.median(times)


def main():
    print(f"environment: cli={CLI}, repeats={REPEATS}")
    print()

    # (label, create sql, join sql)
    workloads = [
        ("2D 100^2",
         "DROP TABLE IF EXISTS a; CREATE TABLE a AS SELECT (x // 100)::INTEGER AS k1, (x % 100)::INTEGER AS k2, x AS v FROM range(10000) t(x);",
         "SELECT count(*) FROM a AS x JOIN a AS y USING (k1, k2)"),
        ("2D 300^2",
         "DROP TABLE IF EXISTS a; CREATE TABLE a AS SELECT (x // 300)::INTEGER AS k1, (x % 300)::INTEGER AS k2, x AS v FROM range(90000) t(x);",
         "SELECT count(*) FROM a AS x JOIN a AS y USING (k1, k2)"),
        ("2D 700^2",
         "DROP TABLE IF EXISTS a; CREATE TABLE a AS SELECT (x // 700)::INTEGER AS k1, (x % 700)::INTEGER AS k2, x AS v FROM range(490000) t(x);",
         "SELECT count(*) FROM a AS x JOIN a AS y USING (k1, k2)"),
        ("3D 21^3",
         "DROP TABLE IF EXISTS a; CREATE TABLE a AS SELECT (x // 441)::INTEGER AS k1, (x // 21 % 21)::INTEGER AS k2, (x % 21)::INTEGER AS k3, x AS v FROM range(9261) t(x);",
         "SELECT count(*) FROM a AS x JOIN a AS y USING (k1, k2, k3)"),
        ("3D 40^3",
         "DROP TABLE IF EXISTS a; CREATE TABLE a AS SELECT (x // 1600)::INTEGER AS k1, (x // 40 % 40)::INTEGER AS k2, (x % 40)::INTEGER AS k3, x AS v FROM range(64000) t(x);",
         "SELECT count(*) FROM a AS x JOIN a AS y USING (k1, k2, k3)"),
        ("3D 90^3",
         "DROP TABLE IF EXISTS a; CREATE TABLE a AS SELECT (x // 8100)::INTEGER AS k1, (x // 90 % 90)::INTEGER AS k2, (x % 90)::INTEGER AS k3, x AS v FROM range(729000) t(x);",
         "SELECT count(*) FROM a AS x JOIN a AS y USING (k1, k2, k3)"),
        ("probe 1k x 1M 2D",
         "DROP TABLE IF EXISTS s; DROP TABLE IF EXISTS p; "
         "CREATE TABLE s AS SELECT (x // 100)::INTEGER AS k1, (x % 100)::INTEGER AS k2, x AS v FROM range(1000) t(x); "
         "CREATE TABLE p AS SELECT (x // 100 % 100)::INTEGER AS k1, (x % 100)::INTEGER AS k2, x AS v FROM range(1000000) t(x);",
         "SELECT count(*) FROM p AS x JOIN s AS y USING (k1, k2)"),
        ("probe 1k x 1M 3D",
         "DROP TABLE IF EXISTS s; DROP TABLE IF EXISTS p; "
         "CREATE TABLE s AS SELECT (x // 100)::INTEGER AS k1, (x // 10 % 10)::INTEGER AS k2, (x % 10)::INTEGER AS k3, x AS v FROM range(1000) t(x); "
         "CREATE TABLE p AS SELECT (x // 100 % 10)::INTEGER AS k1, (x // 10 % 10)::INTEGER AS k2, (x % 10)::INTEGER AS k3, x AS v FROM range(1000000) t(x);",
         "SELECT count(*) FROM p AS x JOIN s AS y USING (k1, k2, k3)"),
    ]

    rows = []
    for label, create, join in workloads:
        run(create, timer=False)
        ms = median_ms(join)
        rows.append((label, ms))
        print(f"  {label}: {ms:.3f} ms per join")

    print()
    print(f"{'workload':<20}{'median ms':>10}")
    for label, ms in rows:
        print(f"{label:<20}{ms:>10.3f}")


if __name__ == "__main__":
    main()
