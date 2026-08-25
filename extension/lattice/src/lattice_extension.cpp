#include "duckdb.hpp"
#include "duckdb/catalog/catalog.hpp"
#include "duckdb/catalog/catalog_entry/duck_table_entry.hpp"
#include "duckdb/catalog/catalog_entry/table_catalog_entry.hpp"
#include "duckdb/common/exception/binder_exception.hpp"
#include "duckdb/common/string_util.hpp"
#include "duckdb/common/types/vector.hpp"
#include "duckdb/function/table_function.hpp"
#include "duckdb/main/extension/extension_loader.hpp"
#include "duckdb/parser/qualified_name.hpp"
#include "duckdb/storage/data_table.hpp"
#include "duckdb/storage/table/scan_state.hpp"
#include "duckdb/transaction/duck_transaction.hpp"
#include "lattice_extension.hpp"

// LatticeScan: the coordinate lattice access path for multi-dimensional point
// and range queries over bounded integer dimensions. The table is addressed as
// a regular lattice: the dimension values are bounded, one row per cell, and
// the table is stored in lattice order (the physical layout decision in
// benchmark/lattice/layout_decision.md). A cell (d1, d2, d3) with extents
// (E1, E2, E3) maps to the row offset
//
//   n = ((d1 * E2 + d2) * E3 + d3)
//
// in closed form, with no hash and no index scan. A point query is one
// offset; a range box enumerates the matching subspace with the same
// arithmetic.
//
// Interface:
//   lattice_scan('table', 'd1,d2,d3', 'payload', E1, E2, E3,
//                lo1, hi1, lo2, hi2, lo3, hi3)
// Each lo/hi pair is a per-dimension range; NULL means the full range and
// lo == hi is a point.
//
// Track: ssccsorg/syntagma #54, branch 54-duckdb-lattice.

namespace duckdb {

struct LatticeBindData : public TableFunctionData {
	LatticeBindData(TableCatalogEntry &table_p) : table(table_p) {
	}

