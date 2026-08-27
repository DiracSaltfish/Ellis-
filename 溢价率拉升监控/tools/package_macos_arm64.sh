#!/bin/zsh
set -euo pipefail

script_dir=${0:A:h}
project_root=${script_dir:h}
build_dir=${1:-${project_root}/build/macos-arm64-release-make}
dist_dir=${project_root}/dist
qt_bin=/opt/homebrew/opt/qt/bin
qt_plugin_dir=$(/opt/homebrew/opt/qtbase/bin/qtpaths --plugin-dir 2>/dev/null || true)
package_name='ETF溢价率拉升监控-macOS-arm64.zip'
codesign_identity=${CODESIGN_IDENTITY:-$(
    /usr/bin/security find-identity -v -p codesigning 2>/dev/null \
        | /usr/bin/awk '/^[[:space:]]*[0-9]+\)/ && $0 !~ /CSSMERR/ { print $2; exit }'
)}
if [[ -z ${codesign_identity} ]]; then
    codesign_identity='-'
fi

if [[ ! -x ${build_dir}/etf-premium-core ]]; then
    print -u2 "Release core 不存在：${build_dir}/etf-premium-core"
    exit 2
fi
if [[ ! -x ${qt_bin}/macdeployqt ]]; then
    print -u2 "找不到 macdeployqt：${qt_bin}/macdeployqt"
    exit 2
fi

package_tmp=$(mktemp -d "${TMPDIR:-/tmp}/etf-premium-package.XXXXXX")
trap 'rm -rf -- "${package_tmp}"' EXIT
stage=${package_tmp}/macos-arm64

cmake --install "${build_dir}" --prefix "${stage}"

for app in etf-premium-console.app etf-premium-client.app; do
    ${qt_bin}/macdeployqt "${stage}/${app}" -always-overwrite -verbose=1
    # 保留 offscreen 平台插件，避免在 Codex/自动化环境设置 QT_QPA_PLATFORM=offscreen 时
    # Qt 在 QApplication 初始化阶段因找不到平台插件直接 abort。
    if [[ -n ${qt_plugin_dir} && -f ${qt_plugin_dir}/platforms/libqoffscreen.dylib ]]; then
        cp -f "${qt_plugin_dir}/platforms/libqoffscreen.dylib" \
            "${stage}/${app}/Contents/PlugIns/platforms/libqoffscreen.dylib"
    fi
    /usr/bin/codesign --force --deep --sign "${codesign_identity}" "${stage}/${app}"
    /usr/bin/codesign --verify --deep --strict --verbose=1 "${stage}/${app}"
done
/usr/bin/codesign --force --sign "${codesign_identity}" "${stage}/bin/etf-premium-core"
/usr/bin/codesign --verify --strict --verbose=1 "${stage}/bin/etf-premium-core"

/usr/bin/plutil -extract NSLocalNetworkUsageDescription raw \
    "${stage}/etf-premium-client.app/Contents/Info.plist" >/dev/null
"${stage}/etf-premium-client.app/Contents/MacOS/etf-premium-client" --help 2>&1 \
    | /usr/bin/grep -q -- '--settings'
"${stage}/etf-premium-client.app/Contents/MacOS/etf-premium-client" --help 2>&1 \
    | /usr/bin/grep -q -- '--read-only'

mkdir -p "${dist_dir}"
zip_tmp=${package_tmp}/${package_name}
/usr/bin/ditto -c -k --sequesterRsrc --keepParent "${stage}" "${zip_tmp}"

if [[ -e ${dist_dir}/macos-arm64 ]]; then
    mv "${dist_dir}/macos-arm64" "${package_tmp}/previous-macos-arm64"
fi
if [[ -e ${dist_dir}/${package_name} ]]; then
    mv "${dist_dir}/${package_name}" "${package_tmp}/previous-${package_name}"
fi
mv "${stage}" "${dist_dir}/macos-arm64"
mv "${zip_tmp}" "${dist_dir}/${package_name}"

(
    cd "${dist_dir}"
    /usr/bin/shasum -a 256 "${package_name}" > SHA256SUMS
)

print "发布目录：${dist_dir}/macos-arm64"
print "发布压缩包：${dist_dir}/${package_name}"
print "代码签名：${codesign_identity}"
cat "${dist_dir}/SHA256SUMS"
