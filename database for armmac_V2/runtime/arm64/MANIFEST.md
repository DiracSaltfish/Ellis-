# arm64 运行产物清单

清单复核日期：2026-08-30；编译器：AppleClang；骨架架构：Mach-O arm64。

| 文件 | SHA-256 | 状态 |
|---|---|---|
| `experimental/lib/libtgw_core.dylib` | `9322fa57edd325068efc1de4eec49850b816d8ccdb5bf04efb85776cba91c66e` | TCP/本地状态机骨架 |
| `experimental/bin/tgw_demo` | `b12e19bb627f2d621ead2b57a31beb4a9c45ac6e695b25d022e37825dd075dbf` | 骨架演示；RPATH=`@executable_path/../lib` |

真实 TGW 网络实现的 wheel：`dist/tgw_macos_arm64-1.0.9.2.6-py3-none-any.whl`，
SHA-256 `4f5b4b80629b8c23bebca0b71ff3f2e7df3750a98c538e6cd22de468907825ca`。

本清单中的 dylib/demo 不是生产 SDK；详见 `experimental/README.md`。
