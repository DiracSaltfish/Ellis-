#!/bin/bash
set -euo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
APP_SOURCE_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd -P)
# shellcheck source=lib/common.sh
. "$SCRIPT_DIR/lib/common.sh"

APP_NAME='Machome 四合一运行中心.app'
BUILD_DIR=${MACHOME_BUILD_DIR:-"$APP_SOURCE_DIR/build-release"}
DIST_DIR=${MACHOME_DIST_DIR:-"$APP_SOURCE_DIR/dist"}
QT_PREFIX=${QT_PREFIX:-}
CMAKE_VALUE=${CMAKE_BIN:-cmake}
CTEST_VALUE=${CTEST_BIN:-ctest}
MACDEPLOYQT_VALUE=${MACDEPLOYQT_BIN:-}
CODESIGN_VALUE=${CODESIGN_BIN:-/usr/bin/codesign}
DITTO_VALUE=${DITTO_BIN:-/usr/bin/ditto}
PYTHON_VALUE=${PYTHON_BIN:-python3}
GENERATOR=${CMAKE_GENERATOR:-}
JOBS=${MACHOME_BUILD_JOBS:-}
DRY_RUN=0
RUN_TESTS=1
BUILD_NATIVE_IBKR=0
BUILD_LIVE_QMT=0
UPLOAD_COMPONENT_DIR=${MACHOME_UPLOAD_COMPONENT_DIR:-}
IBKR_SDK_ROOT=${MACHOME_IBKR_CPP_API_ROOT:-}
IBKR_PROTOBUF_ROOT=${MACHOME_IBKR_PROTOBUF_ROOT:-}
IBKR_ABSL_ROOT=${MACHOME_IBKR_ABSL_ROOT:-}

usage() {
    cat <<EOF
用法：bash scripts/build-release.sh [选项]

构建、测试并生成可独立运行的 macOS Qt 应用包。默认产物：
  ${DIST_DIR}/${APP_NAME}

选项：
  --source-dir PATH      CMake 源目录（默认：${APP_SOURCE_DIR}）
  --build-dir PATH       Release 构建目录
  --dist-dir PATH        最终 .app/.zip 输出目录
  --qt-prefix PATH       Qt 6 安装前缀
  --cmake PATH           cmake 可执行文件
  --ctest PATH           ctest 可执行文件
  --macdeployqt PATH     Qt 6 macdeployqt 可执行文件
  --codesign PATH        codesign 可执行文件
  --ditto PATH           ditto 可执行文件
  --generator NAME       可选 CMake generator（例如 Ninja）
  --jobs N               并行构建数
  --with-live-qmt-orders 编译真实 QMT 能力（运行时配置仍单独控制）
  --upload-component PATH 已构建的包内 Upload 组件目录
  --with-native-ibkr     构建并打包原生 IBKR TWS 行情桥
  --ibkr-sdk-root PATH   官方 IBKR C++ API 根目录
  --ibkr-protobuf-root PATH
                         与该 SDK 生成代码匹配的 protobuf 前缀
  --ibkr-absl-root PATH  上述 protobuf 对应的 Abseil 前缀
  --skip-tests           明确跳过 CTest（交付构建不建议）
  --dry-run              仅打印计划，不创建/修改文件
  -h, --help             显示帮助

也可使用环境变量 MACHOME_BUILD_DIR、MACHOME_DIST_DIR、QT_PREFIX、
CMAKE_BIN、CTEST_BIN、MACDEPLOYQT_BIN、CODESIGN_BIN、DITTO_BIN、
CMAKE_GENERATOR、MACHOME_BUILD_JOBS、MACHOME_IBKR_CPP_API_ROOT、
MACHOME_IBKR_PROTOBUF_ROOT、MACHOME_IBKR_ABSL_ROOT。
EOF
    machome_usage_footer
}

