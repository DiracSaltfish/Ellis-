#!/bin/zsh
set -eu
cd "${0:A:h:h}"
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j 4
go build -o build/iopv-server ./cmd/server
if [[ "$(uname -s)" == Darwin ]]; then
 mkdir -p 'dist/内网IOPV管理.app/Contents/MacOS'
 swiftc desktop/Manager.swift -o 'dist/内网IOPV管理.app/Contents/MacOS/IOPVManager' -framework Cocoa -framework WebKit
 python3 - <<'PY'
from pathlib import Path
import plistlib
p=Path('dist/内网IOPV管理.app/Contents/Info.plist')
p.write_bytes(plistlib.dumps({'CFBundleExecutable':'IOPVManager','CFBundleIdentifier':'com.ellis.intranet-iopv.manager','CFBundleName':'内网IOPV管理','CFBundlePackageType':'APPL','NSHighResolutionCapable':True,'NSAppTransportSecurity':{'NSAllowsLocalNetworking':True,'NSAllowsArbitraryLoads':True}}))
PY
fi
