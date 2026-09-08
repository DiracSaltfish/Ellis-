cmake_minimum_required(VERSION 3.24)

if(APPLE AND ("${CMAKE_OSX_DEPLOYMENT_TARGET}" STREQUAL ""
              OR "${CMAKE_OSX_DEPLOYMENT_TARGET}" VERSION_LESS "26.0"))
    # An empty value is emitted as an empty LSMinimumSystemVersion by CMake.
    # The production Homebrew Qt/protobuf/abseil binaries target macOS 26.
    # Declare that honestly so LaunchServices and Mach-O metadata agree.
    set(CMAKE_OSX_DEPLOYMENT_TARGET "26.0" CACHE STRING
        "Minimum supported macOS version" FORCE)
endif()

project(machome_operations_hub VERSION 1.0.0 LANGUAGES CXX)

option(MACHOME_BUILD_NATIVE_IBKR_BRIDGE
    "Build the optional native IBKR TWS quote fan-out sidecar" OFF)
if(APPLE)
    set(MACHOME_BUILD_PREMIUM_TGW_HELPER_DEFAULT ON)
else()
    set(MACHOME_BUILD_PREMIUM_TGW_HELPER_DEFAULT OFF)
endif()
option(MACHOME_BUILD_PREMIUM_TGW_HELPER
    "Build the bundled native TGW market-data helper"
    ${MACHOME_BUILD_PREMIUM_TGW_HELPER_DEFAULT})
option(MACHOME_ENABLE_LIVE_QMT_ORDERS
    "Compile the native redemption engine with real QMT order capability" OFF)

string(TIMESTAMP MACHOME_HUB_BUILD_TIME "%Y-%m-%dT%H:%M:%SZ" UTC)
execute_process(
    COMMAND git rev-parse --short=12 HEAD
    WORKING_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}"
    RESULT_VARIABLE MACHOME_HUB_GIT_RESULT
    OUTPUT_VARIABLE MACHOME_HUB_GIT_COMMIT
    OUTPUT_STRIP_TRAILING_WHITESPACE
    ERROR_QUIET
)
execute_process(
    COMMAND git ls-files --error-unmatch -- CMakeLists.txt
    WORKING_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}"
    RESULT_VARIABLE MACHOME_HUB_GIT_TRACKED_RESULT
    OUTPUT_QUIET
    ERROR_QUIET
)
# Do not attribute an untracked standalone delivery to a Git repository found
# only by walking into a parent directory. Until this project itself is added,
# the build identity must state that source control provenance is unavailable.
if(NOT MACHOME_HUB_GIT_RESULT EQUAL 0
   OR NOT MACHOME_HUB_GIT_TRACKED_RESULT EQUAL 0
   OR MACHOME_HUB_GIT_COMMIT STREQUAL "")
    set(MACHOME_HUB_GIT_COMMIT "unversioned")
endif()
execute_process(
    COMMAND git rev-parse HEAD
    WORKING_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}"
    RESULT_VARIABLE MACHOME_HUB_GIT_FULL_RESULT
    OUTPUT_VARIABLE MACHOME_HUB_GIT_COMMIT_FULL
    OUTPUT_STRIP_TRAILING_WHITESPACE
    ERROR_QUIET
)
if(NOT MACHOME_HUB_GIT_FULL_RESULT EQUAL 0
   OR NOT MACHOME_HUB_GIT_TRACKED_RESULT EQUAL 0
   OR MACHOME_HUB_GIT_COMMIT_FULL STREQUAL "")
    set(MACHOME_HUB_GIT_COMMIT_FULL "unversioned")
endif()
execute_process(
    COMMAND git status --porcelain --untracked-files=all -- .
    WORKING_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}"
    RESULT_VARIABLE MACHOME_HUB_GIT_STATUS_RESULT
    OUTPUT_VARIABLE MACHOME_HUB_GIT_STATUS
    OUTPUT_STRIP_TRAILING_WHITESPACE
    ERROR_QUIET
)
if(NOT MACHOME_HUB_GIT_STATUS_RESULT EQUAL 0 OR NOT MACHOME_HUB_GIT_STATUS STREQUAL "")
    set(MACHOME_HUB_GIT_DIRTY_JSON true)
    set(MACHOME_HUB_GIT_DIRTY_SUFFIX "-dirty")
else()
    set(MACHOME_HUB_GIT_DIRTY_JSON false)
    set(MACHOME_HUB_GIT_DIRTY_SUFFIX "")
endif()
string(TIMESTAMP MACHOME_HUB_BUILD_ID_TIME "%Y%m%dT%H%M%SZ" UTC)
set(MACHOME_HUB_BUILD_ID
    "${PROJECT_VERSION}-${MACHOME_HUB_GIT_COMMIT}${MACHOME_HUB_GIT_DIRTY_SUFFIX}-${MACHOME_HUB_BUILD_ID_TIME}")
if(CMAKE_BUILD_TYPE STREQUAL "")
    set(MACHOME_HUB_BUILD_TYPE "multi-config")
else()
    set(MACHOME_HUB_BUILD_TYPE "${CMAKE_BUILD_TYPE}")
endif()
set(MACHOME_HUB_OPTIONAL_COMPONENTS "")
string(APPEND MACHOME_HUB_OPTIONAL_COMPONENTS
    ",\n    \"machome-webull-browser-helper\"")
string(APPEND MACHOME_HUB_OPTIONAL_COMPONENTS
    ",\n    \"machome-wind-probe-helper\"")
if(APPLE)
    string(APPEND MACHOME_HUB_OPTIONAL_COMPONENTS
        ",\n    \"machome-wind-tbapi-probe\"")
endif()
if(MACHOME_BUILD_NATIVE_IBKR_BRIDGE)
    string(APPEND MACHOME_HUB_OPTIONAL_COMPONENTS
        ",\n    \"machome-ibkr-bridge\"")
endif()
if(MACHOME_BUILD_PREMIUM_TGW_HELPER)
    string(APPEND MACHOME_HUB_OPTIONAL_COMPONENTS
        ",\n    \"machome-premium-tgw-helper\"")
endif()
find_package(Python3 REQUIRED COMPONENTS Interpreter)
execute_process(COMMAND ${Python3_EXECUTABLE} "${CMAKE_CURRENT_SOURCE_DIR}/scripts/source-manifest.py"
    "${CMAKE_CURRENT_SOURCE_DIR}" "${CMAKE_CURRENT_BINARY_DIR}/source_manifest.json"
    OUTPUT_VARIABLE MACHOME_SOURCE_SHA256 OUTPUT_STRIP_TRAILING_WHITESPACE COMMAND_ERROR_IS_FATAL ANY)
set(MACHOME_UPLOAD_COMPONENT_DIR "" CACHE PATH "Built self-contained Upload business component directory")
if(MACHOME_UPLOAD_COMPONENT_DIR)
    if(NOT EXISTS "${MACHOME_UPLOAD_COMPONENT_DIR}/machome-upload-component")
        message(FATAL_ERROR "Upload component executable missing")
    endif()
    string(APPEND MACHOME_HUB_OPTIONAL_COMPONENTS ",\n    \"machome-upload-component\",\n    \"machome-upload-web\"")
