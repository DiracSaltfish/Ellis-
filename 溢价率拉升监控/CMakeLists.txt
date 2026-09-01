cmake_minimum_required(VERSION 3.22)

if(APPLE AND NOT CMAKE_OSX_DEPLOYMENT_TARGET)
    set(CMAKE_OSX_DEPLOYMENT_TARGET "26.0" CACHE STRING "Minimum supported macOS version" FORCE)
endif()

project(EtfPremiumMonitor VERSION 0.3.2 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_AUTOMOC ON)
set(CMAKE_AUTOUIC ON)
set(CMAKE_AUTORCC ON)

option(PREMIUM_BUILD_TESTS "Build automated tests" ON)
option(PREMIUM_ENABLE_ZSTD "Persist market partitions as concatenated zstd frames" ON)

find_package(Qt6 6.5 REQUIRED COMPONENTS Core Network WebSockets Widgets Multimedia Concurrent)
if(PREMIUM_BUILD_TESTS)
    find_package(Qt6 6.5 REQUIRED COMPONENTS Test)
    enable_testing()
endif()

set(PREMIUM_ZSTD_FOUND OFF)
if(PREMIUM_ENABLE_ZSTD)
    find_path(ZSTD_INCLUDE_DIR zstd.h HINTS /opt/homebrew/opt/zstd/include)
    find_library(ZSTD_LIBRARY NAMES zstd HINTS /opt/homebrew/opt/zstd/lib)
    if(ZSTD_INCLUDE_DIR AND ZSTD_LIBRARY)
        set(PREMIUM_ZSTD_FOUND ON)
    else()
        message(WARNING "libzstd not found; persistence will use plain JSONL")
    endif()
endif()

add_library(premium_common STATIC
    src/common/BridgeFrame.cpp
    src/common/MarketSchedule.cpp
    src/common/MarketTypes.cpp
    src/common/PersistenceWriter.cpp
    src/common/SignalEngine.cpp
    src/common/SnapshotParser.cpp
)
target_include_directories(premium_common PUBLIC src)
target_link_libraries(premium_common PUBLIC Qt6::Core Qt6::Network)
target_compile_definitions(premium_common PUBLIC QT_NO_KEYWORDS)
if(PREMIUM_ZSTD_FOUND)
    target_include_directories(premium_common PRIVATE "${ZSTD_INCLUDE_DIR}")
    target_link_libraries(premium_common PRIVATE "${ZSTD_LIBRARY}")
    target_compile_definitions(premium_common PRIVATE PREMIUM_HAS_ZSTD=1)
endif()

add_executable(etf-premium-core
    src/server/CoreServer.h
    src/server/CoreServer.cpp
    src/server/LegacyL1Server.h
    src/server/LegacyL1Server.cpp
    src/server/main.cpp
)
target_include_directories(etf-premium-core PRIVATE src)
target_link_libraries(etf-premium-core PRIVATE premium_common Qt6::WebSockets)

add_executable(etf-premium-console MACOSX_BUNDLE
    src/console/ConsoleWindow.h
    src/console/ConsoleWindow.cpp
    src/console/main.cpp
)
target_include_directories(etf-premium-console PRIVATE src)
target_link_libraries(etf-premium-console PRIVATE Qt6::Widgets Qt6::Network Qt6::WebSockets Qt6::Concurrent)
set_target_properties(etf-premium-console PROPERTIES
    MACOSX_BUNDLE_GUI_IDENTIFIER "com.ellis.etf-premium.console"
    MACOSX_BUNDLE_BUNDLE_NAME "ETF溢价监控服务端"
    MACOSX_BUNDLE_SHORT_VERSION_STRING "${PROJECT_VERSION}"
    MACOSX_BUNDLE_BUNDLE_VERSION "${PROJECT_VERSION}"
    MACOSX_BUNDLE_INFO_PLIST "${CMAKE_CURRENT_SOURCE_DIR}/cmake/MacOSBundleInfo.plist.in"
)

