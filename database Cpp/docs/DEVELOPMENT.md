# 开发与维护指南

## 1. 模块结构

```text
include/tgw/
  types.hpp       公共强类型、异常、Config
  protocol.hpp    wire builder/parser API
  session.hpp     会话和高层查询/订阅 API
src/
  config.cpp      INI、环境覆盖、task id、错误码
  websocket.cpp   TCP/TLS/RFC6455、reader/waiter/heartbeat
  protocol.cpp    JSON envelope、ZSTD、严格数据解析
  session.cpp     鉴权状态机、push/query 路由、完成/补包
apps/tgw_pull.cpp 脱敏在线烟测
apps/tgw_type_audit.cpp 脱敏类型/范围在线审计
tests/            无凭据离线协议回归
```

`src/internal.hpp` 不是 ABI。公共 ABI 只有 `include/tgw`。

## 2. 依赖选择

- OpenSSL：TLS、证书校验、SHA-1 upgrade accept、加密随机数；
- libzstd：流式解压未知 content-size frame；
- simdjson DOM：快速严格 JSON 类型检查；
- POSIX/macOS：`getaddrinfo`、nonblocking connect+poll、AF_LINK MAC。

库没有 Boost、Python、pandas、SWIG、ctypes 或官方 x86 二进制依赖。

## 3. 修改协议的流程

新增/扩大接口前必须同时具备：

1. 官方头文件或手册的公共类型/语义；
2. 授权官方客户端的同参动态请求/响应证据；
3. request key、key order、wire enum、tag、route、completion 的明确记录；
4. 成功、错误、超时、分包/关闭形状；
5. 离线 fixture 测试；
6. 当前 Mac C++ live 结果和 `tgw_type_audit` 类型/范围结果；
7. `API.md`、`PROTOCOL.md`、`TYPE_AUDIT.md`、`VALIDATION.md` 同步更新。

不要从邻近 enum 推导 wire 值，不要因服务端偶然接受就声明 SDK 合约。

## 4. 构建与质量门槛

普通构建：

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=RelWithDebInfo
cmake --build build -j
ctest --test-dir build --output-on-failure
```

内存安全构建：

```bash
cmake -S . -B build-asan -DCMAKE_BUILD_TYPE=Debug \
  -DCMAKE_CXX_FLAGS='-fsanitize=address -fno-omit-frame-pointer' \
  -DCMAKE_EXE_LINKER_FLAGS='-fsanitize=address'
cmake --build build-asan -j
ASAN_OPTIONS=detect_leaks=0 ./build-asan/tgw_protocol_tests
```

在线测试也应先用 ASan `login`，再做一个低频历史查询。严禁把 token、密码、原始
登录响应、原始抓包或未脱敏行情提交到仓库。

## 5. 错误与资源维护

- `request_many` 必须先消费已排队消息，再处理随后到达的 close/error；否则一次性
  query 的“响应后立即 close”会形成竞态。
- SSL 对象释放必须晚于 I/O thread join。
- macOS socket 设置 `SO_NOSIGPIPE`，防止正常 close 期间进程被 SIGPIPE 终止。
- reader 只传递 JSON 字符串，parser 在消费线程中短生命周期创建，避免 simdjson
  element 跨 parser 生命周期。
- token 和内部密码在 `close` 中覆盖后清空；异常文本不得包含 request payload。
- event queue 满时丢最旧项，业务若要求不丢必须在更上层及时消费或落盘。

## 6. 性能说明

网络层为每连接单 reader、请求 waiter 直接按 id 路由，避免全局轮询。解析使用
simdjson，固定数据转为连续 `vector`/`array`/静态结构体。ETF 与 43 字段证券信息
也使用编译期固定类型，不在高层 API 暴露字典或 variant。需要列式性能的调用方可在
一次循环中搬运到自己的 SoA/Arrow buffer，无需经过 Python object 或 DataFrame。

当前实现是阻塞同步 API。若未来增加协程，应复用同一严格 protocol parser，并保持
鉴权、route、id 序列和完成规则不变。
