#!/bin/bash
set -euo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
APP_SOURCE_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd -P)
PROJECT_ROOT=$(CDPATH= cd -- "$APP_SOURCE_DIR/.." && pwd -P)
# shellcheck source=lib/common.sh
. "$SCRIPT_DIR/lib/common.sh"

APP_NAME='Machome 四合一运行中心.app'
LABEL=${MACHOME_AGENT_LABEL:-com.ellis.machome-hub-agent}
SOURCE_APP=${MACHOME_PACKAGE_APP:-"$APP_SOURCE_DIR/dist/$APP_NAME"}
INSTALL_DIR=${MACHOME_INSTALL_DIR:-"$HOME/Applications"}
CONFIG_SOURCE=${MACHOME_DEFAULT_CONFIG:-"$PROJECT_ROOT/config/modules.example.json"}
CONFIG_PATH=${MACHOME_CONFIG_PATH:-"$HOME/Library/Application Support/MachomeHub/config/modules.json"}
PLIST_TEMPLATE=${MACHOME_PLIST_TEMPLATE:-"$APP_SOURCE_DIR/deploy/com.ellis.machome-hub-agent.plist.in"}
PLIST_PATH=${MACHOME_PLIST_PATH:-"$HOME/Library/LaunchAgents/$LABEL.plist"}
BACKUP_ROOT=${MACHOME_BACKUP_ROOT:-"$HOME/Library/Application Support/MachomeHub/backups"}
LOG_DIR=${MACHOME_LOG_DIR:-"$HOME/Library/Logs/MachomeHub"}
RUNTIME_DIR=${MACHOME_RUNTIME_DIR:-"$HOME/Library/Application Support/MachomeHub/runtime"}
CODESIGN_VALUE=${CODESIGN_BIN:-/usr/bin/codesign}
DITTO_VALUE=${DITTO_BIN:-/usr/bin/ditto}
PLUTIL_VALUE=${PLUTIL_BIN:-/usr/bin/plutil}
APPLY=0
LOAD_AGENT=0
DRY_RUN=1

usage() {
    cat <<EOF
用法：bash scripts/install.sh [选项]

默认仅预演；必须显式传 --apply 才会写入。安装前会保存已有 Hub app、
LaunchAgent plist 和配置。已有配置永不被默认配置覆盖。

选项：
  --source-app PATH      已由 build-release.sh 打包签名的 .app
  --install-dir PATH     用户级应用目录（默认：${INSTALL_DIR}）
  --config-source PATH   首次安装使用的默认配置
  --config PATH          Hub 配置目标
  --plist-template PATH  LaunchAgent plist 模板
  --plist PATH           LaunchAgent plist 目标
  --backup-root PATH     版本化备份根目录
  --log-dir PATH         Hub Agent 日志目录
  --runtime-dir PATH     Hub Agent 默认 socket/lock 目录
  --label LABEL          Hub 自身 LaunchAgent label
  --load-agent           安装后重载 Hub Agent（只操作上述 label）
  --dry-run              仅预演（默认）
  --apply                执行安装
  -h, --help             显示帮助

相同路径也可通过 MACHOME_PACKAGE_APP、MACHOME_INSTALL_DIR、
MACHOME_DEFAULT_CONFIG、MACHOME_CONFIG_PATH、MACHOME_PLIST_TEMPLATE、
MACHOME_PLIST_PATH、MACHOME_BACKUP_ROOT、MACHOME_LOG_DIR、MACHOME_RUNTIME_DIR、
MACHOME_AGENT_LABEL 设置。
EOF
    machome_usage_footer
}

