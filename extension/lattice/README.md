# DuckDB lattice extension

The coordinate lattice access path for multi-dimensional point and range
queries over bounded integer dimensions in the DuckDB fork.

Track: ssccsorg/syntagma #54, branch 54-duckdb-lattice. The pre-integration
benchmark and the physical layout decision live under benchmark/lattice.

## Status

Scaffold. The extension registers the `lattice_scan` table function:

    SELECT * FROM lattice_scan('table', 'd1,d2,d3', 'payload')

The bind validates the arguments and the scan currently emits no rows. The
lattice addressing lands next: the bind resolves the table schema, dimension
extents, and the query box, and the scan emits the matched rows in closed
form over the lattice-sorted layout (benchmark/lattice/layout_decision.md).

## Build (local)

The extension builds in-tree through the local extension config, which is
gitignored:

    echo "duckdb_extension_load(lattice)" > extension/extension_config_local.cmake
    make debug

The static build registers the function in the debug binary.