endif()
if(MACHOME_ENABLE_LIVE_QMT_ORDERS)
    set(MACHOME_LIVE_QMT_JSON true)
else()
    set(MACHOME_LIVE_QMT_JSON false)
endif()
configure_file(
    "${CMAKE_CURRENT_SOURCE_DIR}/deploy/build_manifest.json.in"
    "${CMAKE_CURRENT_BINARY_DIR}/build_manifest.json"
    @ONLY
)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_AUTOMOC ON)
set(CMAKE_AUTORCC ON)

set(MACHOME_IBKR_CPP_API_ROOT "" CACHE PATH
    "Path to an official unpacked IBKR TWS C++ API root or cppclient/client directory")
set(MACHOME_IBKR_CPP_API_LIBRARY "" CACHE FILEPATH
    "Optional prebuilt official IBKR TWS C++ API library")
set(MACHOME_IBKR_PROTOBUF_ROOT "" CACHE PATH
    "Optional protobuf prefix matching the generated sources in the IBKR SDK")
set(MACHOME_IBKR_ABSL_ROOT "" CACHE PATH
    "Optional Abseil prefix required by that protobuf build")

include(CTest)
find_package(Qt6 6.5 REQUIRED COMPONENTS Concurrent Core Network WebSockets Widgets Sql Test)

add_library(machome-hub-common STATIC
    src/common/Config.cpp
    src/common/Config.h
    src/common/FrameCodec.cpp
    src/common/FrameCodec.h
    src/common/JsonUtil.cpp
    src/common/JsonUtil.h
    src/common/ModuleBackend.cpp
    src/common/ModuleBackend.h
    src/common/ModuleEngine.h
    src/common/WorkerSchedule.cpp
    src/common/WorkerSchedule.h
)
target_include_directories(machome-hub-common PUBLIC
    ${CMAKE_CURRENT_SOURCE_DIR}/src
    ${CMAKE_CURRENT_SOURCE_DIR}
)
target_link_libraries(machome-hub-common PUBLIC Qt6::Core Qt6::Network)

add_library(machome-hub-modules STATIC
    modules/monitor_sync/FileStore.cpp
    modules/monitor_sync/FileStore.h
    modules/monitor_sync/MonitorSyncEngine.cpp
    modules/monitor_sync/MonitorSyncEngine.h
    modules/bridge/RealtimeClient.cpp
    modules/bridge/RealtimeClient.h
    modules/premium/PremiumClient.cpp
    modules/premium/PremiumClient.h
    modules/premium/engine/PremiumAEngine.cpp
    modules/premium/engine/PremiumAEngine.h
    modules/premium/engine/common/BridgeFrame.cpp
    modules/premium/engine/common/BridgeFrame.h
    modules/premium/engine/common/MarketSchedule.cpp
    modules/premium/engine/common/MarketSchedule.h
    modules/premium/engine/common/MarketTypes.cpp
    modules/premium/engine/common/MarketTypes.h
    modules/premium/engine/common/PersistenceWriter.cpp
    modules/premium/engine/common/PersistenceWriter.h
    modules/premium/engine/common/SignalEngine.cpp
    modules/premium/engine/common/SignalEngine.h
    modules/premium/engine/common/SnapshotParser.cpp
    modules/premium/engine/common/SnapshotParser.h
    modules/premium/engine/server/CoreServer.cpp
    modules/premium/engine/server/CoreServer.h
    modules/premium/engine/server/LegacyL1Server.cpp
    modules/premium/engine/server/LegacyL1Server.h
    modules/premium/engine/server/PushPlusNotifier.cpp
    modules/premium/engine/server/PushPlusNotifier.h
    modules/webull/WebullClient.cpp
    modules/webull/WebullClient.h
    modules/webull/WebullEngine.cpp
    modules/webull/WebullEngine.h
    modules/qmt/QmtClient.cpp
    modules/qmt/QmtClient.h
    modules/qmt/QmtCanonicalJson.cpp
    modules/qmt/QmtCanonicalJson.h
    modules/ibkr/IbkrBridgeCore.cpp
    modules/ibkr/IbkrBridgeCore.h
    modules/redemption/RedemptionCore.cpp
    modules/redemption/RedemptionCore.h
    modules/redemption/RedemptionEngine.cpp
    modules/redemption/RedemptionEngine.h
    modules/upload/UploadCollectors.cpp
    modules/upload/UploadCollectors.h
    modules/upload/UploadEngine.cpp
    modules/upload/UploadEngine.h
    modules/upload/UploadBusinessComponent.cpp
    modules/upload/UploadBusinessComponent.h
)
target_include_directories(machome-hub-modules PUBLIC ${CMAKE_CURRENT_SOURCE_DIR})
target_link_libraries(machome-hub-modules PUBLIC
    machome-hub-common Qt6::Core Qt6::Network Qt6::Sql Qt6::WebSockets Qt6::Concurrent
)
target_compile_definitions(machome-hub-modules PRIVATE
    MACHOME_ENABLE_LIVE_QMT_ORDERS=$<BOOL:${MACHOME_ENABLE_LIVE_QMT_ORDERS}>)
target_compile_definitions(machome-hub-modules PUBLIC
    MACHOME_NATIVE_IBKR_BRIDGE_AVAILABLE=$<BOOL:${MACHOME_BUILD_NATIVE_IBKR_BRIDGE}>)

qt_add_executable(machome-wind-probe-helper
    helpers/wind_probe/main.cpp
)
target_link_libraries(machome-wind-probe-helper PRIVATE Qt6::Core)
if(APPLE)
    set_source_files_properties(helpers/wind_probe/wind_tbapi_probe.c
        PROPERTIES LANGUAGE CXX SKIP_AUTOMOC ON)
    add_library(machome-wind-tbapi-probe SHARED
        helpers/wind_probe/wind_tbapi_probe.c
    )
    set_target_properties(machome-wind-tbapi-probe PROPERTIES
        OUTPUT_NAME "machome_wind_tbapi_probe"
        C_VISIBILITY_PRESET hidden
        POSITION_INDEPENDENT_CODE ON
    )
    add_dependencies(machome-wind-probe-helper machome-wind-tbapi-probe)
endif()

qt_add_executable(machome-webull-browser-helper
    modules/webull/helper/WebullBrowserHelper.cpp
)
target_link_libraries(machome-webull-browser-helper PRIVATE
    Qt6::Core Qt6::Network Qt6::WebSockets
)