while [ $# -gt 0 ]; do
    case "$1" in
        --source-dir) [ $# -ge 2 ] || machome_die "$1 缺少参数"; APP_SOURCE_DIR=$2; shift 2 ;;
        --build-dir) [ $# -ge 2 ] || machome_die "$1 缺少参数"; BUILD_DIR=$2; shift 2 ;;
        --dist-dir) [ $# -ge 2 ] || machome_die "$1 缺少参数"; DIST_DIR=$2; shift 2 ;;
        --qt-prefix) [ $# -ge 2 ] || machome_die "$1 缺少参数"; QT_PREFIX=$2; shift 2 ;;
        --cmake) [ $# -ge 2 ] || machome_die "$1 缺少参数"; CMAKE_VALUE=$2; shift 2 ;;
        --ctest) [ $# -ge 2 ] || machome_die "$1 缺少参数"; CTEST_VALUE=$2; shift 2 ;;
        --macdeployqt) [ $# -ge 2 ] || machome_die "$1 缺少参数"; MACDEPLOYQT_VALUE=$2; shift 2 ;;
        --codesign) [ $# -ge 2 ] || machome_die "$1 缺少参数"; CODESIGN_VALUE=$2; shift 2 ;;
        --ditto) [ $# -ge 2 ] || machome_die "$1 缺少参数"; DITTO_VALUE=$2; shift 2 ;;
        --generator) [ $# -ge 2 ] || machome_die "$1 缺少参数"; GENERATOR=$2; shift 2 ;;
        --jobs) [ $# -ge 2 ] || machome_die "$1 缺少参数"; JOBS=$2; shift 2 ;;
        --with-native-ibkr) BUILD_NATIVE_IBKR=1; shift ;;
        --with-live-qmt-orders) BUILD_LIVE_QMT=1; shift ;;
        --upload-component) [ $# -ge 2 ] || machome_die "$1 缺少参数"; UPLOAD_COMPONENT_DIR=$2; shift 2 ;;
        --ibkr-sdk-root) [ $# -ge 2 ] || machome_die "$1 缺少参数"; IBKR_SDK_ROOT=$2; shift 2 ;;
        --ibkr-protobuf-root) [ $# -ge 2 ] || machome_die "$1 缺少参数"; IBKR_PROTOBUF_ROOT=$2; shift 2 ;;
        --ibkr-absl-root) [ $# -ge 2 ] || machome_die "$1 缺少参数"; IBKR_ABSL_ROOT=$2; shift 2 ;;
        --skip-tests) RUN_TESTS=0; shift ;;
        --dry-run) DRY_RUN=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) machome_die "未知选项：$1" ;;
    esac
done

APP_SOURCE_DIR=$(machome_absolute_path "$APP_SOURCE_DIR")
BUILD_DIR=$(machome_absolute_path "$BUILD_DIR")
DIST_DIR=$(machome_absolute_path "$DIST_DIR")
if [ -n "$QT_PREFIX" ]; then QT_PREFIX=$(machome_absolute_path "$QT_PREFIX"); fi
if [ -n "$IBKR_SDK_ROOT" ]; then IBKR_SDK_ROOT=$(machome_absolute_path "$IBKR_SDK_ROOT"); fi
if [ -n "$IBKR_PROTOBUF_ROOT" ]; then IBKR_PROTOBUF_ROOT=$(machome_absolute_path "$IBKR_PROTOBUF_ROOT"); fi
if [ -n "$IBKR_ABSL_ROOT" ]; then IBKR_ABSL_ROOT=$(machome_absolute_path "$IBKR_ABSL_ROOT"); fi
machome_assert_safe_target "构建目录" "$BUILD_DIR"
machome_assert_safe_target "产物目录" "$DIST_DIR"
[ -f "$APP_SOURCE_DIR/CMakeLists.txt" ] || machome_die "不是有效的 app CMake 源目录：$APP_SOURCE_DIR"
if [ "$BUILD_NATIVE_IBKR" -eq 1 ]; then
    [ -n "$IBKR_SDK_ROOT" ] || machome_die "--with-native-ibkr 必须指定 --ibkr-sdk-root"
    [ -n "$IBKR_PROTOBUF_ROOT" ] || machome_die "--with-native-ibkr 必须指定 --ibkr-protobuf-root"
    [ -n "$IBKR_ABSL_ROOT" ] || machome_die "--with-native-ibkr 必须指定 --ibkr-absl-root"