while [ $# -gt 0 ]; do
    case "$1" in
        --source-app) [ $# -ge 2 ] || machome_die "$1 缺少参数"; SOURCE_APP=$2; shift 2 ;;
        --install-dir) [ $# -ge 2 ] || machome_die "$1 缺少参数"; INSTALL_DIR=$2; shift 2 ;;
        --config-source) [ $# -ge 2 ] || machome_die "$1 缺少参数"; CONFIG_SOURCE=$2; shift 2 ;;
        --config) [ $# -ge 2 ] || machome_die "$1 缺少参数"; CONFIG_PATH=$2; shift 2 ;;
        --plist-template) [ $# -ge 2 ] || machome_die "$1 缺少参数"; PLIST_TEMPLATE=$2; shift 2 ;;
        --plist) [ $# -ge 2 ] || machome_die "$1 缺少参数"; PLIST_PATH=$2; shift 2 ;;
        --backup-root) [ $# -ge 2 ] || machome_die "$1 缺少参数"; BACKUP_ROOT=$2; shift 2 ;;
        --log-dir) [ $# -ge 2 ] || machome_die "$1 缺少参数"; LOG_DIR=$2; shift 2 ;;
        --runtime-dir) [ $# -ge 2 ] || machome_die "$1 缺少参数"; RUNTIME_DIR=$2; shift 2 ;;
        --label) [ $# -ge 2 ] || machome_die "$1 缺少参数"; LABEL=$2; shift 2 ;;
        --load-agent) LOAD_AGENT=1; shift ;;
        --dry-run) APPLY=0; DRY_RUN=1; shift ;;
        --apply) APPLY=1; DRY_RUN=0; shift ;;
        -h|--help) usage; exit 0 ;;
        *) machome_die "未知选项：$1" ;;
    esac
done

case "$LABEL" in
    ''|*[!A-Za-z0-9._-]*) machome_die "LaunchAgent label 含非法字符：$LABEL" ;;
esac

SOURCE_APP=$(machome_absolute_path "$SOURCE_APP")
INSTALL_DIR=$(machome_absolute_path "$INSTALL_DIR")
CONFIG_SOURCE=$(machome_absolute_path "$CONFIG_SOURCE")
CONFIG_PATH=$(machome_absolute_path "$CONFIG_PATH")
PLIST_TEMPLATE=$(machome_absolute_path "$PLIST_TEMPLATE")
PLIST_PATH=$(machome_absolute_path "$PLIST_PATH")
BACKUP_ROOT=$(machome_absolute_path "$BACKUP_ROOT")
LOG_DIR=$(machome_absolute_path "$LOG_DIR")
RUNTIME_DIR=$(machome_absolute_path "$RUNTIME_DIR")
TARGET_APP="$INSTALL_DIR/$APP_NAME"
SOURCE_AGENT="$SOURCE_APP/Contents/MacOS/machome-hub-agent"
TARGET_AGENT="$TARGET_APP/Contents/MacOS/machome-hub-agent"

machome_assert_safe_target "安装目录" "$INSTALL_DIR"
machome_assert_safe_target "应用目标" "$TARGET_APP"
machome_assert_safe_target "配置目标" "$CONFIG_PATH"
machome_assert_safe_target "plist 目标" "$PLIST_PATH"
machome_assert_safe_target "备份目录" "$BACKUP_ROOT"
machome_assert_safe_target "日志目录" "$LOG_DIR"
machome_assert_safe_target "运行时目录" "$RUNTIME_DIR"
[ "$SOURCE_APP" != "$TARGET_APP" ] || machome_die "源应用与安装目标不能相同"
[ -d "$SOURCE_APP" ] || machome_die "源应用不存在：$SOURCE_APP"
[ -f "$SOURCE_APP/Contents/Info.plist" ] || machome_die "源应用缺少 Contents/Info.plist"
[ -f "$CONFIG_SOURCE" ] || machome_die "默认配置不存在：$CONFIG_SOURCE"
[ -f "$PLIST_TEMPLATE" ] || machome_die "plist 模板不存在：$PLIST_TEMPLATE"

CODESIGN_BIN_RESOLVED=$(machome_resolve_command "$CODESIGN_VALUE") || machome_die "找不到 codesign"
DITTO_BIN_RESOLVED=$(machome_resolve_command "$DITTO_VALUE") || machome_die "找不到 ditto"
PLUTIL_BIN_RESOLVED=$(machome_resolve_command "$PLUTIL_VALUE") || machome_die "找不到 plutil"

if [ ! -x "$SOURCE_AGENT" ]; then
    if [ "$DRY_RUN" -eq 1 ]; then
        machome_warn "当前源包尚未包含 machome-hub-agent；实际安装会拒绝。请先运行 build-release.sh"
    else
        machome_die "源应用缺少可执行 Agent：$SOURCE_AGENT"
    fi
fi

