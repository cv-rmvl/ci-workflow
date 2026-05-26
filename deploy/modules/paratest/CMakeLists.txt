rmvl_generate_para(val)

if(WITH_OPENCV)
  rmvl_add_module(
    paratest
    DEPENDS core
  )
else()
  rmvl_add_module(
    paratest INTERFACE
    DEPENDS core
  )
endif()
