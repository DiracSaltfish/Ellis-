#!/bin/bash
set -euo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
APP_SOURCE_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd -P)
PROJECT_ROOT=$(CDPATH= cd -- "$APP_SOURCE_DIR/.." && pwd -P)
# shellcheck source=lib/common.sh
. "$SCRIPT_DIR/lib/common.sh"

APP_NAME='Machome 四合一运行中心.app'
LABEL=${MACHOME_IBKR_LABEL:-com.ellis.machome-ibkr-bridge}
INSTALLED_APP=${MACHOME_INSTALL_DIR:-"$HOME/Applications"}/$APP_NAME
CONFIG_SOURCE=${MACHOME_IBKR_CONFIG_SOURCE:-"$PROJECT_ROOT/config/ibkr-native-bridge.example.json"}
CONFIG_PATH=${MACHOME_IBKR_CONFIG_PATH:-"$HOME/Library/Application Support/MachomeHub/config/ibkr-native-bridge.json"}
PLIST_TEMPLATE=${MACHOME_IBKR_PLIST_TEMPLATE:-"$APP_SOURCE_DIR/deploy/com.ellis.machome-ibkr-bridge.plist.in"}
PLIST_PATH=${MACHOME_IBKR_PLIST_PATH:-"$HOME/Library/LaunchAgents/$LABEL.plist"}
LOG_DIR=${MACHOME_LOG_DIR:-"$HOME/Library/Logs/MachomeHub"}
BACKUP_ROOT=${MACHOME_BACKUP_ROOT:-"$HOME/Library/Application Support/MachomeHub/backups/ibkr-bridge"}
APPLY=0
LOAD_BRIDGE=0

usage() {
    cat <<EOF
用法：bash scripts/install-ibkr-bridge.sh [选项]

为已经安装、且包含 machome-ibkr-bridge 的四合一应用安装独立配置与
LaunchAgent。默认只预演；不会修改或停止旧 Python uploader/TWS。

  --installed-app PATH  已安装的四合一 .app
  --config-source PATH  首次安装使用的 bridge 配置
  --config PATH         bridge 配置目标
  --plist-template PATH LaunchAgent 模板
  --plist PATH          LaunchAgent 目标
  --log-dir PATH        日志目录
  --backup-root PATH    配置/plist 备份根目录
  --label LABEL         LaunchAgent label
  --load-bridge         显式加载/重载此 bridge label
  --apply               执行写入（否则 dry-run）
  -h, --help            显示帮助
EOF
    machome_usage_footer
}

while [ $# -gt 0 ]; do
    case "$1" in
        --installed-app) [ $# -ge 2 ] || machome_die "$1 缺少参数"; INSTALLED_APP=$2; shift 2 ;;
        --config-source) [ $# -ge 2 ] || machome_die "$1 缺少参数"; CONFIG_SOURCE=$2; shift 2 ;;
        --config) [ $# -ge 2 ] || machome_die "$1 缺少参数"; CONFIG_PATH=$2; shift 2 ;;
        --plist-template) [ $# -ge 2 ] || machome_die "$1 缺少参数"; PLIST_TEMPLATE=$2; shift 2 ;;
        --plist) [ $# -ge 2 ] || machome_die "$1 缺少参数"; PLIST_PATH=$2; shift 2 ;;
        --log-dir) [ $# -ge 2 ] || machome_die "$1 缺少参数"; LOG_DIR=$2; shift 2 ;;
        --backup-root) [ $# -ge 2 ] || machome_die "$1 缺少参数"; BACKUP_ROOT=$2; shift 2 ;;
        --label) [ $# -ge 2 ] || machome_die "$1 缺少参数"; LABEL=$2; shift 2 ;;
        --load-bridge) LOAD_BRIDGE=1; shift ;;
        --apply) APPLY=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) machome_die "未知选项：$1" ;;
    esac
done

case "$LABEL" in ''|*[!A-Za-z0-9._-]*) machome_die "LaunchAgent label 含非法字符：$LABEL" ;; esac
INSTALLED_APP=$(machome_absolute_path "$INSTALLED_APP")
CONFIG_SOURCE=$(machome_absolute_path "$CONFIG_SOURCE")
CONFIG_PATH=$(machome_absolute_path "$CONFIG_PATH")
PLIST_TEMPLATE=$(machome_absolute_path "$PLIST_TEMPLATE")
PLIST_PATH=$(machome_absolute_path "$PLIST_PATH")
LOG_DIR=$(machome_absolute_path "$LOG_DIR")
BACKUP_ROOT=$(machome_absolute_path "$BACKUP_ROOT")
BRIDGE_PROGRAM="$INSTALLED_APP/Contents/MacOS/machome-ibkr-bridge"

for path in "$INSTALLED_APP" "$CONFIG_PATH" "$PLIST_PATH" "$LOG_DIR" "$BACKUP_ROOT"; do
    machome_assert_safe_target "IBKR bridge 路径" "$path"