fi

CMAKE_BIN_RESOLVED=$(machome_resolve_command "$CMAKE_VALUE") || machome_die "找不到 cmake：$CMAKE_VALUE"
CTEST_BIN_RESOLVED=$(machome_resolve_command "$CTEST_VALUE") || machome_die "找不到 ctest：$CTEST_VALUE"
CODESIGN_BIN_RESOLVED=$(machome_resolve_command "$CODESIGN_VALUE") || machome_die "找不到 codesign：$CODESIGN_VALUE"
DITTO_BIN_RESOLVED=$(machome_resolve_command "$DITTO_VALUE") || machome_die "找不到 ditto：$DITTO_VALUE"
PYTHON_BIN_RESOLVED=$(machome_resolve_command "$PYTHON_VALUE") || machome_die "找不到 Python 3：$PYTHON_VALUE"

if [ -z "$QT_PREFIX" ]; then
    if command -v qmake6 >/dev/null 2>&1; then
        QT_PREFIX=$(qmake6 -query QT_INSTALL_PREFIX)
    elif [ -x /opt/homebrew/bin/qmake6 ]; then
        QT_PREFIX=$(/opt/homebrew/bin/qmake6 -query QT_INSTALL_PREFIX)
    elif [ -d /opt/homebrew ]; then
        QT_PREFIX=/opt/homebrew
    else
        machome_die "无法自动定位 Qt 6；请传 --qt-prefix"
    fi
fi

if [ -z "$MACDEPLOYQT_VALUE" ]; then
    if [ -x "$QT_PREFIX/bin/macdeployqt" ]; then
        MACDEPLOYQT_VALUE="$QT_PREFIX/bin/macdeployqt"
    else
        MACDEPLOYQT_VALUE=macdeployqt
    fi
fi
MACDEPLOYQT_BIN_RESOLVED=$(machome_resolve_command "$MACDEPLOYQT_VALUE") || \
    machome_die "找不到 Qt 6 macdeployqt：$MACDEPLOYQT_VALUE"

if [ -z "$JOBS" ]; then
    JOBS=$(/usr/sbin/sysctl -n hw.logicalcpu 2>/dev/null || printf '4')
fi
case "$JOBS" in ''|*[!0-9]*) machome_die "--jobs 必须为正整数" ;; esac
[ "$JOBS" -gt 0 ] || machome_die "--jobs 必须大于 0"

machome_note "源目录：$APP_SOURCE_DIR"
machome_note "Release 构建目录：$BUILD_DIR"
machome_note "交付目录：$DIST_DIR"
machome_note "Qt 前缀：$QT_PREFIX"
if [ "$BUILD_NATIVE_IBKR" -eq 1 ]; then
    machome_note "原生 IBKR bridge：启用（SDK 外部引用，不复制进源码树）"
fi

CMAKE_ARGS=(
    -S "$APP_SOURCE_DIR"
    -B "$BUILD_DIR"
    -DCMAKE_BUILD_TYPE=Release
    -DBUILD_TESTING=ON
    "-DCMAKE_PREFIX_PATH=$QT_PREFIX"
)
if [ "$BUILD_LIVE_QMT" -eq 1 ]; then
    CMAKE_ARGS+=(-DMACHOME_ENABLE_LIVE_QMT_ORDERS=ON)
else
    # Do not inherit ON from a reused CMake build directory.
    CMAKE_ARGS+=(-DMACHOME_ENABLE_LIVE_QMT_ORDERS=OFF)
fi
if [ -n "$UPLOAD_COMPONENT_DIR" ]; then
    CMAKE_ARGS+=("-DMACHOME_UPLOAD_COMPONENT_DIR=$UPLOAD_COMPONENT_DIR")
fi
if [ "$BUILD_NATIVE_IBKR" -eq 1 ]; then
    CMAKE_ARGS+=(
        -DMACHOME_BUILD_NATIVE_IBKR_BRIDGE=ON
        "-DMACHOME_IBKR_CPP_API_ROOT=$IBKR_SDK_ROOT"
        "-DMACHOME_IBKR_PROTOBUF_ROOT=$IBKR_PROTOBUF_ROOT"
        "-DMACHOME_IBKR_ABSL_ROOT=$IBKR_ABSL_ROOT"
    )
