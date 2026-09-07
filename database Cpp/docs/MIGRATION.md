# Python 主线到 C++ API 迁移

## 1. 概念映射

| Python 主线 | C++ 原生 |
|---|---|
| `Cfg().set(...)` | `Config` 或 `load_ini_config()` |
| 全局 `Login/Close` | 实例化 `Session`，`connect_and_login()/close()` |
| `GetVersion()` | `tgw::kClientVersion` / `Config.client_version` |
| `GetTaskID()` | `tgw::next_task_id()` |
| `GetErrorMsg(code)` | `tgw::error_message(code)` |
| `Subscribe/UnSubscribe` | `Session::subscribe/unsubscribe` |
| `ReceiveRawEvent` dict | `receive_raw_event` JSON string |
| `QueryKline` list/DataFrame | `vector<KlineRow>` |
| `QuerySnapshot` tuple | `QueryResult<SnapshotRow>` |
| `SetThirdInfoParam + QueryThirdInfo` | 一次传 `vector<pair<string,string>>` |
| `QueryETFInfo` | `vector<EtfRecord>` |
| `QuerySecuritiesInfo` | `vector<SecuritiesInfoRow>` |
| `QueryExFactorTable` | `vector<ExFactorRow>` |
| `QueryCodeTable` | `vector<CodeTableRow>` |

C++ 不保留 Python 全局 backend；可以创建多个独立 `Session`，但同一账号的并发登录
限制仍由服务器决定。除非明确理解影响，不设置 `force_logout=true`。

## 2. 输出协议差异

- 不提供 DataFrame；固定 schema 用强类型结构，动态 schema 用单行 JSON string；
- 价格/成交等 raw 整数不自动换算，避免无证据的单位推断；
- Python `int` 被拆成头文件规定的 `uint8_t`/`uint32_t`/`int64_t`，越界立即失败；
- wire 不存在的字段用 `optional`，未知快照尾字段保留 raw string，不制造 0 或丢弃；
- 复权因子同时提供 `double` 和准确十进制原文；
- 没有 Python `query_spi`，当前 C++ 查询为同步；业务可在线程池/协程上封装；
- 原始 push 不构造 dict，避免高频 Python object 分配；
- 公开异常分为 transport/timeout/protocol/invalid_argument，不返回含糊的 `False`。

## 3. CMake 集成

同仓库：

```cmake
add_subdirectory("/absolute/path/database Cpp" tgw-cpp-build)
target_link_libraries(your_app PRIVATE tgw::cpp)
```

或安装后使用导出的 CMake target。静态链接仍需系统 OpenSSL、zstd、simdjson。

## 4. 迁移检查表

1. 把凭据留在 `0600` INI，不复制到 C++ 源；
2. 明确设置 CA 安装路径；
3. 先跑 `login`，再跑与业务完全同参的历史查询；
4. 对 raw 数值做业务侧、证据化单位转换；
5. 订阅消费者处理 10,000 队列上限和断线恢复；
6. 捕获三类库异常和 `std::invalid_argument`；
7. 对服务器正常 close/当前不可用接口保留重试和监控，不把失败静默转空数据。