done
[ -f "$CONFIG_SOURCE" ] || machome_die "配置源不存在：$CONFIG_SOURCE"
[ -f "$PLIST_TEMPLATE" ] || machome_die "plist 模板不存在：$PLIST_TEMPLATE"

CONFIG_TO_VALIDATE=$CONFIG_SOURCE
[ ! -f "$CONFIG_PATH" ] || CONFIG_TO_VALIDATE=$CONFIG_PATH
machome_note "模式：$([ "$APPLY" -eq 1 ] && printf APPLY || printf DRY-RUN)"
machome_note "bridge：$BRIDGE_PROGRAM"
machome_note "配置：${CONFIG_PATH}（已有文件不会被覆盖）"
machome_note "LaunchAgent：$PLIST_PATH"

if [ "$APPLY" -eq 0 ]; then
    [ -x "$BRIDGE_PROGRAM" ] || machome_warn "应用尚未包含原生 bridge；请先用 --with-native-ibkr 构建并安装"
    if [ -x "$BRIDGE_PROGRAM" ]; then
        machome_print_cmd "$BRIDGE_PROGRAM" --validate-config --config "$CONFIG_TO_VALIDATE"
    fi
    machome_note "执行时会备份旧配置/plist；未指定 --load-bridge 时不会改变进程。"
    exit 0
fi

[ -x "$BRIDGE_PROGRAM" ] || machome_die "应用不含可执行 machome-ibkr-bridge"
"$BRIDGE_PROGRAM" --validate-config --config "$CONFIG_TO_VALIDATE" >/dev/null
PLUTIL_BIN=$(machome_resolve_command /usr/bin/plutil) || machome_die "找不到 plutil"

if [ "$LOAD_BRIDGE" -eq 1 ] && ! /bin/launchctl print "gui/$(/usr/bin/id -u)/$LABEL" >/dev/null 2>&1 && \
   /usr/bin/pgrep -u "$(/usr/bin/id -u)" -f '/machome-ibkr-bridge([[:space:]]|$)' >/dev/null 2>&1; then
    machome_die "发现不受 ${LABEL} 管理的 bridge；请先正常退出该测试实例"
fi

umask 077
/bin/mkdir -p "$(dirname -- "$CONFIG_PATH")" "$(dirname -- "$PLIST_PATH")" "$LOG_DIR" "$BACKUP_ROOT"
STAMP=$(/bin/date -u +%Y%m%dT%H%M%SZ)-$$
BACKUP_DIR="$BACKUP_ROOT/$STAMP"
/bin/mkdir -p "$BACKUP_DIR"
[ ! -f "$CONFIG_PATH" ] || /bin/cp -p "$CONFIG_PATH" "$BACKUP_DIR/config.json"
[ ! -f "$PLIST_PATH" ] || /bin/cp -p "$PLIST_PATH" "$BACKUP_DIR/launchagent.plist"
if [ ! -f "$CONFIG_PATH" ]; then
    /usr/bin/install -m 0600 "$CONFIG_SOURCE" "$CONFIG_PATH"
fi

render_value() {
    local xml_value
    xml_value=$(machome_xml_escape "$1")
    machome_sed_replacement "$xml_value"
}
TMP_PLIST=$(/usr/bin/mktemp "$(dirname -- "$PLIST_PATH")/.machome-ibkr-plist.XXXXXX")
trap '/bin/rm -f -- "$TMP_PLIST"' EXIT HUP INT TERM
/usr/bin/sed \
    -e "s|@BRIDGE_PROGRAM@|$(render_value "$BRIDGE_PROGRAM")|g" \
    -e "s|@CONFIG_PATH@|$(render_value "$CONFIG_PATH")|g" \
    -e "s|@WORKING_DIRECTORY@|$(render_value "$INSTALLED_APP/Contents/MacOS")|g" \
    -e "s|@STDOUT_LOG@|$(render_value "$LOG_DIR/ibkr-bridge.stdout.log")|g" \
    -e "s|@STDERR_LOG@|$(render_value "$LOG_DIR/ibkr-bridge.stderr.log")|g" \
    -e "s|@LABEL@|$(render_value "$LABEL")|g" \
    "$PLIST_TEMPLATE" > "$TMP_PLIST"
"$PLUTIL_BIN" -lint "$TMP_PLIST" >/dev/null
/bin/chmod 0644 "$TMP_PLIST"
/bin/mv "$TMP_PLIST" "$PLIST_PATH"
trap - EXIT HUP INT TERM

if [ "$LOAD_BRIDGE" -eq 1 ]; then
    DOMAIN="gui/$(/usr/bin/id -u)"
    if /bin/launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
        /bin/launchctl bootout "$DOMAIN" "$PLIST_PATH"
    fi
    /bin/launchctl bootstrap "$DOMAIN" "$PLIST_PATH"
    /bin/launchctl kickstart -k "$DOMAIN/$LABEL"
    machome_note "已加载 ${LABEL}；未触碰任何旧 uploader label。"
else
    machome_note "配置和 plist 已安装；未改变运行中进程。"
fi
machome_note "备份：$BACKUP_DIR"
