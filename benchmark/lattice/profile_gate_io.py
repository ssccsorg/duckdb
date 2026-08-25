#!/usr/bin/env python3
"""Profile gate supplement: file-backed and query-volume measurements.

Phase 1 continuation (ssccsorg/syntagma #54). Uses the installed duckdb
package (no C++ build).

Measurements:
- F1: file-backed warm point queries (P2, P3) against the same lattice table
- F2: cold-cache point queries, when the OS file cache can be purged
- V1: a volume of raw point queries in a loop
- V2: the same volume with parameterized (prepared) executions
"""
from __future__ import annotations

import json
import random
import statistics
import subprocess
import tempfile
import time
from pathlib import Path

import duckdb

D = 215
N = D * D * D
SEED = 20260824
VOLUME = 100_000
RUNS = 7
HERE = Path(__file__).parent
RESULTS = HERE / "results" / "pre_integration_io.json"


def build(con):
    con.execute("DROP TABLE IF EXISTS lattice_bench")
    con.execute(
        f"""
        CREATE TABLE lattice_bench AS
        SELECT (x / {D * D})::INTEGER % {D} AS d1,
               (x / {D})::INTEGER % {D} AS d2,
               (x % {D})::INTEGER AS d3,
               random() AS payload
        FROM range(0, {N}) t(x)
        ORDER BY random()
        """
    )


def purge_cache():
    try:
        r = subprocess.run(["purge"], capture_output=True, text=True, timeout=15)
        return r.returncode == 0, r.stderr.strip()
    except Exception as exc:  # pragma: no cover
        return False, str(exc)


def median_ms(fn, runs=RUNS):
    fn()  # warm run
    times = []
    for _ in range(runs):
        t0 = time.perf_counter()
        fn()
        times.append((time.perf_counter() - t0) * 1000)
    return statistics.median(times)


def main():
    con = duckdb.connect()
    version = con.execute("SELECT version()").fetchone()[0]
    rng = random.Random(SEED)

    # V1 and V2: in-memory query volume
    build(con)
    preds = [
        (rng.randrange(D), rng.randrange(D), rng.randrange(D)) for _ in range(VOLUME)
    ]
    raw_sql = "SELECT count(*) FROM lattice_bench WHERE d1 = {a} AND d2 = {b} AND d3 = {c}"

    t0 = time.perf_counter()
    for a, b, c in preds:
        con.execute(raw_sql.format(a=a, b=b, c=c)).fetchall()
    v1_s = time.perf_counter() - t0

    t0 = time.perf_counter()
    for a, b, c in preds:
        con.execute("SELECT count(*) FROM lattice_bench WHERE d1 = ? AND d2 = ? AND d3 = ?", [a, b, c]).fetchall()
    v2_s = time.perf_counter() - t0

    results = {
        "phase": "profile_gate_io",
        "version": version,
        "dims": D,
        "rows": N,
        "volume": VOLUME,
        "volume_raw_s": v1_s,
        "volume_raw_ms_per_query": v1_s * 1000 / VOLUME,
        "volume_prepared_s": v2_s,
        "volume_prepared_ms_per_query": v2_s * 1000 / VOLUME,
    }

    # F1 and F2: file-backed
    tmpdir = Path(tempfile.mkdtemp(prefix="lattice_bench_"))
    db_path = tmpdir / "lattice_bench.duckdb"
    fcon = duckdb.connect(str(db_path))
    fcon.execute("PRAGMA disable_checkpoint_on_shutdown")
    build(fcon)
    fcon.execute("CHECKPOINT")
    fcon.close()
    file_size = db_path.stat().st_size

    def open_and_measure():
        c = duckdb.connect(str(db_path), read_only=True)
        try:
            p2c = median_ms(lambda: c.execute(
                "SELECT count(*) FROM lattice_bench WHERE d1 = 7 AND d2 = 9").fetchall(), runs=3)
            p2p = median_ms(lambda: c.execute(
                "SELECT payload FROM lattice_bench WHERE d1 = 7 AND d2 = 9").fetchall(), runs=3)
            p3c = median_ms(lambda: c.execute(
                "SELECT count(*) FROM lattice_bench WHERE d1 = 7 AND d2 = 9 AND d3 = 11").fetchall(), runs=3)
            p3p = median_ms(lambda: c.execute(
                "SELECT payload FROM lattice_bench WHERE d1 = 7 AND d2 = 9 AND d3 = 11").fetchall(), runs=3)
            return {"P2/count_ms": p2c, "P2/payload_ms": p2p, "P3/count_ms": p3c, "P3/payload_ms": p3p}
        finally:
            c.close()

    results["file_bytes"] = file_size
    results["file_warm"] = open_and_measure()

    purge_ok, purge_err = purge_cache()
    results["purge_ok"] = purge_ok
    results["purge_err"] = purge_err
    if purge_ok:
        results["file_cold"] = open_and_measure()
    else:
        results["file_cold"] = None

    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text(json.dumps(results, indent=2))
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
