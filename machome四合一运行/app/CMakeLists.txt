cmake_minimum_required(VERSION 3.24)

project(machome_operations_hub VERSION 1.0.0 LANGUAGES CXX)

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
)
target_include_directories(machome-hub-common PUBLIC
    ${CMAKE_CURRENT_SOURCE_DIR}/src
    ${CMAKE_CURRENT_SOURCE_DIR}
)
target_link_libraries(machome-hub-common PUBLIC Qt6::Core Qt6::Network)

add_library(machome-hub-modules STATIC
    modules/bridge/UploadClient.cpp
    modules/bridge/UploadClient.h
    modules/bridge/RealtimeClient.cpp
    modules/bridge/RealtimeClient.h
    modules/premium/PremiumClient.cpp
    modules/premium/PremiumClient.h
    modules/webull/WebullClient.cpp
    modules/webull/WebullClient.h
    modules/qmt/QmtClient.cpp
    modules/qmt/QmtClient.h
    modules/qmt/QmtCanonicalJson.cpp
    modules/qmt/QmtCanonicalJson.h
)
target_include_directories(machome-hub-modules PUBLIC ${CMAKE_CURRENT_SOURCE_DIR})
target_link_libraries(machome-hub-modules PUBLIC
    Qt6::Core Qt6::Network Qt6::WebSockets
)

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
    src/ui/ModulePages.cpp
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
    MACOSX_BUNDLE_BUNDLE_NAME "Machome 四合一运行中心"
    MACOSX_BUNDLE_SHORT_VERSION_STRING "${PROJECT_VERSION}"
    MACOSX_BUNDLE_BUNDLE_VERSION "${PROJECT_VERSION}"
)
add_dependencies(machome-hub-ui machome-hub-agent)
add_custom_command(TARGET machome-hub-ui POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E make_directory
            "$<TARGET_BUNDLE_DIR:machome-hub-ui>/Contents/Resources/config"
            "$<TARGET_BUNDLE_DIR:machome-hub-ui>/Contents/Resources/contracts"
            "$<TARGET_BUNDLE_DIR:machome-hub-ui>/Contents/Resources/webull"
    COMMAND ${CMAKE_COMMAND} -E copy_if_different
            "$<TARGET_FILE:machome-hub-agent>"
            "$<TARGET_BUNDLE_DIR:machome-hub-ui>/Contents/MacOS/machome-hub-agent"
    COMMAND ${CMAKE_COMMAND} -E copy_if_different
            "${CMAKE_CURRENT_SOURCE_DIR}/../config/modules.example.json"
            "$<TARGET_BUNDLE_DIR:machome-hub-ui>/Contents/Resources/config/modules.example.json"
    COMMAND ${CMAKE_COMMAND} -E copy_directory
            "${CMAKE_CURRENT_SOURCE_DIR}/../contracts"
            "$<TARGET_BUNDLE_DIR:machome-hub-ui>/Contents/Resources/contracts"
    COMMAND ${CMAKE_COMMAND} -E copy_if_different
            "${CMAKE_CURRENT_SOURCE_DIR}/modules/webull/webull_control_runner.py"
            "$<TARGET_BUNDLE_DIR:machome-hub-ui>/Contents/Resources/webull/webull_control_runner.py"
    COMMAND ${CMAKE_COMMAND} -E copy_if_different
            "${CMAKE_CURRENT_BINARY_DIR}/build_manifest.json"
            "$<TARGET_BUNDLE_DIR:machome-hub-ui>/Contents/Resources/build_manifest.json"
    VERBATIM
)

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

    add_test(NAME webull-control-runner
        COMMAND ${CMAKE_COMMAND} -E env
            PYTHONDONTWRITEBYTECODE=1
            ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/modules/webull/test_webull_control_runner.py
    )
    set_tests_properties(webull-control-runner PROPERTIES
        WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}/modules/webull
        TIMEOUT 15
    )

    add_test(NAME contract-files
        COMMAND ${CMAKE_COMMAND} -E env
            PYTHONDONTWRITEBYTECODE=1
            ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/test_contract_files.py
    )
    set_tests_properties(contract-files PROPERTIES
        WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
        TIMEOUT 10
    )

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
        src/ui/ModulePages.cpp
        src/ui/ModulePages.h
        src/ui/PremiumHistory.cpp
        src/ui/PremiumHistory.h
    )
    target_link_libraries(tst-module-pages PRIVATE
        machome-hub-common Qt6::Concurrent Qt6::Test Qt6::Widgets
    )
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
