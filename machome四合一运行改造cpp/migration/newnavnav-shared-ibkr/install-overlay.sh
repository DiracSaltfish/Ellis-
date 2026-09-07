#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
target_root=""
python_bin="/Users/ellis/miniconda3/bin/python3"
apply=0

usage() {
    echo "usage: $0 [--apply] --target-root PATH [--python PATH]"
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --apply) apply=1; shift ;;
        --target-root) target_root=${2-}; shift 2 ;;
        --python) python_bin=${2-}; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
    esac
done

if [ -z "$target_root" ]; then
    echo "--target-root is required" >&2
    exit 2
fi
target_scripts="$target_root/scripts"
if [ ! -d "$target_scripts" ]; then
    echo "target scripts directory does not exist: $target_scripts" >&2
    exit 2
fi
if [ ! -x "$python_bin" ]; then
    echo "python is not executable: $python_bin" >&2
    exit 2
fi

files="
machome_ibkr_bridge_client.py
ib_us_uploader_support.py
private_valuation_uploader.py
private_513350_valuation_uploader.py
private_xop_family_uploader.py
private_159605_valuation_uploader.py
private_china_internet_valuation_uploader.py
private_164824_valuation_uploader.py
private_nasdaq_valuation_uploader.py
"

for file in $files; do
    if [ ! -f "$script_dir/scripts/$file" ]; then
        echo "overlay source missing: $file" >&2
        exit 2
    fi
    if [ "$file" != "machome_ibkr_bridge_client.py" ] && [ ! -f "$target_scripts/$file" ]; then
        echo "target uploader missing: $target_scripts/$file" >&2
        exit 2
    fi
done

echo "mode: $([ "$apply" -eq 1 ] && echo apply || echo dry-run)"
echo "source: $script_dir/scripts"
echo "target: $target_scripts"
echo "files:"
for file in $files; do
    echo "  $file"
done

if [ "$apply" -ne 1 ]; then
    echo "no files changed; pass --apply only in an approved maintenance window"
    exit 0
fi

stamp=$(date -u +%Y%m%dT%H%M%SZ)
backup_dir="$target_scripts/.machome-ibkr-overlay-backups/$stamp"
mkdir -p "$backup_dir"
for file in $files; do
    if [ -f "$target_scripts/$file" ]; then
        cp -p "$target_scripts/$file" "$backup_dir/$file"
    fi
done

for file in $files; do
    temporary="$target_scripts/.$file.machome-new"
    cp "$script_dir/scripts/$file" "$temporary"
    chmod 0644 "$temporary"
    mv "$temporary" "$target_scripts/$file"
done

PYTHONDONTWRITEBYTECODE=1 "$python_bin" -m py_compile \
    "$target_scripts/machome_ibkr_bridge_client.py" \
    "$target_scripts/ib_us_uploader_support.py" \
    "$target_scripts/private_valuation_uploader.py" \
    "$target_scripts/private_513350_valuation_uploader.py" \
    "$target_scripts/private_xop_family_uploader.py" \
    "$target_scripts/private_159605_valuation_uploader.py" \
    "$target_scripts/private_china_internet_valuation_uploader.py" \
    "$target_scripts/private_164824_valuation_uploader.py" \
    "$target_scripts/private_nasdaq_valuation_uploader.py"

echo "overlay installed; backup: $backup_dir"