if(MACHOME_BUILD_PREMIUM_TGW_HELPER)
    find_package(OpenSSL REQUIRED)
    find_package(Threads REQUIRED)
    find_package(PkgConfig REQUIRED)
    pkg_check_modules(MACHOME_TGW_ZSTD REQUIRED IMPORTED_TARGET libzstd)
    pkg_check_modules(MACHOME_TGW_SIMDJSON REQUIRED IMPORTED_TARGET simdjson)
    add_library(machome-tgw-cpp STATIC
        third_party/tgw_cpp/src/config.cpp
        third_party/tgw_cpp/src/protocol.cpp
        third_party/tgw_cpp/src/session.cpp
        third_party/tgw_cpp/src/websocket.cpp
    )
    target_include_directories(machome-tgw-cpp PUBLIC
        ${CMAKE_CURRENT_SOURCE_DIR}/third_party/tgw_cpp/include
    PRIVATE
        ${CMAKE_CURRENT_SOURCE_DIR}/third_party/tgw_cpp/src)
    target_compile_definitions(machome-tgw-cpp PRIVATE TGW_DEFAULT_CA_FILE="")
    target_link_libraries(machome-tgw-cpp PRIVATE
        OpenSSL::SSL OpenSSL::Crypto
        PkgConfig::MACHOME_TGW_ZSTD PkgConfig::MACHOME_TGW_SIMDJSON
        Threads::Threads)

    qt_add_executable(machome-premium-tgw-helper
        helpers/premium_tgw/main.cpp
        modules/premium/engine/common/BridgeFrame.cpp
        helpers/premium_tgw/NativeTgwAdapter.cpp
        helpers/premium_tgw/NativeTgwAdapter.h
        helpers/premium_tgw/RawEventExtractor.cpp
        helpers/premium_tgw/RawEventExtractor.h
        helpers/premium_tgw/RawEventValidator.cpp
        helpers/premium_tgw/RawEventValidator.h
        helpers/premium_tgw/SubscriptionPlan.cpp
        helpers/premium_tgw/SubscriptionPlan.h
    )
    target_include_directories(machome-premium-tgw-helper PRIVATE
        ${CMAKE_CURRENT_SOURCE_DIR}
        ${CMAKE_CURRENT_SOURCE_DIR}/third_party/tgw_cpp/include)
    target_link_libraries(machome-premium-tgw-helper PRIVATE
        machome-hub-common machome-tgw-cpp Qt6::Core Qt6::Network
        PkgConfig::MACHOME_TGW_SIMDJSON)
endif()

if(MACHOME_BUILD_NATIVE_IBKR_BRIDGE)
    set(MACHOME_IBKR_API_INCLUDE_DIR "")
    foreach(MACHOME_IBKR_API_CANDIDATE
            "${MACHOME_IBKR_CPP_API_ROOT}"
            "${MACHOME_IBKR_CPP_API_ROOT}/client"
            "${MACHOME_IBKR_CPP_API_ROOT}/source/cppclient/client"
            "${MACHOME_IBKR_CPP_API_ROOT}/cppclient/client")
        if(EXISTS "${MACHOME_IBKR_API_CANDIDATE}/EClientSocket.h")
            set(MACHOME_IBKR_API_INCLUDE_DIR "${MACHOME_IBKR_API_CANDIDATE}")
            break()
        endif()
    endforeach()
    if(NOT MACHOME_IBKR_API_INCLUDE_DIR)
        message(FATAL_ERROR
            "MACHOME_BUILD_NATIVE_IBKR_BRIDGE=ON requires MACHOME_IBKR_CPP_API_ROOT "
            "pointing to the official IBKR C++ API")
    endif()
    if(NOT EXISTS "${MACHOME_IBKR_API_INCLUDE_DIR}/DefaultEWrapper.h")
        message(FATAL_ERROR "The selected IBKR SDK is missing DefaultEWrapper.h")
    endif()

    set(MACHOME_IBKR_PROTOBUF_DIR "")
    if(EXISTS "${MACHOME_IBKR_API_INCLUDE_DIR}/protobufUnix")
        set(MACHOME_IBKR_PROTOBUF_DIR "${MACHOME_IBKR_API_INCLUDE_DIR}/protobufUnix")
    elseif(EXISTS "${MACHOME_IBKR_API_INCLUDE_DIR}/protobufWin")
        set(MACHOME_IBKR_PROTOBUF_DIR "${MACHOME_IBKR_API_INCLUDE_DIR}/protobufWin")
    endif()

    set(MACHOME_IBKR_API_SOURCES "")
    set(MACHOME_IBKR_LINK_PROTOBUF OFF)
    set(MACHOME_IBKR_EXTRA_LIBRARIES "")
    if(NOT MACHOME_IBKR_CPP_API_LIBRARY)
        if(MACHOME_IBKR_PROTOBUF_ROOT)
            foreach(MACHOME_IBKR_PROTOBUF_REQUIRED
                    include/google/protobuf/stubs/common.h
                    lib/libprotobuf.dylib
                    bin/protoc)
                if(NOT EXISTS "${MACHOME_IBKR_PROTOBUF_ROOT}/${MACHOME_IBKR_PROTOBUF_REQUIRED}")
                    message(FATAL_ERROR
                        "MACHOME_IBKR_PROTOBUF_ROOT is missing ${MACHOME_IBKR_PROTOBUF_REQUIRED}")
                endif()
            endforeach()
            set(Protobuf_INCLUDE_DIR "${MACHOME_IBKR_PROTOBUF_ROOT}/include"
                CACHE PATH "Pinned protobuf include for IBKR" FORCE)
            set(Protobuf_LIBRARY "${MACHOME_IBKR_PROTOBUF_ROOT}/lib/libprotobuf.dylib"
                CACHE FILEPATH "Pinned protobuf library for IBKR" FORCE)
            set(Protobuf_LIBRARY_RELEASE "${MACHOME_IBKR_PROTOBUF_ROOT}/lib/libprotobuf.dylib"
                CACHE FILEPATH "Pinned protobuf release library for IBKR" FORCE)
            set(Protobuf_PROTOC_EXECUTABLE "${MACHOME_IBKR_PROTOBUF_ROOT}/bin/protoc"
                CACHE FILEPATH "Pinned protoc for IBKR" FORCE)
        endif()
        if(MACHOME_IBKR_ABSL_ROOT)
            if(NOT EXISTS "${MACHOME_IBKR_ABSL_ROOT}/lib/cmake/absl/abslConfig.cmake")
                message(FATAL_ERROR "MACHOME_IBKR_ABSL_ROOT does not contain abslConfig.cmake")
            endif()
            set(absl_DIR "${MACHOME_IBKR_ABSL_ROOT}/lib/cmake/absl"
                CACHE PATH "Pinned Abseil config for IBKR" FORCE)
        endif()
        include(cmake/BuildIbkrApiSources.cmake)
        machome_configure_ibkr_api_sources(
            MACHOME_IBKR_API_SOURCES
            MACHOME_IBKR_LINK_PROTOBUF
            MACHOME_IBKR_EXTRA_LIBRARIES
            "${MACHOME_IBKR_API_INCLUDE_DIR}"
            "${MACHOME_IBKR_PROTOBUF_DIR}")
    endif()

    qt_add_executable(machome-ibkr-bridge
        modules/ibkr/main.cpp
        modules/ibkr/NativeIbkrBridge.cpp
        modules/ibkr/NativeIbkrBridge.h
        ${MACHOME_IBKR_API_SOURCES}
    )
    target_include_directories(machome-ibkr-bridge PRIVATE
        ${CMAKE_CURRENT_SOURCE_DIR}
        "${MACHOME_IBKR_API_INCLUDE_DIR}"
        "${MACHOME_IBKR_PROTOBUF_DIR}"
    )
    # Homebrew Qt exposes its prefix as an interface include directory. Put the
    # protobuf version selected for this exact IBKR generated-code set before
    # that generic prefix, otherwise a newer global protobuf may be selected.
    # FindProtobuf keeps Protobuf_INCLUDE_DIR in the CMake cache even though
    # machome_configure_ibkr_api_sources() is a function and its normal
    # Protobuf_INCLUDE_DIRS variable is scoped locally. Prefer the cache value,
    # then fall back to the imported target for config-package installations.
    set(MACHOME_IBKR_PROTOBUF_PRIMARY_INCLUDE "${Protobuf_INCLUDE_DIR}")
    if(NOT MACHOME_IBKR_PROTOBUF_PRIMARY_INCLUDE AND TARGET protobuf::libprotobuf)
        get_target_property(MACHOME_IBKR_PROTOBUF_TARGET_INCLUDES
            protobuf::libprotobuf INTERFACE_INCLUDE_DIRECTORIES)
        foreach(MACHOME_IBKR_PROTOBUF_TARGET_INCLUDE IN LISTS MACHOME_IBKR_PROTOBUF_TARGET_INCLUDES)
            if(EXISTS "${MACHOME_IBKR_PROTOBUF_TARGET_INCLUDE}/google/protobuf")
                set(MACHOME_IBKR_PROTOBUF_PRIMARY_INCLUDE
                    "${MACHOME_IBKR_PROTOBUF_TARGET_INCLUDE}")
                break()
            endif()
        endforeach()
    endif()
    if(MACHOME_IBKR_PROTOBUF_PRIMARY_INCLUDE)
        set(MACHOME_IBKR_PROTOBUF_FORWARD_ROOT
            "${CMAKE_CURRENT_BINARY_DIR}/machome-ibkr-protobuf-include")
        file(MAKE_DIRECTORY "${MACHOME_IBKR_PROTOBUF_FORWARD_ROOT}/google")
        file(CREATE_LINK
            "${MACHOME_IBKR_PROTOBUF_PRIMARY_INCLUDE}/google/protobuf"
            "${MACHOME_IBKR_PROTOBUF_FORWARD_ROOT}/google/protobuf"
            SYMBOLIC COPY_ON_ERROR)
        target_include_directories(machome-ibkr-bridge BEFORE PRIVATE
            "${MACHOME_IBKR_PROTOBUF_FORWARD_ROOT}")
    endif()
    if(TARGET absl::base)
        get_target_property(MACHOME_IBKR_ABSL_INCLUDE_DIRS
            absl::base INTERFACE_INCLUDE_DIRECTORIES)
        if(MACHOME_IBKR_ABSL_INCLUDE_DIRS)
            target_include_directories(machome-ibkr-bridge BEFORE PRIVATE
                ${MACHOME_IBKR_ABSL_INCLUDE_DIRS})
        endif()
    endif()
    target_link_libraries(machome-ibkr-bridge PRIVATE
        machome-hub-modules Qt6::Core Qt6::Network
    )
    if(MACHOME_IBKR_CPP_API_LIBRARY)
        target_link_libraries(machome-ibkr-bridge PRIVATE "${MACHOME_IBKR_CPP_API_LIBRARY}")
    endif()
    if(MACHOME_IBKR_LINK_PROTOBUF)
        target_link_libraries(machome-ibkr-bridge PRIVATE protobuf::libprotobuf)
        if(APPLE)
            get_target_property(MACHOME_IBKR_PROTOBUF_LIBRARY
                protobuf::libprotobuf IMPORTED_LOCATION)
            if(NOT MACHOME_IBKR_PROTOBUF_LIBRARY)
                get_target_property(MACHOME_IBKR_PROTOBUF_LIBRARY
                    protobuf::libprotobuf IMPORTED_LOCATION_RELEASE)
            endif()
            if(MACHOME_IBKR_PROTOBUF_LIBRARY)
                # A copied Homebrew protobuf retains the Cellar's absolute
                # install name. Point the bridge at the selected compatible
                # copy so it runs from the build tree and macdeployqt can find
                # and bundle it later.
                add_custom_command(TARGET machome-ibkr-bridge POST_BUILD
                    COMMAND ${CMAKE_COMMAND}
                        "-DMACHOME_EXECUTABLE=$<TARGET_FILE:machome-ibkr-bridge>"
                        "-DMACHOME_DYLIB=${MACHOME_IBKR_PROTOBUF_LIBRARY}"
                        -P "${CMAKE_CURRENT_SOURCE_DIR}/cmake/FixDylibReference.cmake"
                    VERBATIM)
            endif()
        endif()
    endif()
    if(MACHOME_IBKR_EXTRA_LIBRARIES)
        target_link_libraries(machome-ibkr-bridge PRIVATE ${MACHOME_IBKR_EXTRA_LIBRARIES})
    endif()
    target_compile_definitions(machome-ibkr-bridge PRIVATE MACHOME_NATIVE_IBKR=1)