CONFIG_TO_VALIDATE=$CONFIG_SOURCE
if [ -f "$CONFIG_PATH" ]; then CONFIG_TO_VALIDATE=$CONFIG_PATH; fi

machome_note "安装模式：$([ "$APPLY" -eq 1 ] && printf 'APPLY' || printf 'DRY-RUN')"
machome_note "应用：$SOURCE_APP -> $TARGET_APP"
machome_note "配置：$CONFIG_PATH"
if [ -e "$CONFIG_PATH" ]; then
    machome_note "已有配置将备份但保持原样；不会写入默认配置。"
else
    machome_note "目标配置不存在，将写入默认 shadow 配置：$CONFIG_SOURCE"
fi
machome_note "LaunchAgent：${PLIST_PATH}（label=${LABEL}）"
machome_note "旧 Hub 文件备份根：$BACKUP_ROOT"

if [ "$DRY_RUN" -eq 1 ]; then
    machome_print_cmd "$CODESIGN_BIN_RESOLVED" --verify --deep --strict --verbose=1 "$SOURCE_APP"
    if [ -x "$SOURCE_AGENT" ]; then
        machome_print_cmd "$SOURCE_AGENT" --config "$CONFIG_TO_VALIDATE" --check-config
    fi
    machome_print_cmd /bin/mkdir -p "$INSTALL_DIR" "$(dirname -- "$CONFIG_PATH")" "$(dirname -- "$PLIST_PATH")" "$BACKUP_ROOT" "$LOG_DIR" "$RUNTIME_DIR"
    machome_note "随后会先备份现有 Hub app/plist/config，再以暂存文件替换 app/plist。"
    if [ "$LOAD_AGENT" -eq 1 ]; then
        machome_print_cmd /bin/launchctl bootstrap "gui/$(/usr/bin/id -u)" "$PLIST_PATH"
    else
        machome_note "未指定 --load-agent：只安装 plist，不改变任何运行中进程。"
    fi
    machome_note "dry-run 完成；未创建目录、备份或安装文件。"
    exit 0
fi

[ "$(/usr/bin/uname -s)" = Darwin ] || machome_die "安装脚本仅支持 macOS"
"$CODESIGN_BIN_RESOLVED" --verify --deep --strict --verbose=1 "$SOURCE_APP"
"$SOURCE_AGENT" --config "$CONFIG_TO_VALIDATE" --check-config

AGENT_WAS_LOADED=0
if [ "$LOAD_AGENT" -eq 1 ] && /bin/launchctl print "gui/$(/usr/bin/id -u)/$LABEL" >/dev/null 2>&1; then
    AGENT_WAS_LOADED=1
fi
if [ "$LOAD_AGENT" -eq 1 ] && [ "$AGENT_WAS_LOADED" -eq 0 ] && \
   /usr/bin/pgrep -u "$(/usr/bin/id -u)" -f '/machome-hub-agent([[:space:]]|$)' >/dev/null 2>&1; then
    machome_die "发现不受 $LABEL 管理的 Hub Agent；请先关闭对应 Hub GUI/Agent，再使用 --load-agent"
fi

umask 077
/bin/mkdir -p "$INSTALL_DIR" "$(dirname -- "$CONFIG_PATH")" \
    "$(dirname -- "$PLIST_PATH")" "$BACKUP_ROOT" "$LOG_DIR" "$RUNTIME_DIR"
/bin/chmod 0700 "$RUNTIME_DIR"

STAMP=$(/bin/date -u +%Y%m%dT%H%M%SZ)
BACKUP_DIR="$BACKUP_ROOT/$STAMP-$$"
ORIGINAL_DIR="$BACKUP_DIR/original"
/bin/mkdir -p "$ORIGINAL_DIR/app" "$ORIGINAL_DIR/plist" "$ORIGINAL_DIR/config"

STAGE_ROOT=$(/usr/bin/mktemp -d "$INSTALL_DIR/.machome-install.XXXXXX")
STAGED_APP="$STAGE_ROOT/$APP_NAME"
STAGED_PLIST="$STAGE_ROOT/$LABEL.plist"
cleanup_prepared_stage() {
    local status=$?
    trap - EXIT HUP INT TERM
    case "${STAGE_ROOT:-}" in
        "$INSTALL_DIR"/.machome-install.*) [ ! -d "$STAGE_ROOT" ] || /bin/rm -rf -- "$STAGE_ROOT" ;;
    esac
    exit "$status"
}
trap cleanup_prepared_stage EXIT HUP INT TERM
"$DITTO_BIN_RESOLVED" "$SOURCE_APP" "$STAGED_APP"

