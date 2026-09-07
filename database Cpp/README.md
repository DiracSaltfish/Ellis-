# TGW 纯 C++20 macOS ARM64 客户端

本目录是独立于 `database for armmac_V2` 的纯 C++ 实现。它不导入 Python、
不链接 CPython、不加载原项目中的实验 dylib，也不在运行时调用原项目文件。

## 结论与实现范围

原项目的生产主线并不是“C++ 底层 + Python 封装”。真实可用链路位于
`src/python/tgw_macos`，TLS、RFC 6455 WebSocket、登录鉴权 JSON、Zstandard
解压、请求路由和数据包解析均由 Python 实现；`native/experimental` 只是 TCP/
状态机骨架，原文档明确禁止把它用于真实鉴权或行情。

本项目把当前已验证的 internet-mode 链路全部改写为原生 C++20：

- OpenSSL TLS 1.0–1.2 专用上下文、CA 链与 `www.dgw.com` 主机名校验；
- RFC 6455 upgrade、客户端 masking、分片、ping/pong/close；
- `ReqLogon` 账号密码鉴权及 token 会话；
- push 长连接和 `dgw1_query`/`dgw2_query` 一次性查询连接；
- Zstandard 和 `0x59 + Zstandard` 推送、多 JSON 对象流；
- 订阅/退订、K 线、历史 L1、ThirdInfo、ETF 信息、证券信息、除权因子、
  全市场代码表协议；
- 强类型 C++ 返回对象以及原始 JSON push/ThirdInfo 行；
- 本地 `MMDDHHmmSS + sequence` task id 和官方错误码文本。

已验证边界没有被擅自扩大。未知订阅类型、未取证 K 线周期、未验证历史快照
目标和未验证证券信息组合仍会在本地明确失败。

## 构建

要求 Apple Silicon macOS、CMake 3.24+、C++20 编译器，以及本机原生库：

```bash
brew install cmake openssl@3 zstd simdjson pkg-config
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j
ctest --test-dir build --output-on-failure
```

产物：

- `build/libtgw_cpp.a`：C++ 开发库；设置 `BUILD_SHARED_LIBS=ON` 可构建 dylib；
- `build/tgw_pull`：凭据安全的真实服务器拉取/烟测工具；
- `build/tgw_type_audit`：不输出字段内容的真实类型、范围、空值和混型审计；
- `build/tgw_protocol_tests`：不联网协议测试。

## 最小使用示例

```cpp
#include <tgw/client.hpp>
#include <iostream>

int main() {
    auto config = tgw::load_ini_config("/secure/path/galaxy_account.ini");
    tgw::Session session(std::move(config));
    const auto login = session.connect_and_login();
    if (!login.authenticated) return 1;

    tgw::KlineRequest request{
        "510300", 101, 0, 1, 10008, 20260825, 20260825, 0, 0
    };
    const auto rows = session.query_kline(request);
    std::cout << rows.size() << '\n';
}
```

真实验证命令只输出脱敏摘要：

```bash
./build/tgw_pull --config /secure/path/galaxy_account.ini login
./build/tgw_pull --config /secure/path/galaxy_account.ini calendar
./build/tgw_pull --config /secure/path/galaxy_account.ini kline-daily
```

不要把密码放进命令行、源码或日志。INI 格式与原项目一致；推荐权限 `0600`。

## 文档

- [C++ API](docs/API.md)
- [协议与鉴权](docs/PROTOCOL.md)
- [开发与维护](docs/DEVELOPMENT.md)
- [Python 到 C++ 迁移](docs/MIGRATION.md)
- [真实服务器验证记录](docs/VALIDATION.md)
- [真实数据格式与类型审计](docs/TYPE_AUDIT.md)

该实现只覆盖只读 internet-mode 范围，不实现 coloc/QTCP/RTCP、自动重连、
写操作或未取证的接口。使用授权、数据权限、频率和生产风险仍由调用方负责。
