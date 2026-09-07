cmake_minimum_required(VERSION 3.24)
project(acceptance_repro LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 20)
find_package(Qt6 REQUIRED COMPONENTS Core Network WebSockets Sql Test)
get_filename_component(HUB "${CMAKE_CURRENT_SOURCE_DIR}/../../../app" ABSOLUTE)
add_executable(acceptance-repro main.cpp)
target_include_directories(acceptance-repro PRIVATE "${HUB}" "${HUB}/src")
target_compile_definitions(acceptance-repro PRIVATE HUB_SOURCE="${HUB}")
target_link_libraries(acceptance-repro PRIVATE
  "${HUB}/build-acceptance-20260905/libmachome-hub-modules.a"
  "${HUB}/build-acceptance-20260905/libmachome-hub-common.a"
  Qt6::Core Qt6::Network Qt6::WebSockets Qt6::Sql Qt6::Test)
