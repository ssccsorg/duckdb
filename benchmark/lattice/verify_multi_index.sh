#!/usr/bin/env bash
# Correctness and activation checks for the multi-filter index scan
# (the TableScanInitGlobal FIXME, ssccsorg/syntagma #56).
#
# The multi-filter path scans one single-column ART per filtered column and
# intersects the row-ID sets. It activates when every filtered column carries
# a single-column ART and each individual filter stays below the index-scan
# threshold (index_scan_max_count, index_scan_percentage).
#
# Usage: verify_multi_index.sh [path-to-duckdb-cli]
set -euo pipefail

CLI="${1:-build/release/duckdb}"

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

check_plan() {
  local label="$1" expected="$2" sql="$3"
  local out
  out=$("$CLI" -c "$sql" 2>&1)
  if echo "$out" | grep -q "$expected"; then
    echo "OK: $label"
  else
    echo "FAIL: $label"
    echo "$out"
    exit 1
  fi
}

# 2-filter intersection: count, payload, filter order, empty result
check "2-filter intersection matches the analytic expectation" "
SET index_scan_percentage=1.0;
CREATE TABLE t2 AS SELECT (x//215)::INTEGER % 215 AS d1, (x%215)::INTEGER AS d2, x AS payload FROM range(46225) t(x);
CREATE INDEX i1 ON t2(d1);
CREATE INDEX i2 ON t2(d2);
SELECT count(*) = 1 AND sum(payload) = 1514 AS equal FROM t2 WHERE d1 = 7 AND d2 = 9;
SELECT count(*) = 1 AS equal FROM t2 WHERE d2 = 9 AND d1 = 7;
SELECT count(*) = 0 AS equal FROM t2 WHERE d1 = 7 AND d2 = 999;
"

# 3-filter intersection
check "3-filter intersection matches the analytic expectation" "
SET index_scan_percentage=1.0;
CREATE TABLE t3 AS SELECT (x//2500)::INTEGER % 50 AS d1, (x//50)::INTEGER % 50 AS d2, (x%50)::INTEGER AS d3, x AS payload FROM range(125000) t(x);
CREATE INDEX j1 ON t3(d1);
CREATE INDEX j2 ON t3(d2);
CREATE INDEX j3 ON t3(d3);
SELECT count(*) = 1 AND sum(payload) = 17961 AS equal FROM t3 WHERE d1 = 7 AND d2 = 9 AND d3 = 11;
"

# Activation: the multi-filter point query uses the index scan
check_plan "2-filter point activates the index scan" "Index Scan" "
SET index_scan_percentage=1.0;
CREATE TABLE t2 AS SELECT (x//215)::INTEGER % 215 AS d1, (x%215)::INTEGER AS d2, x AS payload FROM range(46225) t(x);
CREATE INDEX i1 ON t2(d1);
CREATE INDEX i2 ON t2(d2);
EXPLAIN ANALYZE SELECT count(*) FROM t2 WHERE d1 = 7 AND d2 = 9;
"

# Single-filter regression on a non-first column (the positional binding fix)
check_plan "single-filter scan on a non-first column activates" "Index Scan" "
SET index_scan_percentage=1.0;
CREATE TABLE t2 AS SELECT (x//215)::INTEGER % 215 AS d1, (x%215)::INTEGER AS d2, x AS payload FROM range(46225) t(x);
CREATE INDEX i2 ON t2(d2);
EXPLAIN ANALYZE SELECT count(*) FROM t2 WHERE d2 = 9;
"

# Missing index on one filtered column falls back to the scan
check_plan "missing index on a filtered column falls back to the scan" "Sequential Scan" "
SET index_scan_percentage=1.0;
CREATE TABLE t2 AS SELECT (x//215)::INTEGER % 215 AS d1, (x%215)::INTEGER AS d2, x AS payload FROM range(46225) t(x);
CREATE INDEX i1 ON t2(d1);
EXPLAIN ANALYZE SELECT count(*) FROM t2 WHERE d1 = 7 AND d2 = 9;
"

# Default threshold: the E=215 3D table has per-dim selectivity above the
# 0.1 percent threshold, so the multi-filter path falls back to the scan
check_plan "default threshold falls back to the scan on the 215 table" "Sequential Scan" "
CREATE TABLE t3 AS SELECT (x//46225)::INTEGER % 215 AS d1, (x//215)::INTEGER % 215 AS d2, (x%215)::INTEGER AS d3, x AS payload FROM range(9938375) t(x);
CREATE INDEX j1 ON t3(d1);
CREATE INDEX j2 ON t3(d2);
CREATE INDEX j3 ON t3(d3);
EXPLAIN ANALYZE SELECT count(*) FROM t3 WHERE d1 = 7 AND d2 = 9 AND d3 = 11;
"

echo "all multi-filter index scan checks passed"
