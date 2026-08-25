#include "duckdb/execution/operator/join/perfect_hash_join_executor.hpp"

#include "duckdb/common/operator/subtract.hpp"
#include "duckdb/execution/operator/join/physical_hash_join.hpp"
#include "duckdb/common/atomic.hpp"
#include "duckdb/planner/joinside.hpp"

namespace duckdb {

PerfectHashJoinExecutor::PerfectHashJoinExecutor(const PhysicalHashJoin &join_p, JoinHashTable &ht_p)
    : join(join_p), ht(ht_p) {
}

idx_t PerfectHashJoinExecutor::GetDimensionCount() const {
	return join.conditions.size();
}

//===--------------------------------------------------------------------===//
// Initialize
//===--------------------------------------------------------------------===//
bool ExtractNumericValue(const Value &val, hugeint_t &result) {
	if (!val.type().IsIntegral()) {
		switch (val.type().InternalType()) {
		case PhysicalType::INT8:
			result = Hugeint::Convert(val.GetValueUnsafe<int8_t>());
			break;
		case PhysicalType::INT16:
			result = Hugeint::Convert(val.GetValueUnsafe<int16_t>());
			break;
		case PhysicalType::INT32:
			result = Hugeint::Convert(val.GetValueUnsafe<int32_t>());
			break;
		case PhysicalType::INT64:
			result = Hugeint::Convert(val.GetValueUnsafe<int64_t>());
			break;
		case PhysicalType::INT128:
			result = val.GetValueUnsafe<hugeint_t>();
			break;
		case PhysicalType::UINT8:
			result = Hugeint::Convert(val.GetValueUnsafe<uint8_t>());
			break;
		case PhysicalType::UINT16:
			result = Hugeint::Convert(val.GetValueUnsafe<uint16_t>());
			break;
		case PhysicalType::UINT32:
			result = Hugeint::Convert(val.GetValueUnsafe<uint32_t>());
			break;
		case PhysicalType::UINT64:
			result = Hugeint::Convert(val.GetValueUnsafe<uint64_t>());
			break;
		case PhysicalType::UINT128: {
			const auto uhugeint_val = val.GetValueUnsafe<uhugeint_t>();
			if (uhugeint_val > NumericCast<uhugeint_t>(NumericLimits<hugeint_t>::Maximum())) {
				return false;
			}
			result.lower = uhugeint_val.lower;
			result.upper = NumericCast<int64_t>(uhugeint_val.upper);
			break;
		}
		default:
			return false;
		}
	} else {
		auto cast = val.DefaultTryCastAs(LogicalType::HUGEINT);
		if (!cast) {
			return false;
		}
		result = cast->GetValue<hugeint_t>();
	}
	return true;
}

bool PerfectHashJoinExecutor::CanDoPerfectHashJoin(const PhysicalHashJoin &op, const vector<Value> &mins,
                                                   const vector<Value> &maxs) {
	// TODO: Add support for residual predicates
	if (op.predicate) {
		return false;
	}

	if (perfect_join_statistics.is_build_small) {
		return true; // Already true based on static statistics
	}

	// We only do this optimization for inner joins with integer equality conditions
	const auto dims = mins.size();
	if (op.join_type != JoinType::INNER || op.conditions.size() != dims || dims == 0) {
		return false;
	}
	for (idx_t d = 0; d < dims; d++) {
		if (op.conditions[d].GetComparisonType() != ExpressionType::COMPARE_EQUAL ||
		    !TypeIsInteger(op.conditions[d].GetLHS().GetReturnType().InternalType())) {
			return false;
		}
	}

	// We bail out if there are nested types on the RHS
	for (auto &type : op.children[1].get().GetTypes()) {
		switch (type.InternalType()) {
		case PhysicalType::STRUCT:
		case PhysicalType::LIST:
		case PhysicalType::ARRAY:
			return false;
		default:
			break;
		}
	}

	// The packed key space is the product of the per-condition extents
	idx_t packed_space = 1;
	vector<idx_t> extents;
	extents.reserve(dims);
	for (idx_t d = 0; d < dims; d++) {
		hugeint_t min_value, max_value;
		if (!ExtractNumericValue(mins[d], min_value) || !ExtractNumericValue(maxs[d], max_value)) {
			return false;
		}
		if (max_value < min_value) {
			return false; // Empty table
		}
		hugeint_t extent_value;
		if (!TrySubtractOperator::Operation(max_value, min_value, extent_value)) {
			return false;
		}
		extent_value += 1;
		if (extent_value > Hugeint::Convert(NumericLimits<idx_t>::Maximum())) {
			return false;
		}
		const auto extent = NumericCast<idx_t>(extent_value);
		if (packed_space > NumericLimits<idx_t>::Maximum() / extent) {
			return false;
		}
		packed_space *= extent;
		extents.push_back(extent);
	}

	// The max size our build must have to run the perfect HJ
	static constexpr idx_t MAX_BUILD_SIZE = 1048576;
	if (packed_space > MAX_BUILD_SIZE) {
		return false;
	}

	// If count is larger than the packed space (duplicates), we bail out
	if (ht.Count() > packed_space) {
		return false;
	}

	perfect_join_statistics.build_mins = mins;
	perfect_join_statistics.build_maxs = maxs;
	perfect_join_statistics.extents = extents;
	perfect_join_statistics.packed_space = packed_space;
	perfect_join_statistics.is_build_small = true;
	return true;
}

//===--------------------------------------------------------------------===//
// Build
//===--------------------------------------------------------------------===//
bool PerfectHashJoinExecutor::BuildPerfectHashTable() {
	// First, allocate memory for each build column
	const auto build_size = perfect_join_statistics.packed_space;
	for (const auto &type : join.rhs_output_columns.col_types) {
		// PHJ keeps each entry alive for the operator's lifetime and wraps it in every emitted chunk
		perfect_hash_table.emplace_back(DictionaryVector::CreateReusableGlobalDictionary(type, build_size));
	}

	// and for duplicate_checking
	bitmap_build_idx.Initialize(build_size);
	bitmap_build_idx.SetAllInvalid(build_size);

	// Now fill columns with build data
	return FullScanHashTable();
}

bool PerfectHashJoinExecutor::FullScanHashTable() {
	auto &data_collection = ht.GetDataCollection();

	const auto dims = GetDimensionCount();
	const auto key_count = ht.Count();
	Vector tuples_addresses(LogicalType::POINTER, key_count);

	// Scan each key column of the build side
	vector<Vector> key_vectors;
	key_vectors.reserve(dims);
	for (idx_t d = 0; d < dims; d++) {
		Vector build_vector(ht.equality_types[d], key_count);
		ht.ScanKeyColumn(tuples_addresses, build_vector, d);
		key_vectors.push_back(std::move(build_vector));
	}

	// Per-dimension offsets and domain validity: a row is in the domain only if
	// every key lies within its condition's min-max range
	vector<vector<idx_t>> offsets(dims, vector<idx_t>(key_count));
	vector<bool> in_domain(key_count, true);
	for (idx_t d = 0; d < dims; d++) {
		const auto &min_value = perfect_join_statistics.build_mins[d];
		const auto &max_value = perfect_join_statistics.build_maxs[d];
		switch (key_vectors[d].GetType().InternalType()) {
		case PhysicalType::INT8:
			TemplatedComputeDimOffsets<int8_t>(key_vectors[d], key_count, min_value.GetValueUnsafe<int8_t>(),
			                                   max_value.GetValueUnsafe<int8_t>(), offsets[d], in_domain);
			break;
		case PhysicalType::INT16:
			TemplatedComputeDimOffsets<int16_t>(key_vectors[d], key_count, min_value.GetValueUnsafe<int16_t>(),
			                                    max_value.GetValueUnsafe<int16_t>(), offsets[d], in_domain);
			break;
		case PhysicalType::INT32:
			TemplatedComputeDimOffsets<int32_t>(key_vectors[d], key_count, min_value.GetValueUnsafe<int32_t>(),
			                                    max_value.GetValueUnsafe<int32_t>(), offsets[d], in_domain);
			break;
		case PhysicalType::INT64:
			TemplatedComputeDimOffsets<int64_t>(key_vectors[d], key_count, min_value.GetValueUnsafe<int64_t>(),
			                                    max_value.GetValueUnsafe<int64_t>(), offsets[d], in_domain);
			break;
		case PhysicalType::INT128:
			TemplatedComputeDimOffsets<hugeint_t>(key_vectors[d], key_count, min_value.GetValueUnsafe<hugeint_t>(),
			                                      max_value.GetValueUnsafe<hugeint_t>(), offsets[d], in_domain);
			break;
		case PhysicalType::UINT8:
			TemplatedComputeDimOffsets<uint8_t>(key_vectors[d], key_count, min_value.GetValueUnsafe<uint8_t>(),
			                                    max_value.GetValueUnsafe<uint8_t>(), offsets[d], in_domain);
			break;
		case PhysicalType::UINT16:
			TemplatedComputeDimOffsets<uint16_t>(key_vectors[d], key_count, min_value.GetValueUnsafe<uint16_t>(),
			                                     max_value.GetValueUnsafe<uint16_t>(), offsets[d], in_domain);
			break;
		case PhysicalType::UINT32:
			TemplatedComputeDimOffsets<uint32_t>(key_vectors[d], key_count, min_value.GetValueUnsafe<uint32_t>(),
			                                     max_value.GetValueUnsafe<uint32_t>(), offsets[d], in_domain);
			break;
		case PhysicalType::UINT64:
			TemplatedComputeDimOffsets<uint64_t>(key_vectors[d], key_count, min_value.GetValueUnsafe<uint64_t>(),
			                                     max_value.GetValueUnsafe<uint64_t>(), offsets[d], in_domain);
			break;
		case PhysicalType::UINT128:
			TemplatedComputeDimOffsets<uhugeint_t>(key_vectors[d], key_count, min_value.GetValueUnsafe<uhugeint_t>(),
			                                       max_value.GetValueUnsafe<uhugeint_t>(), offsets[d], in_domain);
			break;
		default:
			throw NotImplementedException("Type not supported for perfect hash join");
		}
	}

	// Fold the offsets into the mixed-radix position and check duplicates
	SelectionVector sel_build(key_count + 1);
	SelectionVector sel_tuples(key_count + 1);
	const auto &extents = perfect_join_statistics.extents;
	unique_keys = 0;
	for (idx_t i = 0; i < key_count; i++) {
		if (!in_domain[i]) {
			continue;
		}
		idx_t position = offsets[0][i];
		for (idx_t d = 1; d < dims; d++) {
			position = position * extents[d] + offsets[d][i];
		}
		if (bitmap_build_idx.RowIsValidUnsafe(position)) {
			return false; // duplicate key
		}
		bitmap_build_idx.SetValidUnsafe(position);
		sel_build.set_index(unique_keys, position);
		sel_tuples.set_index(unique_keys, i);
		unique_keys++;
	}

	const auto build_size = perfect_join_statistics.packed_space;
	if (unique_keys == build_size && !ht.has_null) {
		perfect_join_statistics.is_build_dense = true;
		bitmap_build_idx.Reset(build_size); // All valid
	}

	// Full scan the remaining build columns and fill the perfect hash table
	for (idx_t i = 0; i < join.rhs_output_columns.col_types.size(); i++) {
		auto &vector = perfect_hash_table[i]->data;
		const auto output_col_idx = ht.output_columns[i];
		D_ASSERT(vector.GetType() == ht.layout_ptr->GetTypes()[output_col_idx]);
		auto &col_mask = FlatVector::ValidityMutable(vector);
		col_mask.Reset(build_size);
		data_collection.Gather(tuples_addresses, sel_tuples, unique_keys, output_col_idx, vector, sel_build, nullptr);
		// This ensures the empty entries are set to NULL, so that the emitted dictionary vectors make sense
		col_mask.Combine(bitmap_build_idx, build_size);
	}

	return true;
}

template <typename T>
bool PerfectHashJoinExecutor::TemplatedComputeDimOffsets(const Vector &source, idx_t count, T min_value, T max_value,
                                                         vector<idx_t> &offsets, vector<bool> &in_domain) const {
	auto entries = source.Values<T>();
	for (idx_t i = 0; i < count; i++) {
		auto input_value = entries.GetValueUnsafe(i);
		// compute the offset if the value is in the range
		if (min_value <= input_value && input_value <= max_value) {
			offsets[i] = UnsafeNumericCast<idx_t>(input_value - min_value);
		} else {
			in_domain[i] = false;
		}
	}
	return true;
}

//===--------------------------------------------------------------------===//
// Probe
//===--------------------------------------------------------------------===//
class PerfectHashJoinState : public OperatorState {
public:
	PerfectHashJoinState(ClientContext &context, const PhysicalHashJoin &join) : probe_executor(context) {
		join_keys.Initialize(Allocator::Get(context), join.condition_types);
		for (auto &cond : join.conditions) {
			probe_executor.AddExpression(cond.GetLHS());
		}
		build_sel_vec.Initialize(STANDARD_VECTOR_SIZE);
		probe_sel_vec.Initialize(STANDARD_VECTOR_SIZE);
		seq_sel_vec.Initialize(STANDARD_VECTOR_SIZE);
	}

