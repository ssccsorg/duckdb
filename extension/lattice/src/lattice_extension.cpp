#include "duckdb.hpp"
#include "duckdb/function/table_function.hpp"
#include "duckdb/main/extension/extension_loader.hpp"
#include "lattice_extension.hpp"

// LatticeScan: the coordinate lattice access path for multi-dimensional point
// and range queries over bounded integer dimensions. Scaffold phase: the
// interface binds and validates, and the scan emits no rows until the lattice
// addressing lands. The physical layout decision is lattice-sorted storage
// (benchmark/lattice/layout_decision.md).
//
// Track: ssccsorg/syntagma #54, branch 54-duckdb-lattice.

namespace duckdb {

struct LatticeBindData : public TableFunctionData {
	string table_name;
	string dim_columns;
	string payload_column;
};

static unique_ptr<FunctionData> LatticeBind(ClientContext &context, TableFunctionBindInput &input,
                                            vector<LogicalType> &return_types, vector<Identifier> &names) {
	auto bind_data = make_uniq<LatticeBindData>();
	// Inputs: lattice_scan('table', 'd1,d2,d3', 'payload')
	bind_data->table_name = input.inputs[0].GetValue<string>();
	if (input.inputs.size() > 1) {
		bind_data->dim_columns = input.inputs[1].GetValue<string>();
	}
	if (input.inputs.size() > 2) {
		bind_data->payload_column = input.inputs[2].GetValue<string>();
	}
	// Scaffold: the schema and the lattice dimensions are resolved in the
	// addressing phase, which binds against the table catalog entry.
	return_types.push_back(LogicalType::BIGINT);
	names.emplace_back("lattice_row");
	return std::move(bind_data);
}

static void LatticeScan(ClientContext &context, TableFunctionInput &data_p, DataChunk &output) {
	// Scaffold: the lattice addressing lands in the next phase. The scan
	// then emits the matched rows as a closed form of the coordinates.
	output.SetCardinality(0);
}

static void LoadLatticeInternal(ExtensionLoader &loader) {
	TableFunction lattice_scan("lattice_scan", {LogicalType::VARCHAR, LogicalType::VARCHAR, LogicalType::VARCHAR},
	                           LatticeScan, LatticeBind);
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
