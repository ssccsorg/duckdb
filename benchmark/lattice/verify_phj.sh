#!/usr/bin/env bash
# Correctness check for the multi-dimension perfect hash join (PHJ) lattice
# generalization. The tables are dense bounded integer lattices, so the
# mixed-radix PHJ path applies; the result must match the analytic expectation
# (count and payload sum of the self join).
#
# Usage: verify_phj.sh [path-to-duckdb-cli]
set -euo pipefail

CLI="${1:-build/debug/duckdb}"

check() {
  local label="$1" sql="$2"
  local out
  out=$("$CLI" -c "$sql" 2>&1)
  if echo "$out" | grep -q "true"; then
    echo "OK: $label"
  else
    echo "FAIL: $label"
    echo "$out"
    exit 1
  fi
}

# 2D lattice [0,100)^2, one row per cell, self join on (d1, d2)
check "2D PHJ (10000 cells)" "
CREATE TABLE a2 AS SELECT (x // 100)::INTEGER AS d1, (x % 100)::INTEGER AS d2, x AS v FROM range(10000) t(x);
CREATE TABLE b2 AS SELECT (x // 100)::INTEGER AS d1, (x % 100)::INTEGER AS d2, x AS v FROM range(10000) t(x);
SELECT count(*) = 10000 AND sum(a2.v) + sum(b2.v) = 99990000 AS equal FROM a2 JOIN b2 USING (d1, d2);
"

# 3D lattice [0,21)^3, one row per cell, self join on (d1, d2, d3)
check "3D PHJ (9261 cells)" "
CREATE TABLE a3 AS SELECT (x // 441)::INTEGER AS d1, (x // 21 % 21)::INTEGER AS d2, (x % 21)::INTEGER AS d3, x AS v FROM range(9261) t(x);
CREATE TABLE b3 AS SELECT (x // 441)::INTEGER AS d1, (x // 21 % 21)::INTEGER AS d2, (x % 21)::INTEGER AS d3, x AS v FROM range(9261) t(x);
SELECT count(*) = 9261 AND sum(a3.v) + sum(b3.v) = 85756860 AS equal FROM a3 JOIN b3 USING (d1, d2, d3);
"

# Non-dense build: a probe key inside the domain but absent from the build must not match
check "sparse build (b=4 absent from probe-side build)" "
CREATE TABLE p (b INTEGER, v INTEGER);
INSERT INTO p VALUES (1, 10), (2, 20), (3, 30), (5, 50);
CREATE TABLE q (b INTEGER, v INTEGER);
INSERT INTO q VALUES (1, 1), (2, 2), (3, 3), (4, 4);
SELECT count(*) = 3 AS equal FROM p JOIN q USING (b);
"

echo "all multi-dimension PHJ checks passed"
