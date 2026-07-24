cmake_minimum_required(VERSION 3.16)

project(RMVL_KDT)

find_package(RMVL REQUIRED)

set(KDT_RUNTIME_OUTPUT_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}/bin")
set(CMAKE_RUNTIME_OUTPUT_DIRECTORY "${KDT_RUNTIME_OUTPUT_DIRECTORY}")

# Visual Studio 等多配置生成器默认会追加 Debug、Release 子目录。为每个
# 配置显式设置输出目录，保证 Python 组件始终从 adapter/bin 加载程序。
foreach(config IN LISTS CMAKE_CONFIGURATION_TYPES)
  string(TOUPPER "${config}" config_upper)
  set(
    "CMAKE_RUNTIME_OUTPUT_DIRECTORY_${config_upper}"
    "${KDT_RUNTIME_OUTPUT_DIRECTORY}"
  )
endforeach()

add_subdirectory(dep)

aux_source_directory(src src_list)

foreach(f ${src_list})
  get_filename_component(f_name ${f} NAME_WLE)
  rmvl_add_exe(
    ${f_name}
    SOURCES ${f}
    DEPENDS lpss kdt_dep
  )
endforeach()
