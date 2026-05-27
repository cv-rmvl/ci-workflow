if(NOT COMMAND rmvl_generate_msg)
  message(STATUS "Skipping msgtest: rmvl_generate_msg is unavailable in this RMVL version")
  return()
endif()

rmvl_generate_msg(Test)

rmvl_add_module(
  msgtest
  DEPENDS core
)
