#!/bin/bash
set -euo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
# shellcheck source=lib/common.sh
. "$SCRIPT_DIR/lib/common.sh"

BACKUP_ROOT=${MACHOME_BACKUP_ROOT:-"$HOME/Library/Application Support/MachomeHub/backups"}
BACKUP_DIR=
USE_LATEST=0
APPLY=0
DRY_RUN=1
LOAD_AGENT=0
RESTORE_CONFIG=0
DITTO_VALUE=${DITTO_BIN:-/usr/bin/ditto}
CODESIGN_VALUE=${CODESIGN_BIN:-/usr/bin/codesign}
PLUTIL_VALUE=${PLUTIL_BIN:-/usr/bin/plutil}

usage() {
    cat <<EOF
用法：bash scripts/rollback.sh (--backup PATH | --latest) [选项]

默认只预演。回滚会恢复指定安装备份中的旧 Hub app/plist；若该次安装是
首次创建配置，则把该配置移入 backup/rollback-current，而不是删除。

选项：
  --backup PATH          install.sh 输出的具体备份目录
  --latest               使用备份根目录下按名称排序的最新备份
  --backup-root PATH     --latest 的查找根（默认：${BACKUP_ROOT}）
  --restore-config       还原备份配置；默认保留安装后可能发生的配置修改
  --load-agent           回滚后重载 Hub 自身 LaunchAgent
  --dry-run              仅预演（默认）
  --apply                执行回滚
  -h, --help             显示帮助

当前安装文件不会被删除，而会保存在所选备份目录的 rollback-current 下。
EOF
    machome_usage_footer
}

while [ $# -gt 0 ]; do
    case "$1" in
        --backup) [ $# -ge 2 ] || machome_die "$1 缺少参数"; BACKUP_DIR=$2; shift 2 ;;
        --latest) USE_LATEST=1; shift ;;
        --backup-root) [ $# -ge 2 ] || machome_die "$1 缺少参数"; BACKUP_ROOT=$2; shift 2 ;;
        --restore-config) RESTORE_CONFIG=1; shift ;;
        --load-agent) LOAD_AGENT=1; shift ;;
        --dry-run) APPLY=0; DRY_RUN=1; shift ;;
        --apply) APPLY=1; DRY_RUN=0; shift ;;
        -h|--help) usage; exit 0 ;;
        *) machome_die "未知选项：$1" ;;
    esac
done

[ -z "$BACKUP_DIR" ] || [ "$USE_LATEST" -eq 0 ] || machome_die "--backup 与 --latest 不能同时使用"
[ -n "$BACKUP_DIR" ] || [ "$USE_LATEST" -eq 1 ] || machome_die "必须指定 --backup 或 --latest"

