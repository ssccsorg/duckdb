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

    # (label, dims, extent per dim, row count)
    workloads = [
        ("2D 100^2", 2, 100),
        ("2D 300^2", 2, 300),
        ("2D 700^2", 2, 700),
        ("3D 21^3", 3, 21),
        ("3D 40^3", 3, 40),
        ("3D 90^3", 3, 90),
    ]

    rows = []
    for label, dims, e in workloads:
        n = e ** dims
        if dims == 2:
            create = (f"DROP TABLE IF EXISTS a; CREATE TABLE a AS SELECT (x // {e})::INTEGER AS k1, (x % {e})::INTEGER AS k2, x AS v "
                      f"FROM range({n}) t(x);")
            join = f"SELECT count(*) FROM a AS x JOIN a AS y USING (k1, k2)"
        else:
            create = (f"DROP TABLE IF EXISTS a; CREATE TABLE a AS SELECT (x // {e * e})::INTEGER AS k1, (x // {e} % {e})::INTEGER AS k2, "
                      f"(x % {e})::INTEGER AS k3, x AS v FROM range({n}) t(x);")
            join = f"SELECT count(*) FROM a AS x JOIN a AS y USING (k1, k2, k3)"
        run(create, timer=False)
        ms = median_ms(join)
        rows.append((label, n, ms))
        print(f"  {label}: {n} rows, {ms:.3f} ms per join")

    print()
    print(f"{'workload':<12}{'rows':>9}{'median ms':>10}")
    for label, n, ms in rows:
        print(f"{label:<12}{n:>9}{ms:>10.3f}")


if __name__ == "__main__":
    main()
