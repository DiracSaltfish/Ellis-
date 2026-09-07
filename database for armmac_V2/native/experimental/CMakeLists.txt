cmake_minimum_required(VERSION 3.24)
project(tgw_macos_port LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_OSX_ARCHITECTURES "arm64" CACHE STRING "target arch" FORCE)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

add_library(tgw_core SHARED
  ${CMAKE_CURRENT_LIST_DIR}/src/tgw_core.cpp)
target_include_directories(tgw_core PUBLIC
  ${CMAKE_CURRENT_LIST_DIR}/include)
set_target_properties(tgw_core PROPERTIES
  OUTPUT_NAME "tgw_core"
  MACOSX_RPATH ON)

add_executable(tgw_demo demo_main.cpp)
target_link_libraries(tgw_demo PRIVATE tgw_core)
set_target_properties(tgw_demo PROPERTIES
  BUILD_WITH_INSTALL_RPATH ON
  BUILD_RPATH "@executable_path/../lib"
  INSTALL_RPATH "@executable_path/../lib")