fi
if [ -n "$GENERATOR" ]; then CMAKE_ARGS+=(-G "$GENERATOR"); fi

if [ "$DRY_RUN" -eq 1 ]; then
    EXPECTED_APP="$BUILD_DIR/$APP_NAME"
    EXPECTED_AGENT="$BUILD_DIR/machome-hub-agent"
    EXPECTED_BRIDGE="$BUILD_DIR/machome-ibkr-bridge"
    EXPECTED_TGW_HELPER="$BUILD_DIR/machome-premium-tgw-helper"
    EXPECTED_WEBULL_HELPER="$BUILD_DIR/machome-webull-browser-helper"
    EXPECTED_WIND_HELPER="$BUILD_DIR/machome-wind-probe-helper"
    machome_note "dry-run 仅列出计划；不调用 CMake/CTest，不创建任何目录："
    machome_print_cmd "$CMAKE_BIN_RESOLVED" "${CMAKE_ARGS[@]}"
    machome_print_cmd "$CMAKE_BIN_RESOLVED" --build "$BUILD_DIR" --config Release --parallel "$JOBS"
    if [ "$RUN_TESTS" -eq 1 ]; then
        machome_print_cmd "$CTEST_BIN_RESOLVED" --test-dir "$BUILD_DIR" -C Release --output-on-failure
    fi
    machome_print_cmd "$DITTO_BIN_RESOLVED" "$EXPECTED_APP" "$DIST_DIR/.machome-stage.XXXXXX/$APP_NAME"
    machome_print_cmd /usr/bin/install -m 0755 "$EXPECTED_AGENT" "$DIST_DIR/.machome-stage.XXXXXX/$APP_NAME/Contents/MacOS/machome-hub-agent"
    machome_print_cmd "$MACDEPLOYQT_BIN_RESOLVED" "$DIST_DIR/.machome-stage.XXXXXX/$APP_NAME" "-executable=$DIST_DIR/.machome-stage.XXXXXX/$APP_NAME/Contents/MacOS/machome-hub-agent" "-executable=$DIST_DIR/.machome-stage.XXXXXX/$APP_NAME/Contents/Helpers/machome-premium-tgw-helper" "-executable=$DIST_DIR/.machome-stage.XXXXXX/$APP_NAME/Contents/Helpers/machome-webull-browser-helper" "-executable=$DIST_DIR/.machome-stage.XXXXXX/$APP_NAME/Contents/Helpers/machome-wind-probe-helper" -always-overwrite -verbose=1
    if [ "$BUILD_NATIVE_IBKR" -eq 1 ]; then
        machome_note "macdeployqt 还会扫描：$EXPECTED_BRIDGE"
    fi
    machome_print_cmd "$CODESIGN_BIN_RESOLVED" --force --deep --sign - --timestamp=none "$DIST_DIR/.machome-stage.XXXXXX/$APP_NAME"
    machome_note "dry-run 完成；未创建构建目录、产物或签名。"
    exit 0
fi

"$CMAKE_BIN_RESOLVED" "${CMAKE_ARGS[@]}"
"$CMAKE_BIN_RESOLVED" --build "$BUILD_DIR" --config Release --parallel "$JOBS"
if [ "$RUN_TESTS" -eq 1 ]; then
    "$CTEST_BIN_RESOLVED" --test-dir "$BUILD_DIR" -C Release --output-on-failure
else
    machome_warn "已按请求跳过 CTest；该产物不具备标准交付验收记录"
fi

SOURCE_APP=
SOURCE_AGENT=
SOURCE_BRIDGE=
SOURCE_TGW_HELPER=
SOURCE_WEBULL_HELPER=
SOURCE_WIND_HELPER=
for candidate in "$BUILD_DIR/$APP_NAME" "$BUILD_DIR/Release/$APP_NAME"; do
    if [ -d "$candidate" ]; then SOURCE_APP=$candidate; break; fi
