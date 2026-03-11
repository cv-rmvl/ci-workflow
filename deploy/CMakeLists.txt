cmake_minimum_required(VERSION 3.16)

project(deploy)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

find_package(RMVL REQUIRED)

add_subdirectory(modules)

file(GLOB cpp_files "src/*.cpp")

foreach(f ${cpp_files})
  get_filename_component(n ${f} NAME_WE)
  add_executable(${n} ${f})
  target_link_libraries(${n} PRIVATE ${RMVL_LIBS})
endforeach()