endif()
add_executable(machome-hub-agent
    src/agent/main.cpp
    src/agent/AuditWriter.cpp
    src/agent/AuditWriter.h
    src/agent/CommandJournal.cpp
    src/agent/CommandJournal.h
    src/agent/HubAgent.cpp
    src/agent/HubAgent.h
    src/agent/LifecycleController.cpp
    src/agent/LifecycleController.h
    src/agent/ModuleWorker.cpp
    src/agent/ModuleWorker.h
)
target_link_libraries(machome-hub-agent PRIVATE
    machome-hub-common machome-hub-modules Qt6::Core Qt6::Network Qt6::Sql
)
target_compile_definitions(machome-hub-agent PRIVATE
    MACHOME_HUB_BUILD_TIME="${MACHOME_HUB_BUILD_TIME}"
    MACHOME_HUB_GIT_COMMIT="${MACHOME_HUB_GIT_COMMIT}"
)

qt_add_executable(machome-hub-ui MACOSX_BUNDLE
    src/ui/main.cpp
    src/ui/HubClient.cpp
    src/ui/HubClient.h
    src/ui/MainWindow.cpp
    src/ui/MainWindow.h
    src/ui/AlertController.cpp
    src/ui/AlertController.h
    src/ui/MonitorSyncPage.cpp
    src/ui/MonitorSyncPage.h
    src/ui/ModulePages.cpp
    src/ui/UiText.cpp
    src/ui/UiText.h
    src/ui/ServiceOverview.cpp
    src/ui/ServiceOverview.h
    src/ui/ModulePages.h
    src/ui/PremiumHistory.cpp
    src/ui/PremiumHistory.h
)
target_link_libraries(machome-hub-ui PRIVATE
    machome-hub-common Qt6::Concurrent Qt6::Core Qt6::Network Qt6::Widgets
)
set_target_properties(machome-hub-ui PROPERTIES
    OUTPUT_NAME "Machome 四合一运行中心"
    MACOSX_BUNDLE_GUI_IDENTIFIER "com.ellis.machome.operations-hub"
    MACOSX_BUNDLE_BUNDLE_NAME "Machome 五合一运行中心"
    MACOSX_BUNDLE_SHORT_VERSION_STRING "${PROJECT_VERSION}"
    MACOSX_BUNDLE_BUNDLE_VERSION "${PROJECT_VERSION}"
)
add_dependencies(machome-hub-ui machome-hub-agent)
add_dependencies(machome-hub-ui machome-webull-browser-helper)
add_dependencies(machome-hub-ui machome-wind-probe-helper)
if(APPLE)
    add_dependencies(machome-hub-ui machome-wind-tbapi-probe)
