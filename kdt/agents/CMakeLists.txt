cmake_minimum_required(VERSION 3.16)

project(kdt_agent LANGUAGES CXX)

find_package(RMVL REQUIRED)

get_filename_component(KDT_ROOT_PATH "${CMAKE_CURRENT_SOURCE_DIR}/.." ABSOLUTE)

configure_file(
  ${CMAKE_CURRENT_SOURCE_DIR}/runtime/pathconf.hpp.in
  ${CMAKE_CURRENT_BINARY_DIR}/rt/pathconf.hpp
)

include_directories(${CMAKE_CURRENT_BINARY_DIR}/rt)

rmvl_add_exe(rmvl_kdt_agent
  SOURCES kdt-agent.cpp
  DEPENDS io
)
