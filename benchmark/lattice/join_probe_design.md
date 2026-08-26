# Multi-dimension perfect hash join: lattice generalization design

Track: ssccsorg/syntagma #55, branch 55-join-probe.

## The existing mechanism

DuckDB's perfect hash join (PHJ) is the single-key dense bounded integer
precedent of the lattice mechanism. `CanUsePerfectHashJoin` in
`physical_hash_join.cpp` accepts exactly one equality condition, and
`PerfectHashJoinExecutor` in `perfect_hash_join_executor.cpp` builds and probes
an O(1) position table:

- Build: `idx = input_value - min_value` (the single-dimension position
  function), a bitmap rejects duplicate keys, and `build_size = max - min + 1`.
- Bounds: `build_range = max - min` at most 2^20, inner join, integer key,
  no residual predicate, no nested RHS types, and `count <= build_range`.
- Probe: compute the LHS key, select values in the min-max domain, resolve the
  position in O(1), and emit the RHS as dictionary vectors over the position
  table.

## The lattice generalization

The mixed-radix position function replaces the linear position function for D
integer equality conditions. With per-condition extents E_i = max_i - min_i + 1
and per-row key values d_i, the packed position is

    n = ((d_1 - min_1) * E_2 + (d_2 - min_2)) * E_3 + (d_3 - min_3)

for three dimensions, generalized to D by the same fold. The packed space is
the product of the extents, replacing the single-dimension build range. The
mapping of the existing mechanism:

| PHJ component | Single-key | D-dimension lattice |
| :--- | :--- | :--- |
| Position function | `key - min` | mixed-radix fold over the D keys |
| Build space | `max - min + 1` | product of the per-condition extents |
| Density condition | `count <= build_range` | `count <= packed space` |
| Build bound | `build_range <= 2^20` | `packed space <= 2^20` |
| Duplicate check | one bitmap over the range | one bitmap over the packed space |

## Modification points

- `src/execution/operator/join/physical_hash_join.cpp`: extend
  `CanUsePerfectHashJoin` from one condition to D conditions, all integer
  equality, each with min/max statistics; compute the packed space and apply
  the 2^20 bound.
- `src/execution/operator/join/perfect_hash_join_executor.{hpp,cpp}`: replace
  the single-key scan/build/probe with the D-key mixed-radix fold; the
  position table size becomes the packed space; the probe computes the same
  fold from the D LHS columns.

## Constraints and risks

- The packed space memory bound is the same constraint as the lattice table
  metadata: the product of the extents must stay bounded, otherwise the PHJ
  falls back to the regular hash table.
- The density condition requires near-full occupancy; sparse multi-dimension
  keys fall back.
- The current PHJ guarantees (inner join, no residual predicate, no nested
  RHS types) carry over.
- The probe and build are per-key scalar folds; the vectorized path keeps the
  same type-dispatch structure as the existing templates.

## Verification plan

- Correctness: run the existing hash join test suite plus lattice-style joins
  (equi-join on two or three bounded integer columns) and compare the result
  against the regular hash join.
- A/B: the same joins through the regular HT and the D-dimension PHJ, on dense
  bounded keys, measuring the probe cost and the build memory.
