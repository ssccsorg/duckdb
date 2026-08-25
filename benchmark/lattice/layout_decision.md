# Lattice physical layout decision

Track: ssccsorg/syntagma #54, branch 54-duckdb-lattice. Phase 2, task 1.

## Decision

Lattice-sorted storage (option a). The table is converted once into lattice
order, a cell maps to a row offset in closed form, and the dimension columns
become positional. This replaces the full-scan cost measured in the profile
gate.

## Inputs from the profile gate

| Measurement | Value | Role in the decision |
| :--- | :--- | :--- |
| P3 point query, 9.9M-row table | 1.35 ms, 9,938,375 rows scanned, 1 matched | The cost the layout removes |
| Query volume, 100k P3 points | 136.5 s (1.37 ms per query) | The gain materializes at volume |
| ART index, forced | 25.3 ms for 46,225 matches | Index per-row fetch loses to the scan |
| ART index, default settings | never activated (0.465% > 0.1% threshold) | No index alternative exists on this table |
| Table load (random order) | 0.80 s | Reference for the one-time conversion cost |
| File size | 92 MB | The cell-to-row map alternative would add about 80 MB |

## Why lattice-sorted storage over a cell-to-row map

The cell-to-row map alternative (option b) keeps the existing append layout
and indexes each cell. On the benchmark table, 9,938,375 cells need a map of
about 80 MB, comparable to the 92 MB file itself, and the map must be
maintained on every append. Lattice-sorted storage makes the dimension values
derived from the row position: no dimension storage, no per-row evaluation,
and the long-term virtual lattice columns follow from the same layout. The
one-time conversion (a sort of the dimension columns, bounded by the load-time
reference) amortizes against the measured volume workload, where 100k point
queries drop from 136.5 s to the per-query floor.

## Consequences

- The extension table function binds the table, its dimension columns, and
  their extents, and resolves a query box to the matching row offsets in
  closed form.
- The payload columns keep their compressed columnar storage; only the
  dimension columns become positional.
- Writes require conversion or append in lattice order; the append path is a
  follow-up scope item.
- Variable-length payloads and updates are follow-up scope items.

## Open items

- Conversion tool: sort the table into lattice order and rewrite the dimension
  columns as positional metadata.
- Extent metadata: per-dimension extents and the cell-to-offset stride table
  persisted with the table.
- Range enumeration: the mixed-radix box iterator over the per-dimension
  extents (syntagma core upgrade scope).
