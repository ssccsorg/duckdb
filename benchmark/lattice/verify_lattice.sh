#!/usr/bin/env bash
# Correctness check: lattice_scan against the regular scan on the same table.
# The lattice table is built with payload = the cell linear index x, so the
# count and payload sum are exact identities, not statistical matches.
#
# Usage: verify_lattice.sh [path-to-duckdb-cli]
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

SETUP="
CREATE TABLE lattice_bench AS
  SELECT (x / 46225)::INTEGER % 215 AS d1, (x / 215)::INTEGER % 215 AS d2,
         (x % 215)::INTEGER AS d3, x AS payload
  FROM range(0, 9938375) t(x);
CREATE TABLE t_sorted AS SELECT * FROM lattice_bench ORDER BY d1, d2, d3;
"

check "point (7, 9, 11)" "$SETUP
CREATE TEMP TABLE a AS SELECT count(*) AS c, sum(payload) AS s FROM lattice_scan('t_sorted','d1,d2,d3','payload',215,215,215,7,7,9,9,11,11);
CREATE TEMP TABLE b AS SELECT count(*) AS c, sum(payload) AS s FROM t_sorted WHERE d1 = 7 AND d2 = 9 AND d3 = 11;
SELECT (SELECT count(*) FROM a) = (SELECT count(*) FROM b) AND (SELECT sum(s) FROM a) = (SELECT sum(s) FROM b) AS equal;"

check "range box 10..20 cubed" "$SETUP
CREATE TEMP TABLE a AS SELECT count(*) AS c, sum(payload) AS s FROM lattice_scan('t_sorted','d1,d2,d3','payload',215,215,215,10,20,10,20,10,20);
CREATE TEMP TABLE b AS SELECT count(*) AS c, sum(payload) AS s FROM t_sorted WHERE d1 BETWEEN 10 AND 20 AND d2 BETWEEN 10 AND 20 AND d3 BETWEEN 10 AND 20;
SELECT (SELECT count(*) FROM a) = (SELECT count(*) FROM b) AND (SELECT sum(s) FROM a) = (SELECT sum(s) FROM b) AS equal;"

check "full lattice" "$SETUP
CREATE TEMP TABLE a AS SELECT count(*) AS c, sum(payload) AS s FROM lattice_scan('t_sorted','d1,d2,d3','payload',215,215,215,NULL,NULL,NULL,NULL,NULL,NULL);
CREATE TEMP TABLE b AS SELECT count(*) AS c, sum(payload) AS s FROM t_sorted;
SELECT (SELECT count(*) FROM a) = (SELECT count(*) FROM b) AND (SELECT sum(s) FROM a) = (SELECT sum(s) FROM b) AS equal;"

check "point with full-range dims (7, NULL, NULL)" "$SETUP
CREATE TEMP TABLE a AS SELECT count(*) AS c, sum(payload) AS s FROM lattice_scan('t_sorted','d1,d2,d3','payload',215,215,215,7,7,NULL,NULL,NULL,NULL);
CREATE TEMP TABLE b AS SELECT count(*) AS c, sum(payload) AS s FROM t_sorted WHERE d1 = 7;
SELECT (SELECT count(*) FROM a) = (SELECT count(*) FROM b) AND (SELECT sum(s) FROM a) = (SELECT sum(s) FROM b) AS equal;"

echo "all lattice_scan checks passed"
