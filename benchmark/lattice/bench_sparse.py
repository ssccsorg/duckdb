#!/usr/bin/env python3
"""Sparse-lattice boundary A/B: the density requirement and the cell-to-row map.

The lattice closed form maps a cell to a row offset only when the table is
dense (every cell present). Real tables are sparse. This script measures the
boundary:

- storage: the dense cell-to-row map size against the data, at the 215 cell
  space (the plan records about 80 MB per 9.9M cells), and the sparse table
  plus its cell index at the 600M cell space at 10 percent density
- query: the sparse point query through the cell index (the practical map
  stand-in) against the scan on the sparse table, warm median through the
  CLI timer

Usage:
    bench_sparse.py <cli> <db_path> build-215
    bench_sparse.py <cli> <db_path> index-215
    bench_sparse.py <cli> <db_path> query-215
    bench_sparse.py <cli> <db_path> build-600
    bench_sparse.py <cli> <db_path> scan-600
    bench_sparse.py <cli> <db_path> index-600
    bench_sparse.py <cli> <db_path> query-600
"""
from __future__ import annotations

import json
import re
import statistics
import subprocess
import sys

CLI = sys.argv[1] if len(sys.argv) > 1 else "build/release/duckdb"
DB_PATH = sys.argv[2] if len(sys.argv) > 2 else "benchmark/lattice/results/sparse_io.duckdb"
REPEATS = 7

SPARSE_215 = f"""
DROP TABLE IF EXISTS sparse_215;
CREATE TABLE sparse_215 AS
  SELECT cell, payload, row_number() OVER (ORDER BY cell) - 1 AS pos FROM (
    SELECT ((d1 * 215 + d2) * 215 + d3)::BIGINT AS cell, x AS payload FROM (
      SELECT (x / 46225)::INTEGER % 215 AS d1, (x / 215)::INTEGER % 215 AS d2,
             (x % 215)::INTEGER AS d3, x
      FROM range(0, 9938375) t(x)
    ) WHERE ((d1 * 215 + d2) * 215 + d3) % 10 = 0
  );
DROP TABLE IF EXISTS cell_map_dense;
CREATE TABLE cell_map_dense AS
  WITH cells AS (SELECT * FROM range(0, 9938375) t(cell)),
       present AS (SELECT cell, pos FROM sparse_215)
  SELECT c.cell, p.pos FROM cells c LEFT JOIN present p USING (cell) ORDER BY c.cell;
CHECKPOINT;
"""

SPARSE_600 = f"""
DROP TABLE IF EXISTS sparse_600;
CREATE TABLE sparse_600 AS
  SELECT ((d1 * 843 + d2) * 843 + d3)::BIGINT AS cell, x AS payload FROM (
    SELECT (x / 710649)::INTEGER % 843 AS d1, (x / 843)::INTEGER % 843 AS d2,
           (x % 843)::INTEGER AS d3, x
    FROM range(0, 599077107) t(x)
  ) WHERE ((d1 * 843 + d2) * 843 + d3) % 10 = 0
  ORDER BY cell;
"""


def run(sql: str, timer: bool = False):
    body = (".timer on\n" if timer else "") + sql
    p = subprocess.run([CLI, DB_PATH], input=body.encode(), capture_output=True)
    if p.returncode != 0:
        raise RuntimeError(f"CLI failed: {p.stderr.decode()[:500]}")
    out = p.stdout.decode()
    times = [float(m) for m in re.findall(r"Run Time \(s\): real ([0-9.]+)", out)]
    return times, out


def median_ms(sql, repeats=REPEATS):
    times = []
    for _ in range(repeats):
        ts, _ = run(sql + ";", timer=True)
        times.append(ts[0] * 1000)
    return statistics.median(times)


def size():
    import os
    return os.path.getsize(DB_PATH)


def main():
    mode = sys.argv[3] if len(sys.argv) > 3 else "query-215"
    if mode == "build-215":
        run(SPARSE_215)
        print(json.dumps({"mode": mode, "bytes_no_map_index": size(),
                          "note": "sparse_215 plus cell_map_dense, the ART index on the map not yet created"}))
    elif mode == "index-215":
        run("CREATE INDEX idx_map ON cell_map_dense(cell); CHECKPOINT;")
        print(json.dumps({"mode": mode, "bytes_with_map_index": size()}))
    elif mode == "query-215":
        scan = median_ms("SELECT payload FROM sparse_215 WHERE cell = 0")
        mapped = median_ms("SELECT s.payload FROM sparse_215 s JOIN cell_map_dense m ON m.pos = s.pos WHERE m.cell = 0")
        print(json.dumps({"mode": mode,
                          "scan_ms": scan,
                          "dense_map_path_ms": mapped}))
    elif mode == "build-600":
        run(SPARSE_600)
        print(json.dumps({"mode": mode, "sparse_600_bytes": size()}))
    elif mode == "scan-600":
        scan = median_ms("SELECT payload FROM sparse_600 WHERE cell = 0")
        print(json.dumps({"mode": mode, "scan_ms": scan,
                          "note": "no index on sparse_600 yet"}))
    elif mode == "index-600":
        run("DROP INDEX IF EXISTS idx_cell; CREATE INDEX idx_cell ON sparse_600(cell); CHECKPOINT;")
        print(json.dumps({"mode": mode, "sparse_600_with_index_bytes": size()}))
    elif mode == "query-600":
        indexed = median_ms("SELECT payload FROM sparse_600 WHERE cell = 0")
        print(json.dumps({"mode": mode, "index_ms": indexed,
                          "note": "ART index on cell, single-column equality"}))
    else:
        raise SystemExit(f"unknown mode {mode}")


if __name__ == "__main__":
    main()