	DataChunk join_keys;
	ExpressionExecutor probe_executor;
	SelectionVector build_sel_vec;
	SelectionVector probe_sel_vec;
	SelectionVector seq_sel_vec;
};

unique_ptr<OperatorState> PerfectHashJoinExecutor::GetOperatorState(ExecutionContext &context) {
	auto state = make_uniq<PerfectHashJoinState>(context.client, join);
	return std::move(state);
}

OperatorResultType PerfectHashJoinExecutor::ProbePerfectHashTable(ExecutionContext &context, DataChunk &input,
                                                                  DataChunk &lhs_output_columns, DataChunk &result,
                                                                  OperatorState &state_p) {
	auto &state = state_p.Cast<PerfectHashJoinState>();
	// keeps track of how many probe keys have a match
	idx_t probe_sel_count = 0;

	// fetch the join keys from the chunk
	state.join_keys.Reset();
	state.probe_executor.Execute(input, state.join_keys);
	const auto keys_count = state.join_keys.size();
	const auto dims = GetDimensionCount();

	// Per-dimension offsets of the probe keys relative to the build domain
	vector<vector<idx_t>> offsets(dims, vector<idx_t>(keys_count));
	vector<bool> in_domain(keys_count, true);
	for (idx_t d = 0; d < dims; d++) {
		const auto &keys_vec = state.join_keys.data[d];
		const auto &min_value = perfect_join_statistics.build_mins[d];
		const auto &max_value = perfect_join_statistics.build_maxs[d];
		switch (keys_vec.GetType().InternalType()) {
		case PhysicalType::INT8:
			TemplatedComputeDimOffsets<int8_t>(keys_vec, keys_count, min_value.GetValueUnsafe<int8_t>(),
			                                   max_value.GetValueUnsafe<int8_t>(), offsets[d], in_domain);
			break;
		case PhysicalType::INT16:
			TemplatedComputeDimOffsets<int16_t>(keys_vec, keys_count, min_value.GetValueUnsafe<int16_t>(),
			                                    max_value.GetValueUnsafe<int16_t>(), offsets[d], in_domain);
			break;
		case PhysicalType::INT32:
			TemplatedComputeDimOffsets<int32_t>(keys_vec, keys_count, min_value.GetValueUnsafe<int32_t>(),
			                                    max_value.GetValueUnsafe<int32_t>(), offsets[d], in_domain);
			break;
		case PhysicalType::INT64:
			TemplatedComputeDimOffsets<int64_t>(keys_vec, keys_count, min_value.GetValueUnsafe<int64_t>(),
			                                    max_value.GetValueUnsafe<int64_t>(), offsets[d], in_domain);
			break;
		case PhysicalType::INT128:
			TemplatedComputeDimOffsets<hugeint_t>(keys_vec, keys_count, min_value.GetValueUnsafe<hugeint_t>(),
			                                      max_value.GetValueUnsafe<hugeint_t>(), offsets[d], in_domain);
			break;
		case PhysicalType::UINT8:
			TemplatedComputeDimOffsets<uint8_t>(keys_vec, keys_count, min_value.GetValueUnsafe<uint8_t>(),
			                                    max_value.GetValueUnsafe<uint8_t>(), offsets[d], in_domain);
			break;
		case PhysicalType::UINT16:
			TemplatedComputeDimOffsets<uint16_t>(keys_vec, keys_count, min_value.GetValueUnsafe<uint16_t>(),
			                                     max_value.GetValueUnsafe<uint16_t>(), offsets[d], in_domain);
			break;
		case PhysicalType::UINT32:
			TemplatedComputeDimOffsets<uint32_t>(keys_vec, keys_count, min_value.GetValueUnsafe<uint32_t>(),
			                                     max_value.GetValueUnsafe<uint32_t>(), offsets[d], in_domain);
			break;
		case PhysicalType::UINT64:
			TemplatedComputeDimOffsets<uint64_t>(keys_vec, keys_count, min_value.GetValueUnsafe<uint64_t>(),
			                                     max_value.GetValueUnsafe<uint64_t>(), offsets[d], in_domain);
			break;
		case PhysicalType::UINT128:
			TemplatedComputeDimOffsets<uhugeint_t>(keys_vec, keys_count, min_value.GetValueUnsafe<uhugeint_t>(),
			                                       max_value.GetValueUnsafe<uhugeint_t>(), offsets[d], in_domain);
			break;
		default:
			throw NotImplementedException("Type not supported for perfect hash join");
		}
	}

	// Fold the offsets into the mixed-radix position and fill the selection vectors
	const auto &extents = perfect_join_statistics.extents;
	for (idx_t i = 0; i < keys_count; i++) {
		if (!in_domain[i]) {
			continue;
		}
		idx_t position = offsets[0][i];
		for (idx_t d = 1; d < dims; d++) {
			position = position * extents[d] + offsets[d][i];
		}
		// only probe positions that are occupied by the build side
		if (!bitmap_build_idx.RowIsValid(position)) {
			continue;
		}
		state.build_sel_vec.set_index(probe_sel_count, position);
		state.probe_sel_vec.set_index(probe_sel_count, i);
		probe_sel_count++;
	}

	// If build is dense and probe is in build's domain, just reference probe
	if (perfect_join_statistics.is_build_dense && keys_count == probe_sel_count) {
		result.Reference(lhs_output_columns);
	} else {
		// otherwise, filter it out the values that do not match
		result.Slice(lhs_output_columns, state.probe_sel_vec, probe_sel_count, 0);
	}
	// on the build side, we need to fetch the data and build dictionary vectors with the sel_vec
	for (idx_t i = 0; i < join.rhs_output_columns.col_types.size(); i++) {
		auto &result_vector = result.data[lhs_output_columns.ColumnCount() + i];
		D_ASSERT(result_vector.GetType() == ht.layout_ptr->GetTypes()[ht.output_columns[i]]);
		result_vector.Dictionary(perfect_hash_table[i], state.build_sel_vec, probe_sel_count);
	}
	return OperatorResultType::NEED_MORE_INPUT;
}

} // namespace duckdb
