cmake_minimum_required(VERSION 3.21)
project(cppui_protocol_audit LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 17)
find_package(Qt6 REQUIRED COMPONENTS Core)
get_filename_component(HUB "${CMAKE_CURRENT_SOURCE_DIR}/../../app" ABSOLUTE)
set(V31 "/Users/ellis/QMTUI/CPP/Web/UI_V31/src")
set(V12 "/Users/ellis/QMTUI/CPPETF/UI_V12_双源外盘/src")
# Only pure data models/serializers. No GUI, socket, coordinator or order implementation.
add_executable(protocol-audit audit.cpp
    "${HUB}/modules/premium/engine/common/MarketTypes.cpp"
    "${HUB}/modules/redemption/RedemptionCore.cpp"
    "${V31}/models/PullMonitorModels.cpp"
    "${V12}/models/EtfMonitorModels.cpp")
target_include_directories(protocol-audit PRIVATE "${HUB}" "${V31}" "${V12}")
target_link_libraries(protocol-audit PRIVATE Qt6::Core)
