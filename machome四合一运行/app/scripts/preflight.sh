#!/bin/bash
set -uo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
APP_SOURCE_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd -P)
PROJECT_ROOT=$(CDPATH= cd -- "$APP_SOURCE_DIR/.." && pwd -P)
# shellcheck source=lib/common.sh
. "$SCRIPT_DIR/lib/common.sh"

APP_NAME='Machome 四合一运行中心.app'
SOURCE_DIR=$APP_SOURCE_DIR
BUILD_DIR=${MACHOME_BUILD_DIR:-"$APP_SOURCE_DIR/build-release"}
BUNDLE_PATH=${MACHOME_PACKAGE_APP:-"$APP_SOURCE_DIR/dist/$APP_NAME"}
CONFIG_PATH=${MACHOME_CONFIG_PATH:-"$HOME/Library/Application Support/MachomeHub/config/modules.json"}
DEFAULT_CONFIG=${MACHOME_DEFAULT_CONFIG:-"$PROJECT_ROOT/config/modules.example.json"}
PLIST_PATH=${MACHOME_PLIST_PATH:-"$HOME/Library/LaunchAgents/com.ellis.machome-hub-agent.plist"}
LABEL=${MACHOME_AGENT_LABEL:-com.ellis.machome-hub-agent}
QT_PREFIX=${QT_PREFIX:-}
STRICT=0
WARNINGS=0
FAILURES=0

usage() {
    cat <<EOF
用法：bash scripts/preflight.sh [选项]

只读检查构建和安装前置条件；不创建目录、不写配置、不加载服务。

选项：
  --source-dir PATH      app CMake 源目录
  --build-dir PATH       现有/计划的构建目录
  --bundle PATH          要检查的已打包 .app
  --config PATH          当前 Hub 配置
  --default-config PATH  首次安装默认配置
  --plist PATH           当前 Hub LaunchAgent plist
  --label LABEL          Hub 自身 LaunchAgent label
  --qt-prefix PATH       Qt 6 安装前缀
  --strict               将警告也视为失败
  -h, --help             显示帮助

不存在的可选已构建 bundle、现有配置或 plist 只产生警告；源目录、默认配置、
macOS 与必需构建工具缺失会产生失败。
EOF
    machome_usage_footer
}

while [ $# -gt 0 ]; do
    case "$1" in
        --source-dir) [ $# -ge 2 ] || machome_die "$1 缺少参数"; SOURCE_DIR=$2; shift 2 ;;
        --build-dir) [ $# -ge 2 ] || machome_die "$1 缺少参数"; BUILD_DIR=$2; shift 2 ;;
        --bundle) [ $# -ge 2 ] || machome_die "$1 缺少参数"; BUNDLE_PATH=$2; shift 2 ;;
        --config) [ $# -ge 2 ] || machome_die "$1 缺少参数"; CONFIG_PATH=$2; shift 2 ;;
        --default-config) [ $# -ge 2 ] || machome_die "$1 缺少参数"; DEFAULT_CONFIG=$2; shift 2 ;;
        --plist) [ $# -ge 2 ] || machome_die "$1 缺少参数"; PLIST_PATH=$2; shift 2 ;;
        --label) [ $# -ge 2 ] || machome_die "$1 缺少参数"; LABEL=$2; shift 2 ;;
        --qt-prefix) [ $# -ge 2 ] || machome_die "$1 缺少参数"; QT_PREFIX=$2; shift 2 ;;
        --strict) STRICT=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) machome_die "未知选项：$1" ;;
    esac
done

case "$LABEL" in
    ''|*[!A-Za-z0-9._-]*) machome_die "LaunchAgent label 含非法字符：$LABEL" ;;
esac