render_value() {
    local xml_value
    xml_value=$(machome_xml_escape "$1")
    machome_sed_replacement "$xml_value"
}
AGENT_REPL=$(render_value "$TARGET_AGENT")
CONFIG_REPL=$(render_value "$CONFIG_PATH")
WORK_REPL=$(render_value "$TARGET_APP/Contents/MacOS")
STDOUT_REPL=$(render_value "$LOG_DIR/agent.stdout.log")
STDERR_REPL=$(render_value "$LOG_DIR/agent.stderr.log")
LABEL_REPL=$(render_value "$LABEL")
/usr/bin/sed \
    -e "s|@AGENT_PROGRAM@|$AGENT_REPL|g" \
    -e "s|@CONFIG_PATH@|$CONFIG_REPL|g" \
    -e "s|@WORKING_DIRECTORY@|$WORK_REPL|g" \
    -e "s|@STDOUT_LOG@|$STDOUT_REPL|g" \
    -e "s|@STDERR_LOG@|$STDERR_REPL|g" \
    -e "s|@LABEL@|$LABEL_REPL|g" \
    "$PLIST_TEMPLATE" > "$STAGED_PLIST"
"$PLUTIL_BIN_RESOLVED" -lint "$STAGED_PLIST" >/dev/null
/bin/chmod 0644 "$STAGED_PLIST"

HAD_APP=0
HAD_PLIST=0
HAD_CONFIG=0
CONFIG_WRITTEN=0
[ ! -e "$TARGET_APP" ] || HAD_APP=1
[ ! -e "$PLIST_PATH" ] || HAD_PLIST=1
[ ! -e "$CONFIG_PATH" ] || HAD_CONFIG=1

for manifest_value in "$TARGET_APP" "$CONFIG_PATH" "$PLIST_PATH" "$LABEL"; do
    machome_require_single_line "manifest value" "$manifest_value"
done
MANIFEST="$BACKUP_DIR/manifest.env"
{
    printf 'format_version=1\n'
    printf 'created_at=%s\n' "$STAMP"
    printf 'target_app=%s\n' "$TARGET_APP"
    printf 'config_path=%s\n' "$CONFIG_PATH"
    printf 'plist_path=%s\n' "$PLIST_PATH"
    printf 'label=%s\n' "$LABEL"
    printf 'had_app=%s\n' "$HAD_APP"
    printf 'had_plist=%s\n' "$HAD_PLIST"
    printf 'had_config=%s\n' "$HAD_CONFIG"
    printf 'config_written=%s\n' "$([ "$HAD_CONFIG" -eq 0 ] && printf '1' || printf '0')"
} > "$MANIFEST"
/bin/chmod 0600 "$MANIFEST"

INSTALL_SUCCEEDED=0
APP_INSTALLED=0
PLIST_INSTALLED=0
OLD_APP_SAVED=0
OLD_PLIST_SAVED=0