endif()
add_custom_command(TARGET machome-hub-ui POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E make_directory
            "$<TARGET_BUNDLE_DIR:machome-hub-ui>/Contents/Resources/config"
            "$<TARGET_BUNDLE_DIR:machome-hub-ui>/Contents/Resources/contracts"
            "$<TARGET_BUNDLE_DIR:machome-hub-ui>/Contents/Helpers"
    COMMAND ${CMAKE_COMMAND} -E remove_directory
            "$<TARGET_BUNDLE_DIR:machome-hub-ui>/Contents/Resources/migration"
    COMMAND ${CMAKE_COMMAND} -E copy_if_different
            "$<TARGET_FILE:machome-hub-agent>"
            "$<TARGET_BUNDLE_DIR:machome-hub-ui>/Contents/MacOS/machome-hub-agent"
    COMMAND ${CMAKE_COMMAND} -E copy_if_different
            "${CMAKE_CURRENT_SOURCE_DIR}/../config/modules.example.json"
            "$<TARGET_BUNDLE_DIR:machome-hub-ui>/Contents/Resources/config/modules.example.json"
    COMMAND ${CMAKE_COMMAND} -E copy_if_different
            "${CMAKE_CURRENT_SOURCE_DIR}/../config/redemption-abi-manifest.example.json"
            "$<TARGET_BUNDLE_DIR:machome-hub-ui>/Contents/Resources/config/redemption-abi-manifest.example.json"
    COMMAND ${CMAKE_COMMAND} -E copy_directory
            "${CMAKE_CURRENT_SOURCE_DIR}/../contracts"
            "$<TARGET_BUNDLE_DIR:machome-hub-ui>/Contents/Resources/contracts"
    COMMAND ${CMAKE_COMMAND} -E copy_if_different
            "$<TARGET_FILE:machome-webull-browser-helper>"
            "$<TARGET_BUNDLE_DIR:machome-hub-ui>/Contents/Helpers/machome-webull-browser-helper"
    COMMAND ${CMAKE_COMMAND} -E copy_if_different
            "$<TARGET_FILE:machome-wind-probe-helper>"
            "$<TARGET_BUNDLE_DIR:machome-hub-ui>/Contents/Helpers/machome-wind-probe-helper"
    COMMAND ${CMAKE_COMMAND} -E copy_if_different
            "${CMAKE_CURRENT_BINARY_DIR}/build_manifest.json"
            "$<TARGET_BUNDLE_DIR:machome-hub-ui>/Contents/Resources/build_manifest.json"
    VERBATIM
)
if(APPLE)
    add_custom_command(TARGET machome-hub-ui POST_BUILD
        COMMAND ${CMAKE_COMMAND} -E copy_if_different
                "$<TARGET_FILE:machome-wind-tbapi-probe>"
                "$<TARGET_BUNDLE_DIR:machome-hub-ui>/Contents/Helpers/libmachome_wind_tbapi_probe.dylib"
        VERBATIM
    )
endif()
if(MACHOME_BUILD_NATIVE_IBKR_BRIDGE)
    add_dependencies(machome-hub-ui machome-ibkr-bridge)
    add_custom_command(TARGET machome-hub-ui POST_BUILD
        COMMAND ${CMAKE_COMMAND} -E copy_if_different
                "$<TARGET_FILE:machome-ibkr-bridge>"
                "$<TARGET_BUNDLE_DIR:machome-hub-ui>/Contents/MacOS/machome-ibkr-bridge"
        COMMAND ${CMAKE_COMMAND} -E copy_if_different
                "${CMAKE_CURRENT_SOURCE_DIR}/../config/ibkr-native-bridge.example.json"
                "$<TARGET_BUNDLE_DIR:machome-hub-ui>/Contents/Resources/config/ibkr-native-bridge.example.json"
        COMMAND ${CMAKE_COMMAND} -E copy_if_different
                "${CMAKE_CURRENT_SOURCE_DIR}/../config/ibkr-native-bridge.machome-production.json"
                "$<TARGET_BUNDLE_DIR:machome-hub-ui>/Contents/Resources/config/ibkr-native-bridge.machome-production.json"
        VERBATIM
    )
endif()
if(MACHOME_BUILD_PREMIUM_TGW_HELPER)
    add_dependencies(machome-hub-ui machome-premium-tgw-helper)
    add_custom_command(TARGET machome-hub-ui POST_BUILD
        COMMAND ${CMAKE_COMMAND} -E make_directory
                "$<TARGET_BUNDLE_DIR:machome-hub-ui>/Contents/Helpers"
                "$<TARGET_BUNDLE_DIR:machome-hub-ui>/Contents/Resources/premium"
        COMMAND ${CMAKE_COMMAND} -E copy_if_different
                "$<TARGET_FILE:machome-premium-tgw-helper>"
                "$<TARGET_BUNDLE_DIR:machome-hub-ui>/Contents/Helpers/machome-premium-tgw-helper"
        COMMAND ${CMAKE_COMMAND} -E copy_if_different
                "${CMAKE_CURRENT_SOURCE_DIR}/helpers/premium_tgw/vendor-dgw-ca.crt"
                "$<TARGET_BUNDLE_DIR:machome-hub-ui>/Contents/Resources/premium/vendor-dgw-ca.crt"
        VERBATIM)
endif()

# POST_BUILD does not run when only a configure-time manifest or optional
# bridge asset changes and the UI executable itself is already up to date.
# Keep bundle metadata/assets synchronized on every ordinary ALL build so a
# reused build directory cannot package or test stale component identity.
add_custom_target(machome-hub-bundle-metadata ALL
    COMMAND ${CMAKE_COMMAND} -E make_directory
            "$<TARGET_BUNDLE_DIR:machome-hub-ui>/Contents/Resources"
            "$<TARGET_BUNDLE_DIR:machome-hub-ui>/Contents/Helpers"
    COMMAND ${CMAKE_COMMAND} -E remove_directory
            "$<TARGET_BUNDLE_DIR:machome-hub-ui>/Contents/Resources/migration"
    COMMAND ${CMAKE_COMMAND} -E copy_if_different
            "${CMAKE_CURRENT_BINARY_DIR}/build_manifest.json"
            "$<TARGET_BUNDLE_DIR:machome-hub-ui>/Contents/Resources/build_manifest.json"
    COMMAND ${CMAKE_COMMAND} -E copy_if_different
            "$<TARGET_FILE:machome-webull-browser-helper>"
            "$<TARGET_BUNDLE_DIR:machome-hub-ui>/Contents/Helpers/machome-webull-browser-helper"
    DEPENDS machome-hub-ui machome-webull-browser-helper
    VERBATIM
)
if(MACHOME_BUILD_PREMIUM_TGW_HELPER)
    add_custom_target(machome-hub-bundle-premium-assets ALL
        COMMAND ${CMAKE_COMMAND} -E make_directory
                "$<TARGET_BUNDLE_DIR:machome-hub-ui>/Contents/Helpers"
                "$<TARGET_BUNDLE_DIR:machome-hub-ui>/Contents/Resources/premium"
        COMMAND ${CMAKE_COMMAND} -E copy_if_different
                "$<TARGET_FILE:machome-premium-tgw-helper>"
                "$<TARGET_BUNDLE_DIR:machome-hub-ui>/Contents/Helpers/machome-premium-tgw-helper"
        COMMAND ${CMAKE_COMMAND} -E copy_if_different
                "${CMAKE_CURRENT_SOURCE_DIR}/helpers/premium_tgw/vendor-dgw-ca.crt"
                "$<TARGET_BUNDLE_DIR:machome-hub-ui>/Contents/Resources/premium/vendor-dgw-ca.crt"
        DEPENDS machome-hub-ui machome-premium-tgw-helper
        VERBATIM
    )