done
if [ "$BUILD_NATIVE_IBKR" -eq 1 ]; then
    for candidate in "$BUILD_DIR/machome-ibkr-bridge" "$BUILD_DIR/Release/machome-ibkr-bridge"; do
        if [ -x "$candidate" ]; then SOURCE_BRIDGE=$candidate; break; fi
    done
fi
for candidate in "$BUILD_DIR/machome-hub-agent" "$BUILD_DIR/Release/machome-hub-agent"; do
    if [ -x "$candidate" ]; then SOURCE_AGENT=$candidate; break; fi
done
for candidate in "$BUILD_DIR/machome-premium-tgw-helper" "$BUILD_DIR/Release/machome-premium-tgw-helper"; do
    if [ -x "$candidate" ]; then SOURCE_TGW_HELPER=$candidate; break; fi
done
for candidate in "$BUILD_DIR/machome-webull-browser-helper" "$BUILD_DIR/Release/machome-webull-browser-helper"; do
    if [ -x "$candidate" ]; then SOURCE_WEBULL_HELPER=$candidate; break; fi
done
for candidate in "$BUILD_DIR/machome-wind-probe-helper" "$BUILD_DIR/Release/machome-wind-probe-helper"; do
    if [ -x "$candidate" ]; then SOURCE_WIND_HELPER=$candidate; break; fi
done
[ -n "$SOURCE_APP" ] || machome_die "构建完成但未找到 $APP_NAME"
[ -n "$SOURCE_AGENT" ] || machome_die "构建完成但未找到 machome-hub-agent"
[ -n "$SOURCE_TGW_HELPER" ] || machome_die "构建完成但未找到 machome-premium-tgw-helper"
[ -n "$SOURCE_WEBULL_HELPER" ] || machome_die "构建完成但未找到 machome-webull-browser-helper"
[ -n "$SOURCE_WIND_HELPER" ] || machome_die "构建完成但未找到 machome-wind-probe-helper"
if [ "$BUILD_NATIVE_IBKR" -eq 1 ]; then
    [ -n "$SOURCE_BRIDGE" ] || machome_die "构建完成但未找到 machome-ibkr-bridge"
fi

/bin/mkdir -p "$DIST_DIR"
STAGE_ROOT=$(/usr/bin/mktemp -d "$DIST_DIR/.machome-stage.XXXXXX")
cleanup_stage() {
    if [ -n "${STAGE_ROOT:-}" ] && [ -d "$STAGE_ROOT" ]; then
        case "$STAGE_ROOT" in
            "$DIST_DIR"/.machome-stage.*) /bin/rm -rf -- "$STAGE_ROOT" ;;
            *) machome_warn "拒绝清理异常暂存目录：$STAGE_ROOT" ;;
        esac
    fi
}
trap cleanup_stage EXIT HUP INT TERM

STAGED_APP="$STAGE_ROOT/$APP_NAME"
"$DITTO_BIN_RESOLVED" "$SOURCE_APP" "$STAGED_APP"
/usr/bin/install -m 0755 "$SOURCE_AGENT" "$STAGED_APP/Contents/MacOS/machome-hub-agent"
/bin/mkdir -p "$STAGED_APP/Contents/Helpers"
/usr/bin/install -m 0755 "$SOURCE_TGW_HELPER" \
    "$STAGED_APP/Contents/Helpers/machome-premium-tgw-helper"
/usr/bin/install -m 0755 "$SOURCE_WEBULL_HELPER" \
    "$STAGED_APP/Contents/Helpers/machome-webull-browser-helper"
/usr/bin/install -m 0755 "$SOURCE_WIND_HELPER" \
    "$STAGED_APP/Contents/Helpers/machome-wind-probe-helper"