add_executable(etf-premium-client MACOSX_BUNDLE
    src/client/ClientSettings.h
    src/client/ClientSettings.cpp
    src/client/DetailDialog.h
    src/client/DetailDialog.cpp
    src/client/MonitorWindow.cpp
    src/client/MonitorWindow.h
    src/client/QmtClient.cpp
    src/client/QmtClient.h
    src/client/main.cpp
)
target_include_directories(etf-premium-client PRIVATE src)
target_link_libraries(etf-premium-client PRIVATE premium_common Qt6::Widgets Qt6::WebSockets Qt6::Multimedia)
set_target_properties(etf-premium-client PROPERTIES
    MACOSX_BUNDLE_GUI_IDENTIFIER "com.ellis.etf-premium.client"
    MACOSX_BUNDLE_BUNDLE_NAME "ETF溢价率拉升监控"
    MACOSX_BUNDLE_SHORT_VERSION_STRING "${PROJECT_VERSION}"
    MACOSX_BUNDLE_BUNDLE_VERSION "${PROJECT_VERSION}"
    MACOSX_BUNDLE_INFO_PLIST "${CMAKE_CURRENT_SOURCE_DIR}/cmake/MacOSBundleInfo.plist.in"
)

if(PREMIUM_BUILD_TESTS)
    add_executable(premium-domain-tests
        tests/test_domain.cpp
    )
    target_include_directories(premium-domain-tests PRIVATE src)
    target_link_libraries(premium-domain-tests PRIVATE premium_common Qt6::Test)
    add_test(NAME premium-domain-tests COMMAND premium-domain-tests)

    add_executable(premium-qmt-client-tests
        src/client/ClientSettings.h
        src/client/ClientSettings.cpp
        src/client/QmtClient.h
        src/client/QmtClient.cpp
        tests/test_qmt_client.cpp
    )
    target_include_directories(premium-qmt-client-tests PRIVATE src)
    target_link_libraries(premium-qmt-client-tests PRIVATE premium_common Qt6::Test)
    add_test(NAME premium-qmt-client-tests COMMAND premium-qmt-client-tests)

    add_executable(premium-client-ui-tests
        src/client/ClientSettings.h
        src/client/ClientSettings.cpp
        src/client/DetailDialog.h
        src/client/DetailDialog.cpp
        src/client/MonitorWindow.h
        src/client/MonitorWindow.cpp
        src/client/QmtClient.h
        src/client/QmtClient.cpp
        tests/test_client_ui.cpp
    )
    target_include_directories(premium-client-ui-tests PRIVATE src)
    target_link_libraries(premium-client-ui-tests PRIVATE premium_common Qt6::Test Qt6::Widgets Qt6::WebSockets Qt6::Multimedia)
    add_test(NAME premium-client-ui-tests COMMAND premium-client-ui-tests)

    add_executable(premium-console-ui-tests
        src/console/ConsoleWindow.h
        src/console/ConsoleWindow.cpp
        tests/test_console_ui.cpp
    )
    target_include_directories(premium-console-ui-tests PRIVATE src)
    target_link_libraries(premium-console-ui-tests PRIVATE Qt6::Test Qt6::Widgets Qt6::Network Qt6::WebSockets Qt6::Concurrent)
    add_test(NAME premium-console-ui-tests COMMAND premium-console-ui-tests)

    add_executable(premium-legacy-l1-tests
        src/server/LegacyL1Server.h
        src/server/LegacyL1Server.cpp
        tests/test_legacy_l1.cpp
    )
    target_include_directories(premium-legacy-l1-tests PRIVATE src)
    target_link_libraries(premium-legacy-l1-tests PRIVATE premium_common Qt6::Test Qt6::Network)
    add_test(NAME premium-legacy-l1-tests COMMAND premium-legacy-l1-tests)
endif()

install(TARGETS etf-premium-core etf-premium-console etf-premium-client
    BUNDLE DESTINATION .
    RUNTIME DESTINATION bin
)
install(DIRECTORY config adapter protocol docs tools
    DESTINATION share/etf-premium-monitor
    PATTERN "__pycache__" EXCLUDE
    PATTERN "*.pyc" EXCLUDE
    PATTERN "app.json" EXCLUDE
    PATTERN "client-settings.json" EXCLUDE
    PATTERN "tgw_account.ini" EXCLUDE
    PATTERN "tgw_username_override" EXCLUDE
)
install(FILES README.md DESTINATION share/etf-premium-monitor)