endif()
if(MACHOME_BUILD_NATIVE_IBKR_BRIDGE)
    add_custom_target(machome-hub-bundle-native-assets ALL
        COMMAND ${CMAKE_COMMAND} -E make_directory
                "$<TARGET_BUNDLE_DIR:machome-hub-ui>/Contents/MacOS"
                "$<TARGET_BUNDLE_DIR:machome-hub-ui>/Contents/Resources/config"
        COMMAND ${CMAKE_COMMAND} -E copy_if_different
                "$<TARGET_FILE:machome-ibkr-bridge>"
                "$<TARGET_BUNDLE_DIR:machome-hub-ui>/Contents/MacOS/machome-ibkr-bridge"
        COMMAND ${CMAKE_COMMAND} -E copy_if_different
                "${CMAKE_CURRENT_SOURCE_DIR}/../config/ibkr-native-bridge.example.json"
                "$<TARGET_BUNDLE_DIR:machome-hub-ui>/Contents/Resources/config/ibkr-native-bridge.example.json"
        COMMAND ${CMAKE_COMMAND} -E copy_if_different
                "${CMAKE_CURRENT_SOURCE_DIR}/../config/ibkr-native-bridge.machome-production.json"
                "$<TARGET_BUNDLE_DIR:machome-hub-ui>/Contents/Resources/config/ibkr-native-bridge.machome-production.json"
        DEPENDS machome-hub-ui machome-ibkr-bridge
        VERBATIM
    )
endif()

