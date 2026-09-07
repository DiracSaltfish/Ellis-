#!/bin/bash

# Shared, side-effect-free helpers for the Machome Hub delivery scripts.
# Individual entry points choose their own `set` flags and mutation policy.

machome_note() {
    printf '[machome] %s\n' "$*"
}

machome_warn() {
    printf '[machome] WARNING: %s\n' "$*" >&2
}

machome_die() {
    printf '[machome] ERROR: %s\n' "$*" >&2
    exit 1
}

machome_print_cmd() {
    local argument
    local quoted
    printf '  +'
    for argument in "$@"; do
        case "$argument" in
            *[!A-Za-z0-9_./:=,@%+-]*)
                quoted=${argument//\'/\'\\\'\'}
                printf " '%s'" "$quoted"
                ;;
            *)
                printf ' %s' "$argument"
                ;;
        esac
    done
    printf '\n'
}

machome_run() {
    if [ "${DRY_RUN:-0}" -eq 1 ]; then
        machome_print_cmd "$@"
        return 0
    fi
    "$@"
}

machome_expand_user_path() {
    local value=$1
    case "$value" in
        '~')
            printf '%s\n' "$HOME"
            ;;
        '~/'*)
            printf '%s/%s\n' "$HOME" "${value#\~/}"
            ;;
        *)
            printf '%s\n' "$value"
            ;;
    esac
}

machome_absolute_path() {
    local value
    value=$(machome_expand_user_path "$1")
    case "$value" in
        /*) printf '%s\n' "$value" ;;
        *) printf '%s/%s\n' "$PWD" "$value" ;;
    esac
}

machome_require_single_line() {
    local label=$1
    local value=$2
    case "$value" in
        *$'\n'*|*$'\r'*) machome_die "$label 不能包含换行符" ;;
    esac
}

machome_assert_safe_target() {
    local label=$1
    local value=$2
    machome_require_single_line "$label" "$value"
    case "$value" in
        ''|'/'|'.'|'..'|"$HOME")
            machome_die "$label 指向过宽或危险路径：$value"
            ;;
        /*) ;;
        *) machome_die "$label 必须是绝对路径：$value" ;;
    esac
}

machome_require_executable() {
    local label=$1
    local value=$2
    if [ ! -x "$value" ]; then
        machome_die "$label 不可执行或不存在：$value"
    fi
}

machome_resolve_command() {
    local value=$1
    if [ -x "$value" ]; then
        printf '%s\n' "$value"
        return 0
    fi
    command -v "$value" 2>/dev/null || return 1
}

machome_xml_escape() {
    printf '%s' "$1" | /usr/bin/sed \
        -e 's/&/\&amp;/g' \
        -e 's/</\&lt;/g' \
        -e 's/>/\&gt;/g' \
        -e 's/"/\&quot;/g' \
        -e "s/'/\&apos;/g"
}

machome_sed_replacement() {
    printf '%s' "$1" | /usr/bin/sed -e 's/[\\&|]/\\&/g'
}

# Print dependencies that cannot be satisfied from the bundle or macOS system
# locations. The function is intentionally read-only and always returns zero;
# callers decide whether an unresolved optional plugin should be removed or
# treated as an error.
machome_unresolved_bundle_dependencies() {
    local bundle=$1
    local binary=$2
    local otool_bin=${3:-/usr/bin/otool}
    local dependency
    local relative
    local candidate
    local dependencies
    dependencies=$("$otool_bin" -L "$binary" 2>/dev/null | /usr/bin/sed -n \
        -e '2,$s/^[[:space:]]*//' \
        -e '2,$s/ (compatibility version.*$//' \
        -e '2,$p')
    while IFS= read -r dependency; do
        [ -n "$dependency" ] || continue
        candidate=
        case "$dependency" in
            /System/Library/*|/usr/lib/*)
                continue
                ;;
            @rpath/*)
                relative=${dependency#@rpath/}
                candidate="$bundle/Contents/Frameworks/$relative"
                ;;
            @loader_path/*)
                relative=${dependency#@loader_path/}
                candidate="$(dirname -- "$binary")/$relative"
                ;;
            @executable_path/*)
                relative=${dependency#@executable_path/}
                candidate="$bundle/Contents/MacOS/$relative"
                ;;
            /*)
                # An absolute non-system dependency is not portable, even when
                # it exists on the build host.
                printf '%s\n' "$dependency"
                continue
                ;;
            *)
                printf '%s\n' "$dependency"
                continue
                ;;
        esac
        [ -e "$candidate" ] || printf '%s\n' "$dependency"
    done <<EOF
$dependencies
EOF
    return 0
}

machome_usage_footer() {
    cat <<'EOF'

安全边界：这些脚本只处理 Machome Hub 自身的构建产物、配置和
com.ellis.machome-hub-agent LaunchAgent；不会改写或启停四套原业务。
EOF
}
