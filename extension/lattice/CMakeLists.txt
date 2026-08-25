cmake_minimum_required(VERSION 2.8.12...3.29)

project(LatticeExtension)

include_directories(include)

set(EXTENSION_NAME lattice)

set(LATTICE_EXTENSION_FILES src/lattice_extension.cpp)

build_static_extension(lattice ${LATTICE_EXTENSION_FILES})
set(PARAMETERS "-warnings")
build_loadable_extension(lattice ${PARAMETERS} ${LATTICE_EXTENSION_FILES})