if(BUILD_TESTING)
    find_package(Python3 COMPONENTS Interpreter REQUIRED)

    qt_add_executable(tst-hub-common
        tests/tst_common.cpp
    )
    target_link_libraries(tst-hub-common PRIVATE
        machome-hub-common Qt6::Test
    )
    add_test(NAME hub-common COMMAND tst-hub-common)

    qt_add_executable(tst-premium-client
        tests/tst_premium_client.cpp
    )
    target_link_libraries(tst-premium-client PRIVATE
        machome-hub-modules Qt6::Test
    )
    add_test(NAME premium-client COMMAND tst-premium-client)

    qt_add_executable(tst-premium-engine-domain
        tests/tst_premium_engine_domain.cpp
    )
    target_link_libraries(tst-premium-engine-domain PRIVATE
        machome-hub-modules Qt6::Test
    )
    target_compile_definitions(tst-premium-engine-domain PRIVATE
        MACHOME_TEST_SOURCE_DIR="${CMAKE_CURRENT_SOURCE_DIR}"
    )
    add_test(NAME premium-engine-domain COMMAND tst-premium-engine-domain)

    qt_add_executable(tst-premium-engine-l1
        tests/tst_premium_engine_l1.cpp
    )
    target_link_libraries(tst-premium-engine-l1 PRIVATE
        machome-hub-modules Qt6::Test
    )
    add_test(NAME premium-engine-l1 COMMAND tst-premium-engine-l1)

    qt_add_executable(tst-premium-a-engine
        tests/tst_premium_a_engine.cpp
    )
    target_link_libraries(tst-premium-a-engine PRIVATE
        machome-hub-modules Qt6::Test
    )
    add_test(NAME premium-a-engine COMMAND tst-premium-a-engine)

    qt_add_executable(tst-premium-pushplus
        tests/tst_premium_pushplus.cpp
    )
    target_link_libraries(tst-premium-pushplus PRIVATE
        machome-hub-modules Qt6::Test
    )
    add_test(NAME premium-pushplus COMMAND tst-premium-pushplus)

    add_test(NAME premium-runtime-isolation
        COMMAND ${Python3_EXECUTABLE}
                "${CMAKE_CURRENT_SOURCE_DIR}/tests/test_premium_runtime_isolation.py"
                "${CMAKE_CURRENT_SOURCE_DIR}"
    )
    if(APPLE)
        add_test(NAME premium-baseline-invisible
            COMMAND ${Python3_EXECUTABLE}
                ${CMAKE_CURRENT_SOURCE_DIR}/tests/run_upload_baseline_invisible.py
                --test-binary $<TARGET_FILE:tst-premium-a-engine>
                --deny-root /Users/ellis/工具程序开发/溢价率拉升监控cpp
        )
        set_tests_properties(premium-baseline-invisible PROPERTIES TIMEOUT 20)
    endif()

    qt_add_executable(tst-webull-status
        tests/tst_webull_status.cpp
    )
    target_link_libraries(tst-webull-status PRIVATE
        machome-hub-modules Qt6::Test
    )
    add_test(NAME webull-status COMMAND tst-webull-status)

    qt_add_executable(tst-webull-engine
        tests/tst_webull_engine.cpp
    )
    target_link_libraries(tst-webull-engine PRIVATE
        machome-hub-modules Qt6::Test
    )
    target_compile_definitions(tst-webull-engine PRIVATE
        MACHOME_TEST_SOURCE_DIR="${CMAKE_CURRENT_SOURCE_DIR}"
    )
    add_test(NAME webull-engine COMMAND tst-webull-engine)

    add_test(NAME webull-browser-helper
        COMMAND ${CMAKE_COMMAND} -E env PYTHONDONTWRITEBYTECODE=1
            ${Python3_EXECUTABLE}
            ${CMAKE_CURRENT_SOURCE_DIR}/modules/webull/helper/test_helper.py
            $<TARGET_FILE:machome-webull-browser-helper>
    )
    set_tests_properties(webull-browser-helper PROPERTIES TIMEOUT 10)
    if(APPLE)
        add_test(NAME webull-baseline-invisible
            COMMAND ${Python3_EXECUTABLE}
                ${CMAKE_CURRENT_SOURCE_DIR}/tests/run_upload_baseline_invisible.py
                --test-binary $<TARGET_FILE:tst-webull-engine>
                --deny-root /Users/ellis/Desktop/ETF交割/WebullData
        )
        set_tests_properties(webull-baseline-invisible PROPERTIES TIMEOUT 20)
    endif()

    qt_add_executable(tst-worker-schedule
        tests/tst_worker_schedule.cpp
    )
    target_link_libraries(tst-worker-schedule PRIVATE
        machome-hub-common Qt6::Test
    )
    add_test(NAME worker-schedule COMMAND tst-worker-schedule)

    qt_add_executable(tst-ibkr-bridge-core
        tests/tst_ibkr_bridge_core.cpp
    )
    target_link_libraries(tst-ibkr-bridge-core PRIVATE
        machome-hub-modules Qt6::Test
    )
    add_test(NAME ibkr-bridge-core COMMAND tst-ibkr-bridge-core)

    qt_add_executable(tst-upload-engine
        tests/tst_upload_engine.cpp
    )
    target_link_libraries(tst-upload-engine PRIVATE
        machome-hub-modules Qt6::Sql Qt6::Test
    )
    target_compile_definitions(tst-upload-engine PRIVATE
        MACHOME_TEST_SOURCE_DIR="${CMAKE_CURRENT_SOURCE_DIR}"
    )
    add_test(NAME upload-engine COMMAND tst-upload-engine)
    if(APPLE)
        add_test(NAME upload-baseline-invisible
            COMMAND ${Python3_EXECUTABLE}
                ${CMAKE_CURRENT_SOURCE_DIR}/tests/run_upload_baseline_invisible.py
                --test-binary $<TARGET_FILE:tst-upload-engine>
                --deny-root /Users/ellis/newnavnav
        )
        set_tests_properties(upload-baseline-invisible PROPERTIES TIMEOUT 20)
    endif()

    qt_add_executable(tst-redemption-engine
        tests/tst_redemption_engine.cpp
    )
    target_link_libraries(tst-redemption-engine PRIVATE
        machome-hub-modules Qt6::Sql Qt6::Test
    )
    target_compile_definitions(tst-redemption-engine PRIVATE
        MACHOME_TEST_SOURCE_DIR="${CMAKE_CURRENT_SOURCE_DIR}"
        MACHOME_TEST_WIND_HELPER="$<TARGET_FILE:machome-wind-probe-helper>"
    )
    add_dependencies(tst-redemption-engine machome-wind-probe-helper)
    add_test(NAME redemption-engine COMMAND tst-redemption-engine)
    if(APPLE)
        add_test(NAME redemption-baseline-invisible
            COMMAND ${Python3_EXECUTABLE}
                ${CMAKE_CURRENT_SOURCE_DIR}/tests/run_upload_baseline_invisible.py
                --test-binary $<TARGET_FILE:tst-redemption-engine>
                --deny-root /Users/ellis/Desktop/ETF交割/实时申购赎回数据
        )
        # Includes multiple real 2-second warmup/ACK timing scenarios.
        set_tests_properties(redemption-baseline-invisible PROPERTIES TIMEOUT 60)
    endif()

    add_test(NAME redemption-wind-helper
        COMMAND ${Python3_EXECUTABLE}
            ${CMAKE_CURRENT_SOURCE_DIR}/tests/test_wind_probe_helper.py
            $<TARGET_FILE:machome-wind-probe-helper>
    )
    set_tests_properties(redemption-wind-helper PROPERTIES TIMEOUT 10)

    qt_add_executable(tst-qmt-contract
        tests/tst_qmt_contract.cpp
    )
    target_link_libraries(tst-qmt-contract PRIVATE
        machome-hub-modules Qt6::Test
    )
    target_compile_definitions(tst-qmt-contract PRIVATE
        MACHOME_TEST_SOURCE_DIR="${CMAKE_CURRENT_SOURCE_DIR}"
    )
    add_test(NAME qmt-contract COMMAND tst-qmt-contract)

    qt_add_executable(tst-command-journal
        tests/tst_command_journal.cpp
        src/agent/CommandJournal.cpp
        src/agent/CommandJournal.h
    )
    target_link_libraries(tst-command-journal PRIVATE
        machome-hub-common Qt6::Sql Qt6::Test
    )
    add_test(NAME command-journal COMMAND tst-command-journal)

    qt_add_executable(tst-audit-writer
        tests/tst_audit_writer.cpp
        src/agent/AuditWriter.cpp
        src/agent/AuditWriter.h
    )
    target_link_libraries(tst-audit-writer PRIVATE
        machome-hub-common Qt6::Sql Qt6::Test
    )
    add_test(NAME audit-writer COMMAND tst-audit-writer)

    qt_add_executable(tst-lifecycle-controller
        tests/tst_lifecycle_controller.cpp
        src/agent/LifecycleController.cpp
        src/agent/LifecycleController.h
    )
    target_link_libraries(tst-lifecycle-controller PRIVATE
        machome-hub-common Qt6::Test
    )
    add_test(NAME lifecycle-controller COMMAND tst-lifecycle-controller)

    add_test(NAME contract-files
        COMMAND ${CMAKE_COMMAND} -E env
            PYTHONDONTWRITEBYTECODE=1
            ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/test_contract_files.py
    )
    set_tests_properties(contract-files PROPERTIES
        WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
        TIMEOUT 10
    )

    add_test(NAME ibkr-shared-client-unit
        COMMAND ${CMAKE_COMMAND} -E env
            PYTHONDONTWRITEBYTECODE=1
            ${Python3_EXECUTABLE}
                ${CMAKE_CURRENT_SOURCE_DIR}/../migration/newnavnav-shared-ibkr/tests/test_shared_client.py
    )
    set_tests_properties(ibkr-shared-client-unit PROPERTIES TIMEOUT 10)

    if(MACHOME_BUILD_NATIVE_IBKR_BRIDGE)
        add_test(NAME ibkr-shared-client-integration
            COMMAND ${CMAKE_COMMAND} -E env
                PYTHONDONTWRITEBYTECODE=1
                ${Python3_EXECUTABLE}
                    ${CMAKE_CURRENT_SOURCE_DIR}/../migration/newnavnav-shared-ibkr/tests/test_shared_client.py
                    --bridge-binary $<TARGET_FILE:machome-ibkr-bridge>
        )
        set_tests_properties(ibkr-shared-client-integration PROPERTIES
            TIMEOUT 15
            RUN_SERIAL TRUE
        )
    endif()

    add_test(NAME owner-handoff
        COMMAND ${CMAKE_COMMAND} -E env
            PYTHONDONTWRITEBYTECODE=1
            ${Python3_EXECUTABLE}
                ${CMAKE_CURRENT_SOURCE_DIR}/tests/test_owner_handoff.py -v
    )
    set_tests_properties(owner-handoff PROPERTIES
        WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
        TIMEOUT 15
    )

    add_test(NAME build-manifest
        COMMAND ${Python3_EXECUTABLE}
            ${CMAKE_CURRENT_SOURCE_DIR}/tests/test_build_manifest.py
            "$<TARGET_BUNDLE_DIR:machome-hub-ui>/Contents/Resources/build_manifest.json"
    )

    add_test(NAME upload-runtime-isolation
        COMMAND ${CMAKE_COMMAND} -E env
            PYTHONDONTWRITEBYTECODE=1
            ${Python3_EXECUTABLE}
                ${CMAKE_CURRENT_SOURCE_DIR}/tests/test_upload_runtime_isolation.py
                ${CMAKE_CURRENT_SOURCE_DIR}/../tools/check_upload_runtime_isolation.py
                "$<TARGET_BUNDLE_DIR:machome-hub-ui>"
    )
    set_tests_properties(upload-runtime-isolation PROPERTIES TIMEOUT 20)

    add_test(NAME webull-runtime-isolation
        COMMAND ${CMAKE_COMMAND} -E env PYTHONDONTWRITEBYTECODE=1
            ${Python3_EXECUTABLE}
            ${CMAKE_CURRENT_SOURCE_DIR}/tests/test_webull_runtime_isolation.py
            ${CMAKE_CURRENT_SOURCE_DIR}/../tools/check_webull_runtime_isolation.py
            "$<TARGET_BUNDLE_DIR:machome-hub-ui>"
    )
    set_tests_properties(webull-runtime-isolation PROPERTIES TIMEOUT 20)

    add_test(NAME release-script-dry-run
        COMMAND ${Python3_EXECUTABLE}
            ${CMAKE_CURRENT_SOURCE_DIR}/tests/test_build_release_dry_run.py
    )
    set_tests_properties(release-script-dry-run PROPERTIES
        WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
        TIMEOUT 10
    )

    qt_add_executable(tst-module-pages
        tests/tst_module_pages.cpp
        src/ui/AlertController.cpp
        src/ui/AlertController.h
        src/ui/MonitorSyncPage.cpp src/ui/MonitorSyncPage.h
        src/ui/ModulePages.cpp
        src/ui/UiText.cpp
        src/ui/UiText.h
        src/ui/ServiceOverview.cpp
        src/ui/ServiceOverview.h
        src/ui/ModulePages.h
        src/ui/PremiumHistory.cpp
        src/ui/PremiumHistory.h
    )
    target_link_libraries(tst-module-pages PRIVATE
        machome-hub-common Qt6::Concurrent Qt6::Test Qt6::Widgets
    )
    qt_add_executable(hub-ui-preview
        tests/ui_preview.cpp
        src/ui/MainWindow.cpp src/ui/MainWindow.h
        src/ui/HubClient.cpp src/ui/HubClient.h
        src/ui/AlertController.cpp src/ui/AlertController.h
        src/ui/MonitorSyncPage.cpp src/ui/MonitorSyncPage.h
        src/ui/ModulePages.cpp src/ui/ModulePages.h
        src/ui/ServiceOverview.cpp src/ui/ServiceOverview.h
        src/ui/UiText.cpp src/ui/UiText.h
        src/ui/PremiumHistory.cpp src/ui/PremiumHistory.h)
    target_link_libraries(hub-ui-preview PRIVATE machome-hub-common Qt6::Concurrent Qt6::Widgets)
    add_test(NAME module-pages COMMAND tst-module-pages)
    set_tests_properties(module-pages PROPERTIES
        ENVIRONMENT "QT_QPA_PLATFORM=offscreen"
    )

    add_test(NAME full-smoke
        COMMAND ${CMAKE_COMMAND} -E env
            MACHOME_HUB_BUILD_DIR=${CMAKE_CURRENT_BINARY_DIR}
            QT_QPA_PLATFORM=offscreen
            ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/run_full_smoke.py
    )
    set_tests_properties(full-smoke PROPERTIES
        LABELS integration
        RUN_SERIAL TRUE
        TIMEOUT 60
    )