MACDEPLOYQT_ARGS=(
    "$STAGED_APP"
    "-executable=$STAGED_APP/Contents/MacOS/machome-hub-agent"
    "-executable=$STAGED_APP/Contents/Helpers/machome-premium-tgw-helper"
    "-executable=$STAGED_APP/Contents/Helpers/machome-webull-browser-helper"
    "-executable=$STAGED_APP/Contents/Helpers/machome-wind-probe-helper"
)
if [ "$BUILD_NATIVE_IBKR" -eq 1 ]; then
    /usr/bin/install -m 0755 "$SOURCE_BRIDGE" \
        "$STAGED_APP/Contents/MacOS/machome-ibkr-bridge"
    MACDEPLOYQT_ARGS+=("-executable=$STAGED_APP/Contents/MacOS/machome-ibkr-bridge")
fi
MACDEPLOYQT_ARGS+=(-always-overwrite -verbose=1)
# PyInstaller already relocates Upload's non-Qt runtime. Keep macdeployqt
# from rewriting its load commands; restore it before the final bundle sign.
STAGED_UPLOAD="$STAGED_APP/Contents/Resources/upload"
if [ -d "$STAGED_UPLOAD" ]; then
    /bin/mv "$STAGED_UPLOAD" "$STAGE_ROOT/upload-runtime"
fi
"$MACDEPLOYQT_BIN_RESOLVED" "${MACDEPLOYQT_ARGS[@]}"
if [ -d "$STAGE_ROOT/upload-runtime" ]; then
    /bin/mv "$STAGE_ROOT/upload-runtime" "$STAGED_UPLOAD"
fi
"$PYTHON_BIN_RESOLVED" "$SCRIPT_DIR/bundle-macho-deps.py" \
    --bundle "$STAGED_APP" \
    --binary "$STAGED_APP/Contents/Helpers/machome-premium-tgw-helper" \
    --search-dir /opt/homebrew/opt/openssl@3/lib \
    --search-dir /opt/homebrew/opt/simdjson/lib \
    --search-dir /opt/homebrew/opt/zstd/lib
if [ "$BUILD_NATIVE_IBKR" -eq 1 ]; then
    "$PYTHON_BIN_RESOLVED" "$SCRIPT_DIR/bundle-macho-deps.py" \
        --bundle "$STAGED_APP" \
        --binary "$STAGED_APP/Contents/MacOS/machome-ibkr-bridge" \
        --search-dir "$IBKR_PROTOBUF_ROOT/lib" \
        --search-dir "$IBKR_ABSL_ROOT/lib"
fi
[ -f "$STAGED_APP/Contents/Resources/build_manifest.json" ] || \
    machome_die "应用包缺少 Contents/Resources/build_manifest.json"
"$PYTHON_BIN_RESOLVED" -m json.tool \
    "$STAGED_APP/Contents/Resources/build_manifest.json" >/dev/null || \
    machome_die "build_manifest.json 不是有效 JSON"
MINIMUM_SYSTEM_VERSION=$(/usr/libexec/PlistBuddy -c 'Print :LSMinimumSystemVersion' \
    "$STAGED_APP/Contents/Info.plist" 2>/dev/null || printf '')
[ -n "$MINIMUM_SYSTEM_VERSION" ] || \
    machome_die "应用包 LSMinimumSystemVersion 为空，LaunchServices 将拒绝启动"
machome_note "最低 macOS 版本：$MINIMUM_SYSTEM_VERSION"

# Homebrew Qt can expose optional plugins whose companion framework is not
# installed (for example QtPdf or QtVirtualKeyboard). macdeployqt reports these
# but still exits zero and copies the unusable plugin. The Hub does not require
# such plugins, so remove only staged plugins with objectively unresolved
# bundle dependencies before signing. No source or installed business file is
# ever considered here.
while IFS= read -r -d '' plugin; do
    unresolved=$(machome_unresolved_bundle_dependencies "$STAGED_APP" "$plugin")
    if [ -n "$unresolved" ]; then
        machome_warn "移除依赖不完整的可选 Qt 插件：${plugin#$STAGED_APP/}"
        while IFS= read -r dependency; do
            [ -z "$dependency" ] || machome_warn "  未解析：$dependency"
        done <<EOF
$unresolved
EOF
        /bin/rm -f -- "$plugin"
    fi
done < <(/usr/bin/find "$STAGED_APP/Contents/PlugIns" -type f -name '*.dylib' -print0)