SOURCE_DIR=$(machome_absolute_path "$SOURCE_DIR")
BUILD_DIR=$(machome_absolute_path "$BUILD_DIR")
BUNDLE_PATH=$(machome_absolute_path "$BUNDLE_PATH")
CONFIG_PATH=$(machome_absolute_path "$CONFIG_PATH")
DEFAULT_CONFIG=$(machome_absolute_path "$DEFAULT_CONFIG")
PLIST_PATH=$(machome_absolute_path "$PLIST_PATH")
if [ -n "$QT_PREFIX" ]; then QT_PREFIX=$(machome_absolute_path "$QT_PREFIX"); fi

ok() { printf '[ OK ] %s\n' "$*"; }
warn() { printf '[WARN] %s\n' "$*"; WARNINGS=$((WARNINGS + 1)); }
fail() { printf '[FAIL] %s\n' "$*"; FAILURES=$((FAILURES + 1)); }

check_command() {
    local name=$1
    local value=$2
    local resolved
    if resolved=$(machome_resolve_command "$value"); then
        ok "$name: $resolved"
    else
        fail "$name 不可用：$value"
    fi
}

nearest_existing_parent() {
    local value=$1
    while [ ! -e "$value" ] && [ "$value" != / ]; do
        value=$(dirname -- "$value")
    done
    printf '%s\n' "$value"
}

printf 'Machome Hub 只读 preflight\n'
printf '源目录: %s\n' "$SOURCE_DIR"
printf '目标 bundle: %s\n\n' "$BUNDLE_PATH"

if [ "$(/usr/bin/uname -s 2>/dev/null)" = Darwin ]; then
    ok "操作系统：macOS $(/usr/bin/sw_vers -productVersion 2>/dev/null || printf '?') ($( /usr/bin/uname -m 2>/dev/null || printf '?' ))"
else
    fail "仅支持 macOS"
fi

check_command cmake "${CMAKE_BIN:-cmake}"
check_command ctest "${CTEST_BIN:-ctest}"
check_command codesign "${CODESIGN_BIN:-/usr/bin/codesign}"
check_command ditto "${DITTO_BIN:-/usr/bin/ditto}"
check_command plutil "${PLUTIL_BIN:-/usr/bin/plutil}"
check_command otool "${OTOOL_BIN:-/usr/bin/otool}"

QMAKE6_FOUND=
if [ -z "$QT_PREFIX" ]; then
    if command -v qmake6 >/dev/null 2>&1; then
        QMAKE6_FOUND=$(command -v qmake6)
        QT_PREFIX=$("$QMAKE6_FOUND" -query QT_INSTALL_PREFIX 2>/dev/null)
    elif [ -x /opt/homebrew/bin/qmake6 ]; then
        QMAKE6_FOUND=/opt/homebrew/bin/qmake6
        QT_PREFIX=$("$QMAKE6_FOUND" -query QT_INSTALL_PREFIX 2>/dev/null)
    elif [ -d /opt/homebrew ]; then
        QT_PREFIX=/opt/homebrew
    fi
fi
if [ -z "$QMAKE6_FOUND" ] && [ -x "$QT_PREFIX/bin/qmake6" ]; then
    QMAKE6_FOUND="$QT_PREFIX/bin/qmake6"
fi
if [ -n "$QMAKE6_FOUND" ]; then
    QT_VERSION=$("$QMAKE6_FOUND" -query QT_VERSION 2>/dev/null || printf '')
    QT_MAJOR=${QT_VERSION%%.*}
    QT_REST=${QT_VERSION#*.}
    QT_MINOR=${QT_REST%%.*}
    case "$QT_MAJOR:$QT_MINOR" in
        6:*)
            case "$QT_MINOR" in
                ''|*[!0-9]*) warn "无法解析 Qt 版本：$QT_VERSION" ;;
                *) if [ "$QT_MINOR" -ge 5 ]; then ok "Qt 版本：$QT_VERSION"; else fail "Qt 版本低于 6.5：$QT_VERSION"; fi ;;
            esac
            ;;
        *) fail "qmake6 未报告 Qt 6：$QT_VERSION" ;;
    esac
