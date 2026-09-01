#!/bin/zsh
set -euo pipefail

project_root=${0:A:h:h}
source_root=${1:-"${project_root:h}/database for armmac"}
python_source=${2:-/Users/ellis/miniconda3/envs/ag/bin/python}
temporary_root=$(mktemp -d)
trap 'rm -rf "$temporary_root"' EXIT

"$python_source" -m venv "$project_root/.venv"
"$project_root/.venv/bin/python" -m pip install --upgrade pip build
cp -R "$source_root" "$temporary_root/database-for-armmac"
cd "$temporary_root/database-for-armmac"
mkdir "$temporary_root/wheelhouse"
"$project_root/.venv/bin/python" -m build --wheel --outdir "$temporary_root/wheelhouse"
"$project_root/.venv/bin/python" -m pip install "$temporary_root"/wheelhouse/*.whl
"$project_root/.venv/bin/python" -m pip install 'websockets>=15,<17'
"$project_root/.venv/bin/python" -c 'import platform,sys,tgw_macos; print(sys.version); print(platform.machine()); print(tgw_macos.__version__)'