BACKUP_ROOT=$(machome_absolute_path "$BACKUP_ROOT")
if [ "$USE_LATEST" -eq 1 ]; then
    [ -d "$BACKUP_ROOT" ] || machome_die "备份根目录不存在：$BACKUP_ROOT"
    LATEST=
    for candidate in "$BACKUP_ROOT"/*; do
        [ -d "$candidate" ] || continue
        [ -f "$candidate/manifest.env" ] || continue
        if [ -z "$LATEST" ] || [ "$(basename -- "$candidate")" \> "$(basename -- "$LATEST")" ]; then
            LATEST=$candidate
        fi
    done
    [ -n "$LATEST" ] || machome_die "没有找到有效 Hub 备份：$BACKUP_ROOT"
    BACKUP_DIR=$LATEST
else
    BACKUP_DIR=$(machome_absolute_path "$BACKUP_DIR")
fi
machome_assert_safe_target "备份目录" "$BACKUP_DIR"

MANIFEST="$BACKUP_DIR/manifest.env"
[ -f "$MANIFEST" ] || machome_die "备份缺少 manifest.env：$BACKUP_DIR"

manifest_get() {
    local wanted=$1
    /usr/bin/awk -v wanted="$wanted" '
        index($0, "=") > 0 {
            key = substr($0, 1, index($0, "=") - 1)
            if (key == wanted) {
                print substr($0, index($0, "=") + 1)
                exit
            }
        }
    ' "$MANIFEST"
}

FORMAT_VERSION=$(manifest_get format_version)
TARGET_APP=$(manifest_get target_app)
CONFIG_PATH=$(manifest_get config_path)
PLIST_PATH=$(manifest_get plist_path)
LABEL=$(manifest_get label)
HAD_APP=$(manifest_get had_app)
HAD_PLIST=$(manifest_get had_plist)
HAD_CONFIG=$(manifest_get had_config)
CONFIG_WRITTEN=$(manifest_get config_written)

[ "$FORMAT_VERSION" = 1 ] || machome_die "不支持的备份 manifest 版本：$FORMAT_VERSION"
case "$LABEL" in ''|*[!A-Za-z0-9._-]*) machome_die "manifest 中 label 无效" ;; esac
for flag in "$HAD_APP" "$HAD_PLIST" "$HAD_CONFIG" "$CONFIG_WRITTEN"; do
    case "$flag" in 0|1) ;; *) machome_die "manifest 中布尔字段无效" ;; esac
done
machome_assert_safe_target "应用目标" "$TARGET_APP"
machome_assert_safe_target "配置目标" "$CONFIG_PATH"
machome_assert_safe_target "plist 目标" "$PLIST_PATH"
case "$TARGET_APP" in *.app) ;; *) machome_die "manifest 的应用目标不是 .app" ;; esac

APP_NAME=$(basename -- "$TARGET_APP")
ORIGINAL_APP="$BACKUP_DIR/original/app/$APP_NAME"
ORIGINAL_PLIST="$BACKUP_DIR/original/plist/$LABEL.plist"
ORIGINAL_CONFIG="$BACKUP_DIR/original/config/modules.json"
if [ "$HAD_APP" -eq 1 ]; then [ -d "$ORIGINAL_APP" ] || machome_die "备份缺少原 app：$ORIGINAL_APP"; fi
if [ "$HAD_PLIST" -eq 1 ]; then [ -f "$ORIGINAL_PLIST" ] || machome_die "备份缺少原 plist：$ORIGINAL_PLIST"; fi
if [ "$HAD_CONFIG" -eq 1 ]; then [ -f "$ORIGINAL_CONFIG" ] || machome_die "备份缺少原配置：$ORIGINAL_CONFIG"; fi

DITTO_BIN_RESOLVED=$(machome_resolve_command "$DITTO_VALUE") || machome_die "找不到 ditto"
CODESIGN_BIN_RESOLVED=$(machome_resolve_command "$CODESIGN_VALUE") || machome_die "找不到 codesign"
PLUTIL_BIN_RESOLVED=$(machome_resolve_command "$PLUTIL_VALUE") || machome_die "找不到 plutil"
if [ "$HAD_PLIST" -eq 1 ]; then
    "$PLUTIL_BIN_RESOLVED" -lint "$ORIGINAL_PLIST" >/dev/null || machome_die "备份 plist 校验失败"
fi
if [ "$HAD_APP" -eq 1 ]; then
    "$CODESIGN_BIN_RESOLVED" --verify --deep --strict --verbose=1 "$ORIGINAL_APP" >/dev/null 2>&1 || \
        machome_warn "安装前 app 的签名无效或不存在；回滚仍可进行，但需人工复核"
fi
if [ "$RESTORE_CONFIG" -eq 1 ] && [ "$HAD_APP" -eq 1 ] && [ "$HAD_CONFIG" -eq 1 ] && \
   [ -x "$ORIGINAL_APP/Contents/MacOS/machome-hub-agent" ]; then
    "$ORIGINAL_APP/Contents/MacOS/machome-hub-agent" --config "$ORIGINAL_CONFIG" --check-config || \
        machome_die "备份配置无法通过备份 Agent 校验"
fi

machome_note "回滚模式：$([ "$APPLY" -eq 1 ] && printf 'APPLY' || printf 'DRY-RUN')"
machome_note "备份：$BACKUP_DIR"
machome_note "应用：${TARGET_APP}（安装前存在=${HAD_APP}）"
machome_note "plist：${PLIST_PATH}（安装前存在=${HAD_PLIST}）"
if [ "$CONFIG_WRITTEN" -eq 1 ]; then
    machome_note "本次安装首次创建了配置；回滚会将当前配置移入 rollback-current。"
elif [ "$RESTORE_CONFIG" -eq 1 ]; then
    machome_warn "将按请求用安装前备份替换当前 Hub 配置；当前版本会先保留。"
else
    machome_note "安装时未覆盖配置，默认不回滚配置。"
fi
if [ "$LOAD_AGENT" -eq 0 ]; then
    machome_note "未指定 --load-agent：不会改变当前 Hub Agent 进程。"
fi

if [ "$DRY_RUN" -eq 1 ]; then
    machome_note "实际执行时，当前 Hub 文件将先移入 backup/rollback-current/<时间>-<pid>/。"
    [ "$HAD_APP" -eq 0 ] || machome_print_cmd "$DITTO_BIN_RESOLVED" "$ORIGINAL_APP" "$(dirname -- "$TARGET_APP")/.machome-rollback.XXXXXX/$APP_NAME"
    [ "$HAD_PLIST" -eq 0 ] || machome_print_cmd "$DITTO_BIN_RESOLVED" "$ORIGINAL_PLIST" "$(dirname -- "$PLIST_PATH")/.machome-rollback.XXXXXX/$LABEL.plist"
    if [ "$RESTORE_CONFIG" -eq 1 ] && [ "$HAD_CONFIG" -eq 1 ]; then
        machome_print_cmd "$DITTO_BIN_RESOLVED" "$ORIGINAL_CONFIG" "$(dirname -- "$CONFIG_PATH")/.machome-rollback.XXXXXX/modules.json"
    fi
    machome_note "dry-run 完成；未移动、恢复或删除任何文件。"
    exit 0
fi

[ "$(/usr/bin/uname -s)" = Darwin ] || machome_die "回滚脚本仅支持 macOS"
AGENT_WAS_LOADED=0
if [ "$LOAD_AGENT" -eq 1 ] && /bin/launchctl print "gui/$(/usr/bin/id -u)/$LABEL" >/dev/null 2>&1; then
    AGENT_WAS_LOADED=1
fi
if [ "$LOAD_AGENT" -eq 1 ] && [ "$AGENT_WAS_LOADED" -eq 0 ] && \
   /usr/bin/pgrep -u "$(/usr/bin/id -u)" -f '/machome-hub-agent([[:space:]]|$)' >/dev/null 2>&1; then
    machome_die "发现不受 $LABEL 管理的 Hub Agent；请先关闭对应 Hub GUI/Agent，再使用 --load-agent"
fi
ROLLBACK_STAMP=$(/bin/date -u +%Y%m%dT%H%M%SZ)-$$
CURRENT_DIR="$BACKUP_DIR/rollback-current/$ROLLBACK_STAMP"
STAGE_PARENT=$(dirname -- "$TARGET_APP")
/bin/mkdir -p "$CURRENT_DIR" "$STAGE_PARENT" "$(dirname -- "$PLIST_PATH")" "$(dirname -- "$CONFIG_PATH")"
STAGE_ROOT=$(/usr/bin/mktemp -d "$STAGE_PARENT/.machome-rollback.XXXXXX")
STAGED_APP="$STAGE_ROOT/$APP_NAME"
STAGED_PLIST="$STAGE_ROOT/$LABEL.plist"
STAGED_CONFIG="$STAGE_ROOT/modules.json"

cleanup_stage() {
    case "${STAGE_ROOT:-}" in
        "$STAGE_PARENT"/.machome-rollback.*) [ ! -d "$STAGE_ROOT" ] || /bin/rm -rf -- "$STAGE_ROOT" ;;
    esac
}
trap cleanup_stage EXIT HUP INT TERM

if [ "$HAD_APP" -eq 1 ]; then "$DITTO_BIN_RESOLVED" "$ORIGINAL_APP" "$STAGED_APP"; fi
if [ "$HAD_PLIST" -eq 1 ]; then "$DITTO_BIN_RESOLVED" "$ORIGINAL_PLIST" "$STAGED_PLIST"; fi
if [ "$RESTORE_CONFIG" -eq 1 ] && [ "$HAD_CONFIG" -eq 1 ]; then
    "$DITTO_BIN_RESOLVED" "$ORIGINAL_CONFIG" "$STAGED_CONFIG"
fi

ROLLBACK_SUCCEEDED=0
CURRENT_APP_SAVED=0
CURRENT_PLIST_SAVED=0
CURRENT_CONFIG_SAVED=0
RESTORED_APP_INSTALLED=0
RESTORED_PLIST_INSTALLED=0
RESTORED_CONFIG_INSTALLED=0

recover_failed_rollback() {
    local status=$?
    trap - EXIT HUP INT TERM
    if [ "$status" -eq 0 ]; then status=1; fi
    if [ "$ROLLBACK_SUCCEEDED" -eq 1 ]; then return "$status"; fi
    set +e
    machome_warn "回滚未完成，正在恢复回滚前的 Hub 文件"
    /bin/mkdir -p "$CURRENT_DIR/failed-rollback"
    if [ "$RESTORED_APP_INSTALLED" -eq 1 ] && [ -e "$TARGET_APP" ]; then
        /bin/mv "$TARGET_APP" "$CURRENT_DIR/failed-rollback/$APP_NAME"
    fi
    if [ "$CURRENT_APP_SAVED" -eq 1 ] && [ -e "$CURRENT_DIR/$APP_NAME" ]; then
        /bin/mv "$CURRENT_DIR/$APP_NAME" "$TARGET_APP"
    fi
    if [ "$RESTORED_PLIST_INSTALLED" -eq 1 ] && [ -e "$PLIST_PATH" ]; then
        /bin/mv "$PLIST_PATH" "$CURRENT_DIR/failed-rollback/$LABEL.plist"
    fi
    if [ "$CURRENT_PLIST_SAVED" -eq 1 ] && [ -e "$CURRENT_DIR/$LABEL.plist" ]; then
        /bin/mv "$CURRENT_DIR/$LABEL.plist" "$PLIST_PATH"
    fi
    if [ "$RESTORED_CONFIG_INSTALLED" -eq 1 ] && [ -e "$CONFIG_PATH" ]; then
        /bin/mv "$CONFIG_PATH" "$CURRENT_DIR/failed-rollback/modules.json"
    fi
    if [ "$CURRENT_CONFIG_SAVED" -eq 1 ] && [ -e "$CURRENT_DIR/modules.json" ]; then
        /bin/mv "$CURRENT_DIR/modules.json" "$CONFIG_PATH"
    fi
    if [ "$LOAD_AGENT" -eq 1 ]; then
        /bin/launchctl bootout "gui/$(/usr/bin/id -u)/$LABEL" >/dev/null 2>&1
        if [ "$AGENT_WAS_LOADED" -eq 1 ] && [ -f "$PLIST_PATH" ]; then
            /bin/launchctl bootstrap "gui/$(/usr/bin/id -u)" "$PLIST_PATH" >/dev/null 2>&1
        fi
    fi
    cleanup_stage
    exit "$status"
}
trap - EXIT HUP INT TERM
trap recover_failed_rollback EXIT HUP INT TERM

if [ "$LOAD_AGENT" -eq 1 ] && /bin/launchctl print "gui/$(/usr/bin/id -u)/$LABEL" >/dev/null 2>&1; then
    /bin/launchctl bootout "gui/$(/usr/bin/id -u)/$LABEL"
fi

if [ -e "$TARGET_APP" ]; then
    CURRENT_APP_SAVED=1
    /bin/mv "$TARGET_APP" "$CURRENT_DIR/$APP_NAME"
fi
if [ "$HAD_APP" -eq 1 ]; then
    RESTORED_APP_INSTALLED=1
    /bin/mv "$STAGED_APP" "$TARGET_APP"
    "$CODESIGN_BIN_RESOLVED" --verify --deep --strict --verbose=1 "$TARGET_APP" || \
        machome_warn "旧 app 的签名校验未通过；文件已恢复，请人工复核"
fi

if [ -e "$PLIST_PATH" ]; then
    CURRENT_PLIST_SAVED=1
    /bin/mv "$PLIST_PATH" "$CURRENT_DIR/$LABEL.plist"
fi
if [ "$HAD_PLIST" -eq 1 ]; then
    RESTORED_PLIST_INSTALLED=1
    /bin/mv "$STAGED_PLIST" "$PLIST_PATH"
fi

if [ "$CONFIG_WRITTEN" -eq 1 ]; then
    if [ -e "$CONFIG_PATH" ]; then
        CURRENT_CONFIG_SAVED=1
        /bin/mv "$CONFIG_PATH" "$CURRENT_DIR/modules.json"
    fi
elif [ "$RESTORE_CONFIG" -eq 1 ] && [ "$HAD_CONFIG" -eq 1 ]; then
    if [ -e "$CONFIG_PATH" ]; then
        CURRENT_CONFIG_SAVED=1
        /bin/mv "$CONFIG_PATH" "$CURRENT_DIR/modules.json"
    fi
    RESTORED_CONFIG_INSTALLED=1
    /bin/mv "$STAGED_CONFIG" "$CONFIG_PATH"
    /bin/chmod 0600 "$CONFIG_PATH"
fi

if [ "$LOAD_AGENT" -eq 1 ] && [ -f "$PLIST_PATH" ]; then
    /bin/launchctl bootstrap "gui/$(/usr/bin/id -u)" "$PLIST_PATH"
    /bin/launchctl print "gui/$(/usr/bin/id -u)/$LABEL" >/dev/null
fi

ROLLBACK_SUCCEEDED=1
trap - EXIT HUP INT TERM
cleanup_stage
machome_note "回滚完成；被替换的当前文件保留在：$CURRENT_DIR"