	string table_name;
	vector<string> dim_columns;
	string payload_column;
	vector<idx_t> extents;
	vector<optional<idx_t>> lo;
	vector<optional<idx_t>> hi;
	idx_t lo1, hi1, lo2, hi2, lo3, hi3;
	vector<StorageIndex> dim_storage;
	StorageIndex payload_storage;
	LogicalType payload_type;
	TableCatalogEntry &table;
};

struct LatticeGlobalState : public GlobalTableFunctionState {
	// Mixed-radix odometer over the query box. The next cell to emit.
	idx_t d1 = 0;
	idx_t d2 = 0;
	idx_t d3 = 0;
	bool finished = false;
};

static unique_ptr<GlobalTableFunctionState> LatticeInitGlobal(ClientContext &context, TableFunctionInitInput &input) {
	auto &bind_data = input.bind_data->Cast<LatticeBindData>();
	auto gstate = make_uniq<LatticeGlobalState>();
	gstate->d1 = bind_data.lo1;
	gstate->d2 = bind_data.lo2;
	gstate->d3 = bind_data.lo3;
	return std::move(gstate);
}

static unique_ptr<FunctionData> LatticeBind(ClientContext &context, TableFunctionBindInput &input,
                                            vector<LogicalType> &return_types, vector<Identifier> &names) {
	if (input.inputs.size() < 12) {
		throw BinderException("lattice_scan requires 12 arguments: table, dims, payload, E1, E2, E3, "
		                      "lo1, hi1, lo2, hi2, lo3, hi3");
	}
	auto table_name = input.inputs[0].GetValue<string>();
	auto &table = Catalog::GetEntry<TableCatalogEntry>(context, QualifiedName::Parse(table_name));

	auto bind_data = make_uniq<LatticeBindData>(table);
	bind_data->table_name = table_name;
	bind_data->dim_columns = StringUtil::Split(input.inputs[1].GetValue<string>(), ',');
	bind_data->payload_column = input.inputs[2].GetValue<string>();

	for (idx_t i = 0; i < 3; i++) {
		bind_data->extents.push_back(input.inputs[3 + i].GetValue<idx_t>());
	}
	for (idx_t i = 0; i < 3; i++) {
		auto &lo = input.inputs[6 + 2 * i];
		auto &hi = input.inputs[7 + 2 * i];
		bind_data->lo.push_back(lo.IsNull() ? optional<idx_t>() : optional<idx_t>(lo.GetValue<idx_t>()));
		bind_data->hi.push_back(hi.IsNull() ? optional<idx_t>() : optional<idx_t>(hi.GetValue<idx_t>()));
		if (bind_data->lo[i].value_or(0) > bind_data->hi[i].value_or(bind_data->extents[i] - 1)) {
			throw BinderException("lattice_scan: inverted range on dimension %d", i);
		}
	}
	bind_data->lo1 = bind_data->lo[0].value_or(0);
	bind_data->hi1 = bind_data->hi[0].value_or(bind_data->extents[0] - 1);
	bind_data->lo2 = bind_data->lo[1].value_or(0);
	bind_data->hi2 = bind_data->hi[1].value_or(bind_data->extents[1] - 1);
	bind_data->lo3 = bind_data->lo[2].value_or(0);
	bind_data->hi3 = bind_data->hi[2].value_or(bind_data->extents[2] - 1);

	for (auto &dim : bind_data->dim_columns) {
		auto &col = table.GetColumn(Identifier(dim));
		bind_data->dim_storage.push_back(StorageIndex(col.Logical().index));
		return_types.push_back(LogicalType::BIGINT);
		names.emplace_back(dim);
	}
	auto &payload = table.GetColumn(Identifier(bind_data->payload_column));
	bind_data->payload_storage = StorageIndex(payload.Logical().index);
	bind_data->payload_type = payload.Type();
	return_types.push_back(payload.Type());
	names.emplace_back(bind_data->payload_column);
	return std::move(bind_data);
}

static void LatticeScan(ClientContext &context, TableFunctionInput &data_p, DataChunk &output) {
	auto &bind_data = data_p.bind_data->Cast<LatticeBindData>();
	auto &gstate = data_p.global_state->Cast<LatticeGlobalState>();
	auto &duck_table = bind_data.table.Cast<DuckTableEntry>();
	auto &tx = DuckTransaction::Get(context, duck_table.catalog);
	auto &storage = duck_table.GetStorage();

	// Enumerate the matching subspace in lattice order, one chunk at a time,
	// and map each cell to its row offset with the mixed-radix closed form.
	const auto e2 = bind_data.extents[1];
	const auto e3 = bind_data.extents[2];
	const idx_t max_count = STANDARD_VECTOR_SIZE;

	vector<row_t> row_ids;
	vector<int64_t> d1s;
	vector<int64_t> d2s;
	vector<int64_t> d3s;
	row_ids.reserve(max_count);
	d1s.reserve(max_count);
	d2s.reserve(max_count);
	d3s.reserve(max_count);

	while (row_ids.size() < max_count && !gstate.finished) {
		const auto d1 = gstate.d1;
		const auto d2 = gstate.d2;
		const auto d3 = gstate.d3;
		row_ids.push_back(NumericCast<row_t>((d1 * e2 + d2) * e3 + d3));
		d1s.push_back(NumericCast<int64_t>(d1));
		d2s.push_back(NumericCast<int64_t>(d2));
		d3s.push_back(NumericCast<int64_t>(d3));
		// Advance the odometer.
		if (d3 < bind_data.hi3) {
			gstate.d3++;
		} else if (d2 < bind_data.hi2) {
			gstate.d2++;
			gstate.d3 = bind_data.lo3;
		} else if (d1 < bind_data.hi1) {
			gstate.d1++;
			gstate.d2 = bind_data.lo2;
			gstate.d3 = bind_data.lo3;
		} else {
			gstate.finished = true;
		}
	}

	idx_t count = row_ids.size();
	output.SetCardinality(count);
	if (count == 0) {
		return;
	}

	auto d1_data = FlatVector::GetDataMutable<int64_t>(output.data[0]);
	auto d2_data = FlatVector::GetDataMutable<int64_t>(output.data[1]);
	auto d3_data = FlatVector::GetDataMutable<int64_t>(output.data[2]);
	for (idx_t i = 0; i < count; i++) {
		d1_data[i] = d1s[i];
		d2_data[i] = d2s[i];
		d3_data[i] = d3s[i];
	}

	// Fetch the payload rows at the addressed offsets.
	Vector row_id_vector(LogicalType::ROW_TYPE, reinterpret_cast<data_ptr_t>(row_ids.data()), count);
	DataChunk payload_chunk;
	payload_chunk.Initialize(context, {bind_data.payload_type});
	ColumnFetchState fetch_state;
	storage.Fetch(tx, payload_chunk, {bind_data.payload_storage}, row_id_vector, count, fetch_state);
	count = MinValue<idx_t>(count, payload_chunk.size());
	output.SetCardinality(count);
	for (idx_t i = 0; i < count; i++) {
		output.data[3].SetValue(i, payload_chunk.data[0].GetValue(i));
	}
}

static void LoadLatticeInternal(ExtensionLoader &loader) {
	TableFunction lattice_scan("lattice_scan",
	                           {LogicalType::VARCHAR, LogicalType::VARCHAR, LogicalType::VARCHAR, LogicalType::BIGINT,
	                            LogicalType::BIGINT, LogicalType::BIGINT, LogicalType::BIGINT, LogicalType::BIGINT,
	                            LogicalType::BIGINT, LogicalType::BIGINT, LogicalType::BIGINT, LogicalType::BIGINT},
	                           LatticeScan, LatticeBind, LatticeInitGlobal);
	lattice_scan.parallelism = TableFunctionParallelism::FORCE_SINGLE_THREADED;
	loader.RegisterFunction(lattice_scan);
}

void LatticeExtension::Load(ExtensionLoader &loader) {
	LoadLatticeInternal(loader);
}

std::string LatticeExtension::Name() {
	return "lattice";
}

std::string LatticeExtension::Version() const {
	return "0.1.0";
}

} // namespace duckdb

extern "C" {

DUCKDB_CPP_EXTENSION_ENTRY(lattice, loader) { // NOLINT
	duckdb::LoadLatticeInternal(loader);
}
}
