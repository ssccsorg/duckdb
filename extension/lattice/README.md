# DuckDB lattice extension

The coordinate lattice access path for multi-dimensional point and range
queries over bounded integer dimensions in the DuckDB fork.

Track: ssccsorg/syntagma #54, branch 54-duckdb-lattice. The pre-integration
benchmark and the physical layout decision live under benchmark/lattice.

## Status

The addressing is implemented and verified. The extension registers the
`lattice_scan` table function:

    SELECT * FROM lattice_scan('table', 'd1,d2,d3', 'payload', E1, E2, E3,
                               lo1, hi1, lo2, hi2, lo3, hi3)

The table is addressed as a regular lattice: bounded integer dimensions, one
row per cell, stored in lattice order (the physical layout decision in
benchmark/lattice/layout_decision.md). A cell (d1, d2, d3) with extents
(E1, E2, E3) maps to the row offset `((d1 * E2 + d2) * E3 + d3)` in closed
form. Each lo/hi pair is a per-dimension range; NULL means the full range and
lo == hi is a point. The scan enumerates the matching subspace with a
mixed-radix odometer and fetches the payload rows at the addressed offsets.

Correctness is verified by benchmark/lattice/verify_lattice.sh against the
regular scan on the same table: point, range box, full lattice, and
partial-dimension queries return identical counts and payload sums.

## Boundaries

- The table must be stored in lattice order. The conversion is a sort:
  `CREATE TABLE t_sorted AS SELECT * FROM t ORDER BY d1, d2, d3`.
- The lattice is assumed full (one row per cell); sparse lattices are a
  follow-up scope item.
- The dimension columns are output as BIGINT coordinates; the payload keeps
  its stored type.
- The scan is single-threaded for the prototype.

## Build (local)

The extension builds in-tree through the local extension config, which is
gitignored:

    echo "duckdb_extension_load(lattice)" > extension/extension_config_local.cmake
    make debug
    benchmark/lattice/verify_lattice.sh build/debug/duckdb

The static build registers the function in the debug binary.