recover_failed_install() {
    local status=$?
    trap - EXIT HUP INT TERM
    if [ "$status" -eq 0 ]; then status=1; fi
    if [ "$INSTALL_SUCCEEDED" -eq 1 ]; then
        return "$status"
    fi
    set +e
    machome_warn "安装未完成，正在恢复安装前的 Hub 文件"
    /bin/mkdir -p "$BACKUP_DIR/failed-new"
    if [ "$APP_INSTALLED" -eq 1 ] && [ -e "$TARGET_APP" ]; then
        /bin/mv "$TARGET_APP" "$BACKUP_DIR/failed-new/$APP_NAME"
    fi
    if [ "$OLD_APP_SAVED" -eq 1 ] && [ -e "$ORIGINAL_DIR/app/$APP_NAME" ]; then
        "$DITTO_BIN_RESOLVED" "$ORIGINAL_DIR/app/$APP_NAME" "$TARGET_APP"
    fi
    if [ "$PLIST_INSTALLED" -eq 1 ] && [ -e "$PLIST_PATH" ]; then
        /bin/mv "$PLIST_PATH" "$BACKUP_DIR/failed-new/$LABEL.plist"
    fi
    if [ "$OLD_PLIST_SAVED" -eq 1 ] && [ -e "$ORIGINAL_DIR/plist/$LABEL.plist" ]; then
        "$DITTO_BIN_RESOLVED" "$ORIGINAL_DIR/plist/$LABEL.plist" "$PLIST_PATH"
    fi
    if [ "$CONFIG_WRITTEN" -eq 1 ] && [ -e "$CONFIG_PATH" ]; then
        /bin/mv "$CONFIG_PATH" "$BACKUP_DIR/failed-new/modules.json"
    fi
    if [ "$LOAD_AGENT" -eq 1 ]; then
        /bin/launchctl bootout "gui/$(/usr/bin/id -u)/$LABEL" >/dev/null 2>&1
        if [ "$AGENT_WAS_LOADED" -eq 1 ] && [ -f "$PLIST_PATH" ]; then
            /bin/launchctl bootstrap "gui/$(/usr/bin/id -u)" "$PLIST_PATH" >/dev/null 2>&1
        fi
    fi
    case "${STAGE_ROOT:-}" in
        "$INSTALL_DIR"/.machome-install.*) [ ! -d "$STAGE_ROOT" ] || /bin/rm -rf -- "$STAGE_ROOT" ;;
    esac
    exit "$status"
}
trap - EXIT HUP INT TERM
trap recover_failed_install EXIT HUP INT TERM

if [ "$HAD_CONFIG" -eq 1 ]; then
    "$DITTO_BIN_RESOLVED" "$CONFIG_PATH" "$ORIGINAL_DIR/config/modules.json"
fi
if [ "$HAD_APP" -eq 1 ]; then
    OLD_APP_SAVED=1
    /bin/mv "$TARGET_APP" "$ORIGINAL_DIR/app/$APP_NAME"
fi
if [ "$HAD_PLIST" -eq 1 ]; then
    OLD_PLIST_SAVED=1
    /bin/mv "$PLIST_PATH" "$ORIGINAL_DIR/plist/$LABEL.plist"
fi

APP_INSTALLED=1
/bin/mv "$STAGED_APP" "$TARGET_APP"
PLIST_INSTALLED=1
/bin/mv "$STAGED_PLIST" "$PLIST_PATH"
if [ "$HAD_CONFIG" -eq 0 ]; then
    # noclobber performs an atomic O_EXCL-style claim. This closes the race
    # between the earlier existence check and writing the first-run default.
    ( set -o noclobber; : > "$CONFIG_PATH" ) || \
        machome_die "配置目标在安装事务期间出现，拒绝覆盖：$CONFIG_PATH"
    CONFIG_WRITTEN=1
    /bin/cp -p "$CONFIG_SOURCE" "$CONFIG_PATH"
    /bin/chmod 0600 "$CONFIG_PATH"
fi

"$CODESIGN_BIN_RESOLVED" --verify --deep --strict --verbose=1 "$TARGET_APP"
"$TARGET_AGENT" --config "$CONFIG_PATH" --check-config

if [ "$LOAD_AGENT" -eq 1 ]; then
    if [ "$AGENT_WAS_LOADED" -eq 1 ]; then
        /bin/launchctl bootout "gui/$(/usr/bin/id -u)/$LABEL"
    fi
    /bin/launchctl bootstrap "gui/$(/usr/bin/id -u)" "$PLIST_PATH"
    /bin/launchctl print "gui/$(/usr/bin/id -u)/$LABEL" >/dev/null
else
    machome_note "plist 已安装但未加载；现有进程保持不变。"
fi

INSTALL_SUCCEEDED=1
trap - EXIT HUP INT TERM
case "$STAGE_ROOT" in
    "$INSTALL_DIR"/.machome-install.*) [ ! -d "$STAGE_ROOT" ] || /bin/rm -rf -- "$STAGE_ROOT" ;;
esac

machome_note "安装完成：$TARGET_APP"
machome_note "备份完成：$BACKUP_DIR"
machome_note "回滚预演：bash '$SCRIPT_DIR/rollback.sh' --backup '$BACKUP_DIR'"