for required_binary in \
    "$STAGED_APP/Contents/MacOS/$(/usr/libexec/PlistBuddy -c 'Print :CFBundleExecutable' "$STAGED_APP/Contents/Info.plist")" \
    "$STAGED_APP/Contents/MacOS/machome-hub-agent" \
    "$STAGED_APP/Contents/Helpers/machome-premium-tgw-helper" \
    "$STAGED_APP/Contents/Helpers/machome-webull-browser-helper" \
    "$STAGED_APP/Contents/Helpers/machome-wind-probe-helper"; do
    unresolved=$(machome_unresolved_bundle_dependencies "$STAGED_APP" "$required_binary")
    [ -z "$unresolved" ] || machome_die "必需可执行文件仍含未解析依赖：$required_binary\n$unresolved"
done
if [ "$BUILD_NATIVE_IBKR" -eq 1 ]; then
    BRIDGE_BINARY="$STAGED_APP/Contents/MacOS/machome-ibkr-bridge"
    unresolved=$(machome_unresolved_bundle_dependencies "$STAGED_APP" "$BRIDGE_BINARY")
    [ -z "$unresolved" ] || machome_die "IBKR bridge 仍含未解析依赖：\n$unresolved"
fi
"$CODESIGN_BIN_RESOLVED" --force --deep --sign - --timestamp=none "$STAGED_APP"
"$CODESIGN_BIN_RESOLVED" --verify --deep --strict --verbose=1 "$STAGED_APP"
"$STAGED_APP/Contents/MacOS/machome-hub-agent" --version >/dev/null
"$STAGED_APP/Contents/Helpers/machome-premium-tgw-helper" --help >/dev/null
"$STAGED_APP/Contents/Helpers/machome-webull-browser-helper" --help >/dev/null
if [ "$BUILD_NATIVE_IBKR" -eq 1 ]; then
    # macdeployqt and the non-Qt bundler rewrite Mach-O load commands. macOS
    # may kill a process with an invalidated inherited signature, so execute
    # the load/config smoke only after the final deep signature is applied.
    "$BRIDGE_BINARY" --validate-config \
        --config "$STAGED_APP/Contents/Resources/config/ibkr-native-bridge.example.json" >/dev/null
fi

FINAL_APP="$DIST_DIR/$APP_NAME"
FINAL_ZIP="$DIST_DIR/Machome-Operations-Hub-macOS.zip"
FINAL_SHA="$FINAL_ZIP.sha256"
if [ -e "$FINAL_APP" ] || [ -e "$FINAL_ZIP" ] || [ -e "$FINAL_SHA" ]; then
    ARCHIVE_DIR="$DIST_DIR/archive/$(/bin/date -u +%Y%m%dT%H%M%SZ)-$$"
    /bin/mkdir -p "$ARCHIVE_DIR"
    [ ! -e "$FINAL_APP" ] || /bin/mv "$FINAL_APP" "$ARCHIVE_DIR/"
    [ ! -e "$FINAL_ZIP" ] || /bin/mv "$FINAL_ZIP" "$ARCHIVE_DIR/"
    [ ! -e "$FINAL_SHA" ] || /bin/mv "$FINAL_SHA" "$ARCHIVE_DIR/"
    machome_note "旧交付产物已保留在：$ARCHIVE_DIR"
fi
/bin/mv "$STAGED_APP" "$FINAL_APP"
/bin/rmdir "$STAGE_ROOT"
STAGE_ROOT=
trap - EXIT HUP INT TERM

"$DITTO_BIN_RESOLVED" -c -k --sequesterRsrc --keepParent "$FINAL_APP" "$FINAL_ZIP"
/usr/bin/shasum -a 256 "$FINAL_ZIP" > "$FINAL_SHA"

machome_note "Release 交付完成：$FINAL_APP"
machome_note "压缩包：$FINAL_ZIP"
machome_note "校验值：$FINAL_SHA"
machome_note "签名为 ad-hoc，仅面向受控内网/本机部署；未进行 Developer ID 签名或公证。"
