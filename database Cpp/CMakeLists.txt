cmake_minimum_required(VERSION 3.24)

project(tgw_cpp VERSION 1.0.0 LANGUAGES CXX)

option(TGW_BUILD_TESTS "Build offline protocol tests" ON)
option(TGW_BUILD_CLI "Build the credential-safe live pull CLI" ON)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

find_package(OpenSSL REQUIRED)
find_package(Threads REQUIRED)
find_package(PkgConfig REQUIRED)
pkg_check_modules(ZSTD REQUIRED IMPORTED_TARGET libzstd)
pkg_check_modules(SIMDJSON REQUIRED IMPORTED_TARGET simdjson)

add_library(tgw_cpp
    src/config.cpp
    src/protocol.cpp
    src/session.cpp
    src/websocket.cpp
)
add_library(tgw::cpp ALIAS tgw_cpp)

target_include_directories(tgw_cpp
    PUBLIC
        $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
        $<INSTALL_INTERFACE:include>
    PRIVATE
        ${CMAKE_CURRENT_SOURCE_DIR}/src
)
target_compile_definitions(tgw_cpp PRIVATE
    TGW_DEFAULT_CA_FILE="${CMAKE_CURRENT_SOURCE_DIR}/certs/vendor-dgw-ca.crt"
)
target_link_libraries(tgw_cpp
    PRIVATE
        OpenSSL::SSL
        OpenSSL::Crypto
        PkgConfig::ZSTD
        PkgConfig::SIMDJSON
        Threads::Threads
)
target_compile_options(tgw_cpp PRIVATE
    $<$<CXX_COMPILER_ID:AppleClang,Clang,GNU>:-Wall;-Wextra;-Wpedantic;-Wconversion;-Wshadow>
)
set_target_properties(tgw_cpp PROPERTIES
    EXPORT_NAME cpp
    VERSION ${PROJECT_VERSION}
    SOVERSION ${PROJECT_VERSION_MAJOR}
    CXX_VISIBILITY_PRESET hidden
    VISIBILITY_INLINES_HIDDEN YES
)

if(TGW_BUILD_CLI)
    add_executable(tgw_pull apps/tgw_pull.cpp)
    target_link_libraries(tgw_pull PRIVATE tgw::cpp)

    add_executable(tgw_type_audit apps/tgw_type_audit.cpp)
    target_link_libraries(tgw_type_audit PRIVATE tgw::cpp PkgConfig::SIMDJSON)
endif()

include(CTest)
if(BUILD_TESTING AND TGW_BUILD_TESTS)
    add_executable(tgw_protocol_tests tests/test_protocol.cpp)
    target_link_libraries(tgw_protocol_tests PRIVATE tgw::cpp PkgConfig::ZSTD)
    add_test(NAME tgw_protocol_tests COMMAND tgw_protocol_tests)
endif()

include(GNUInstallDirs)
install(TARGETS tgw_cpp
    EXPORT tgwCppTargets
    ARCHIVE DESTINATION ${CMAKE_INSTALL_LIBDIR}
    LIBRARY DESTINATION ${CMAKE_INSTALL_LIBDIR}
    RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR}
)
if(TGW_BUILD_CLI)
    install(TARGETS tgw_pull tgw_type_audit
            RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR})
endif()
install(DIRECTORY include/ DESTINATION ${CMAKE_INSTALL_INCLUDEDIR})
install(FILES certs/vendor-dgw-ca.crt DESTINATION ${CMAKE_INSTALL_DATADIR}/tgw_cpp/certs)
install(EXPORT tgwCppTargets
    FILE tgwCppTargets.cmake
    NAMESPACE tgw::
    DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/tgw_cpp
)

include(CMakePackageConfigHelpers)
configure_package_config_file(
    cmake/tgw_cppConfig.cmake.in
    ${CMAKE_CURRENT_BINARY_DIR}/tgw_cppConfig.cmake
    INSTALL_DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/tgw_cpp
)
write_basic_package_version_file(
    ${CMAKE_CURRENT_BINARY_DIR}/tgw_cppConfigVersion.cmake
    VERSION ${PROJECT_VERSION}
    COMPATIBILITY SameMajorVersion
)
install(FILES
    ${CMAKE_CURRENT_BINARY_DIR}/tgw_cppConfig.cmake
    ${CMAKE_CURRENT_BINARY_DIR}/tgw_cppConfigVersion.cmake
    DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/tgw_cpp
)
