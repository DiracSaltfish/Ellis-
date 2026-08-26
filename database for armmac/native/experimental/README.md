# 原生 C++ arm64 骨架（实验用途）

这里的 C++ 代码和 `runtime/arm64/experimental` 中的二进制只完成：arm64 可加载性、TCP
连接探测、API 生命周期骨架与 ctypes 稳定入口。它没有实现厂商 TLS/WebSocket 鉴权、
真实订阅或查询协议；`LOGON_OK` 是本地状态迁移，不是服务端认证。

构建：

```bash
cmake -S native/experimental -B native/experimental/build \
  -DCMAKE_BUILD_TYPE=Release
cmake --build native/experimental/build --config Release
```

生产主线位于 `src/python/tgw_macos`。在原生层完整复刻相同协议并通过 Linux/Mac 同参验收
之前，不得把 `TGW_BACKEND=cpp-skeleton` 用于业务。
