# DuckDB coordinate lattice track: pre-integration benchmark

Track issue: ssccsorg/syntagma #54. Development plan: ssccs docs/works/duckdb/lattice.

## Environment

- DuckDB pip package v1.5.5 (no C++ build)
- macOS, 10 threads, in-memory table
- Table lattice_bench(d1, d2, d3, payload): 215 cubed = 9,938,375 rows, one row per cell, dimensions uniform in [0, 215), random row order
- Load time 0.80 s

## Benchmark point

P3, the three-dimension point query `d1 = x AND d2 = y AND d3 = z`:

- rows scanned 9,938,375, rows matched 1, waste ratio 9.94 million to 1
- median 1.35 ms (count), 1.43 ms (payload)
- the full scan is the entire query cost; the lattice removes it with O(1) addressing plus one row fetch

## Measured findings

- Every multi-filter query (P2, P3, R2, R3) scans all 9,938,375 rows even when indexes exist on all three dimensions; the multi-filter FIXME in TableScanInitGlobal is confirmed at scale.
- The ART index never activates with the default settings on this table: the minimum single-dimension selectivity (0.465 percent for d1 = x) exceeds the index_scan_percentage threshold (0.1 percent).
- With index_scan_percentage forced to 1.0, the ART path for d1 = x scans 46,225 rows (the matches) but takes 25.3 ms against 1.27 ms for the full scan: the per-row-id fetch of the ART path loses to the vectorized scan on the in-memory table.
- Scan cost is flat across selectivities for count queries (1.3 to 4.5 ms); the payload projection adds the fetch cost of the matched rows (up to 132 ms at 10 percent selectivity).

## Query volume and file-backed measurements

- Volume: 100,000 three-dimension point queries in a loop take 136.5 s (1.37 ms per query); parameterized executions take 156.8 s (1.57 ms per query). The per-query cost is the 9.9M-row scan; per-query parse overhead is not the dominant term. The lattice removes the scan, so the same volume drops to the per-query floor (addressing plus one-row fetch).
- File-backed (92 MB file, OS-cached): P3 count 1.77 ms, payload 1.79 ms, close to the in-memory numbers; the OS page cache serves the file at memory speed.
- Cold-cache measurement requires root for purge on macOS (Operation not permitted); recorded as pending with sudo.

## Files

- profile_gate.py: reproducible benchmark, run with the installed duckdb package
- profile_gate_io.py: query-volume and file-backed supplement
- results/pre_integration.json: full B0 (no index) and B1 (indexes) matrix
- results/pre_integration_index.json: forced-index supplement
- results/pre_integration_io.json: volume and file-backed measurements

## Run

    python profile_gate.py --index
    python profile_gate_io.py

The outputs overwrite the corresponding results files.