fi
if [ -n "$QT_PREFIX" ] && [ -x "$QT_PREFIX/bin/macdeployqt" ]; then
    ok "Qt 6 / macdeployqt：$QT_PREFIX/bin/macdeployqt"
elif command -v macdeployqt >/dev/null 2>&1; then
    MACDEPLOYQT_FOUND=$(command -v macdeployqt)
    ok "macdeployqt：${MACDEPLOYQT_FOUND}（请确认属于 Qt 6）"
else
    fail "找不到 macdeployqt；请传 --qt-prefix"
fi

if [ -f "$SOURCE_DIR/CMakeLists.txt" ]; then
    ok "CMake 源文件存在"
    if /usr/bin/grep -q 'find_package(Qt6 6\.5' "$SOURCE_DIR/CMakeLists.txt"; then
        ok "工程声明 Qt 6.5+"
    else
        warn "未从 CMakeLists.txt 识别到 Qt 6.5+ 声明"
    fi
else
    fail "缺少 $SOURCE_DIR/CMakeLists.txt"
fi

PYTHON_VALUE=${PYTHON_BIN:-python3}
if PYTHON_RESOLVED=$(machome_resolve_command "$PYTHON_VALUE"); then
    ok "Python（CTest 集成测试需要）：$PYTHON_RESOLVED"
    if [ -f "$DEFAULT_CONFIG" ] && "$PYTHON_RESOLVED" -m json.tool "$DEFAULT_CONFIG" >/dev/null 2>&1; then
        ok "默认配置 JSON 有效：$DEFAULT_CONFIG"
    elif [ -f "$DEFAULT_CONFIG" ]; then
        fail "默认配置不是有效 JSON：$DEFAULT_CONFIG"
    else
        fail "默认配置不存在：$DEFAULT_CONFIG"
    fi
else
    fail "找不到 Python 3（CMake/CTest 需要）"
fi

if [ -f "$CONFIG_PATH" ]; then
    if [ -n "${PYTHON_RESOLVED:-}" ] && "$PYTHON_RESOLVED" -m json.tool "$CONFIG_PATH" >/dev/null 2>&1; then
        ok "现有 Hub 配置 JSON 有效：$CONFIG_PATH"
    else
        fail "现有 Hub 配置无效：$CONFIG_PATH"
    fi
else
    warn "尚无 Hub 配置；install.sh 将仅在此路径不存在时写入默认配置：$CONFIG_PATH"
fi

if [ -f "$PLIST_PATH" ]; then
    if /usr/bin/plutil -lint "$PLIST_PATH" >/dev/null 2>&1; then
        ok "现有 Hub plist 有效：$PLIST_PATH"
    else
        fail "现有 Hub plist 无效：$PLIST_PATH"
    fi
else
    warn "尚未安装 Hub LaunchAgent plist：$PLIST_PATH"
fi

if /bin/launchctl print "gui/$(/usr/bin/id -u)/$LABEL" >/dev/null 2>&1; then
    ok "Hub Agent 当前已加载（只读查询）"
else
    warn "Hub Agent 当前未由 launchd 加载；GUI 仍可按需启动 bundled Agent"
fi

