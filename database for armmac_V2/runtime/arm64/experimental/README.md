# 运行二进制说明

- `lib/libtgw_core.dylib`：Mach-O arm64 C++ 骨架动态库。
- `bin/tgw_demo`：Mach-O arm64 TCP/本地状态机演示程序。

两者均不包含真实 TGW 服务端鉴权和行情协议，仅供后续原生化开发、加载测试和 ABI 探针。
当前可用的真实网络实现通过 Python wheel 交付，构建结果位于项目根目录 `dist/`。
