//===----------------------------------------------------------------------===//
//                         DuckDB
//
// duckdb/execution/operator/join/perfect_hash_join_executor.hpp
//
//
//===----------------------------------------------------------------------===//

#pragma once

#include "duckdb/common/row_operations/row_operations.hpp"
#include "duckdb/execution/execution_context.hpp"
#include "duckdb/execution/join_hashtable.hpp"
#include "duckdb/execution/physical_operator.hpp"

namespace duckdb {

class HashJoinOperatorState;
class HashJoinGlobalSinkState;
class PhysicalHashJoin;

struct PerfectHashJoinStats {
	//! Per-dimension build minima and maxima
	vector<Value> build_mins;
	vector<Value> build_maxs;
	//! Per-dimension extents (max - min + 1)
	vector<idx_t> extents;
	//! The packed key space: the product of the extents, the mixed-radix lattice
	idx_t packed_space = 0;
	bool is_build_small = false;
	bool is_build_dense = false;
};

//! PhysicalHashJoin represents a hash loop join between two tables
class PerfectHashJoinExecutor {
	using PerfectHashTable = vector<buffer_ptr<DictionaryEntry>>;

public:
	PerfectHashJoinExecutor(const PhysicalHashJoin &join, JoinHashTable &ht);

public:
	//! The number of equality conditions (dimensions) of the join
	idx_t GetDimensionCount() const;
	bool CanDoPerfectHashJoin(const PhysicalHashJoin &op, const vector<Value> &mins, const vector<Value> &maxs);

	bool BuildPerfectHashTable();

	unique_ptr<OperatorState> GetOperatorState(ExecutionContext &context);
	OperatorResultType ProbePerfectHashTable(ExecutionContext &context, DataChunk &input, DataChunk &lhs_output_columns,
	                                         DataChunk &chunk, OperatorState &state);

private:
	bool FullScanHashTable();
	template <typename T>
	bool TemplatedComputeDimOffsets(const Vector &source, idx_t count, T min_value, T max_value,
	                                vector<idx_t> &offsets, vector<bool> &in_domain) const;

private:
	const PhysicalHashJoin &join;
	JoinHashTable &ht;
	//! Columnar perfect hash table
	PerfectHashTable perfect_hash_table;
	//! Build statistics
	PerfectHashJoinStats perfect_join_statistics;
	//! Stores the occurrences of each value in the build side
	ValidityMask bitmap_build_idx;
	//! Stores the number of unique keys in the build side
	idx_t unique_keys = 0;
};

} // namespace duckdb
