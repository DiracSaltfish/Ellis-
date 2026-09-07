cmake_minimum_required(VERSION 3.24)
project(machome_hub_prototype VERSION 0.1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

find_package(Qt6 6.5 REQUIRED COMPONENTS Widgets)

qt_add_executable(machome-hub-prototype
    MACOSX_BUNDLE
    src/main.cpp
)

target_link_libraries(machome-hub-prototype PRIVATE Qt6::Widgets)

set_target_properties(machome-hub-prototype PROPERTIES
    MACOSX_BUNDLE_GUI_IDENTIFIER com.ellis.machome-hub.prototype
    MACOSX_BUNDLE_BUNDLE_NAME "Machome Hub Prototype"
    MACOSX_BUNDLE_SHORT_VERSION_STRING ${PROJECT_VERSION}
)