endif()

if(APPLE)
    add_custom_target(machome-hub-bundle-sounds ALL
        COMMAND ${CMAKE_COMMAND} -E copy_directory
            "${CMAKE_CURRENT_SOURCE_DIR}/resources/sounds"
            "$<TARGET_BUNDLE_DIR:machome-hub-ui>/Contents/Resources/sounds"
        DEPENDS machome-hub-ui)
endif()

if(BUILD_TESTING)
    add_test(NAME four-native-acceptance
        COMMAND ${CMAKE_COMMAND} -E env MACHOME_HUB_BUILD_DIR=${CMAKE_CURRENT_BINARY_DIR}
            ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/run_native_acceptance.py)
    set_tests_properties(four-native-acceptance PROPERTIES LABELS integration TIMEOUT 60 RUN_SERIAL TRUE)
endif()

if(APPLE AND MACHOME_UPLOAD_COMPONENT_DIR)
    add_custom_target(machome-hub-bundle-upload ALL
        COMMAND ${CMAKE_COMMAND} -E copy_directory "${MACHOME_UPLOAD_COMPONENT_DIR}"
            "$<TARGET_BUNDLE_DIR:machome-hub-ui>/Contents/Resources/upload/machome-upload-component"
        DEPENDS machome-hub-ui)
endif()

if(BUILD_TESTING AND APPLE AND MACHOME_UPLOAD_COMPONENT_DIR)
    add_test(NAME bundled-upload-acceptance
        COMMAND ${CMAKE_COMMAND} -E env MACHOME_HUB_BUILD_DIR=${CMAKE_CURRENT_BINARY_DIR}
            "MACHOME_HUB_AGENT_BINARY=$<TARGET_BUNDLE_DIR:machome-hub-ui>/Contents/MacOS/machome-hub-agent"
            ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/run_native_acceptance.py --bundled-upload)
    set_tests_properties(bundled-upload-acceptance PROPERTIES LABELS integration TIMEOUT 90 RUN_SERIAL TRUE)
endif()

if(BUILD_TESTING)
    qt_add_executable(tst-alert-controller tests/tst_alert_controller.cpp src/ui/AlertController.cpp src/ui/AlertController.h)
    target_include_directories(tst-alert-controller PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/src)
    target_link_libraries(tst-alert-controller PRIVATE Qt6::Widgets Qt6::Test)
    add_test(NAME alert-controller COMMAND tst-alert-controller)
    set_tests_properties(alert-controller PROPERTIES ENVIRONMENT "QT_QPA_PLATFORM=offscreen")
    add_test(NAME upload-business-supervisor COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/components/upload/test_supervisor.py)
endif()

if(APPLE)
    add_custom_target(machome-hub-bundle-source-manifest ALL
        COMMAND ${CMAKE_COMMAND} -E copy_if_different "${CMAKE_CURRENT_BINARY_DIR}/source_manifest.json"
            "$<TARGET_BUNDLE_DIR:machome-hub-ui>/Contents/Resources/source_manifest.json"
        DEPENDS machome-hub-ui)
endif()

if(BUILD_TESTING)
    add_executable(monitor-sync-test-server tests/monitor_sync_server.cpp)
    target_link_libraries(monitor-sync-test-server PRIVATE machome-hub-modules Qt6::Concurrent)
endif()

if(APPLE)
    target_link_libraries(machome-hub-modules PUBLIC iconv)
endif()
if(BUILD_TESTING)
    add_executable(tst-monitor-sync tests/tst_monitor_sync.cpp)
    target_link_libraries(tst-monitor-sync PRIVATE machome-hub-modules Qt6::Test Qt6::Concurrent)
    add_test(NAME monitor-sync-file-service COMMAND tst-monitor-sync)
endif()