if [ -d "$BUNDLE_PATH" ]; then
    INFO_PLIST="$BUNDLE_PATH/Contents/Info.plist"
    AGENT_BIN="$BUNDLE_PATH/Contents/MacOS/machome-hub-agent"
    if [ -f "$INFO_PLIST" ]; then
        MAIN_EXEC=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleExecutable' "$INFO_PLIST" 2>/dev/null || printf '')
    else
        MAIN_EXEC=
    fi
    if [ -n "$MAIN_EXEC" ] && [ -f "$BUNDLE_PATH/Contents/MacOS/$MAIN_EXEC" ] && \
       [ -x "$BUNDLE_PATH/Contents/MacOS/$MAIN_EXEC" ]; then
        ok "GUI 主可执行文件：$MAIN_EXEC"
    else
        fail "bundle 缺少有效 CFBundleExecutable"
    fi
    if [ -f "$AGENT_BIN" ] && [ -x "$AGENT_BIN" ]; then
        ok "bundle 包含 machome-hub-agent"
    else
        fail "bundle 未包含 machome-hub-agent；不能交付"
    fi
    if /usr/bin/codesign --verify --deep --strict --verbose=1 "$BUNDLE_PATH" >/dev/null 2>&1; then
        ok "bundle 签名结构有效（允许 ad-hoc）"
    else
        warn "bundle 尚未完成/通过签名校验；请运行 build-release.sh"
    fi
    for binary in "$BUNDLE_PATH/Contents/MacOS/$MAIN_EXEC" "$AGENT_BIN"; do
        [ -f "$binary" ] && [ -x "$binary" ] || continue
        NONPORTABLE=$(/usr/bin/otool -L "$binary" 2>/dev/null | \
            /usr/bin/grep -E '/opt/homebrew|/usr/local|Qt[^/]*/[0-9].*/.*\.framework' || true)
        if [ -n "$NONPORTABLE" ]; then
            warn "$(basename -- "$binary") 仍引用构建机绝对库路径；macdeployqt 可能未覆盖"
        else
            ok "$(basename -- "$binary") 未发现 Homebrew/本地 Qt 绝对依赖"
        fi
    done
    if [ -d "$BUNDLE_PATH/Contents/PlugIns" ]; then
        while IFS= read -r -d '' plugin; do
            UNRESOLVED=$(machome_unresolved_bundle_dependencies "$BUNDLE_PATH" "$plugin")
            if [ -n "$UNRESOLVED" ]; then
                warn "Qt 插件存在未打包依赖：${plugin#$BUNDLE_PATH/}"
            fi
        done < <(/usr/bin/find "$BUNDLE_PATH/Contents/PlugIns" -type f -name '*.dylib' -print0)
    fi
    CONFIG_TO_CHECK=$DEFAULT_CONFIG
    if [ -f "$CONFIG_PATH" ]; then CONFIG_TO_CHECK=$CONFIG_PATH; fi
    if [ -x "$AGENT_BIN" ]; then
        if "$AGENT_BIN" --config "$CONFIG_TO_CHECK" --check-config >/dev/null 2>&1; then
            ok "Hub Agent 语义配置校验通过"
        else
            fail "Hub Agent 语义配置校验失败：$CONFIG_TO_CHECK"
        fi
    fi
else
    warn "尚无已打包 bundle：$BUNDLE_PATH"
fi

for target in "$BUILD_DIR" "$(dirname -- "$BUNDLE_PATH")" "$(dirname -- "$CONFIG_PATH")" "$(dirname -- "$PLIST_PATH")"; do
    parent=$(nearest_existing_parent "$target")
    if [ -w "$parent" ]; then
        ok "目标父目录可写：${parent}（仅检查权限，未写入）"
    else
        fail "目标父目录不可写：$parent"
    fi
done

DISK_PARENT=$(nearest_existing_parent "$BUILD_DIR")
AVAILABLE_KB=$(/bin/df -Pk "$DISK_PARENT" 2>/dev/null | /usr/bin/awk 'NR==2 {print $4}')
if [ -n "$AVAILABLE_KB" ]; then
    if [ "$AVAILABLE_KB" -ge 2097152 ]; then
        ok "可用磁盘约 $((AVAILABLE_KB / 1024)) MiB"
    else
        warn "可用磁盘不足 2 GiB：约 $((AVAILABLE_KB / 1024)) MiB"
    fi
fi

printf '\n结果：%s 个失败，%s 个警告。\n' "$FAILURES" "$WARNINGS"
printf '本检查未写文件、未加载/卸载服务，也未探测或操作四套原业务进程。\n'
if [ "$FAILURES" -gt 0 ]; then exit 1; fi
if [ "$STRICT" -eq 1 ] && [ "$WARNINGS" -gt 0 ]; then exit 2; fi
exit 0
