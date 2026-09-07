cmake_minimum_required(VERSION 3.24)
project(tgw_cpp_consumer LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 20)
find_package(tgw_cpp CONFIG REQUIRED)
add_executable(tgw_cpp_consumer main.cpp)
target_link_libraries(tgw_cpp_consumer PRIVATE tgw::cpp)

