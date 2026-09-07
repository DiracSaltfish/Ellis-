# 当前 Mac 构建与真实服务器验证

验证日期：2026-08-30（Asia/Shanghai）  
平台：Apple Silicon macOS，Apple Clang 21，C++20  
依赖：OpenSSL 3.6.3、libzstd 1.5.7、simdjson 4.6.4

## 1. 离线结果

```text
cmake configure/build: PASS
ctest: 1/1 PASS
tgw_protocol_tests: PASS
```

覆盖登录 envelope、订阅 wire enum、`0x59+ZSTD` 多对象、K 线分包解析、历史
快照 `DataEmpty -> -76` 和未验证周期拒绝。

## 2. AddressSanitizer

首次在线关闭测试发现并修复了 OpenSSL reader 与 `SSL_free` 的释放竞态；最终关闭
顺序改为 interrupt/join/free，并设置 `SO_NOSIGPIPE`。修复后下列在线成功用例均在
ASan 构建上运行，没有新的 ASan 报告：

| 命令 | 真实结果 |
|---|---|
| `login` | `authenticated=true`, tag=`OnRspLogon` |
| `calendar` | 18 行，ThirdInfo object schema |
| `kline-daily` | 1 行，`510300`，首日 `20260825` |
| `snapshot-sse` | 11 行，error code 0 |
| `etf-sse` | 1 条 ETF，300 条成分 |
| `secinfo-pair` | 2 行，每行严格 43 字段 |
| `ex-factor` | 33 行，日期 19910403..20260612，保留 double 与小数原文 |
| `type-audit` | 上述接口同会话全部成功，逐字段无混型或越界 |

凭据来自工作区中已有、未复制的本机授权 INI；输出没有账号、密码、token、MAC、
主机地址或原始行情 payload。

## 3. 加强类型审计

新增 `tgw_type_audit` 在不打印字段内容的条件下记录 wire 容器、C++ 类型、计数、范围、
空字符串和数字文本。2026-08-30 的完整一次运行结果：

- 登录、日历、日 K、快照、证券信息、ETF、复权因子全部成功；
- 行数分别为 18、1、11、2、1+300、33；
- Python 会统一显示为 `int` 的字段已按公开头文件区分成 `uint8_t`、`uint32_t`、
  `int64_t`；
- 快照位置 20..23 和 29..34 实际含数字文本，已改为 raw string 保留；
- K 线/快照 wire 缺失字段改成 `optional`，不再制造 0；
- 复权小数保留准确 token，避免只剩 binary64 舍入值。

同参原 Python 在线复拉成功确认：日 K 各字段只显示 Python `int`，证券信息的市场、
日期、金额也全部只显示 Python `int`，ETF 返回 1 条基本信息和 300 条成分。重复拉取
日历、快照、复权因子时服务器间歇返回正常关闭
`1000 / accept conn active close`；同日 C++ 完整审计已成功取得这些数据，所以该关闭
记录为外部时序状态，不作为类型差异。

最终增加字符串容量检查后又逐接口复拉：日历 18 行、快照 11 行、复权因子 33 行均
成功；快照第一次仍遇到上述正常关闭，立即使用同一最终二进制重试后成功 11 行。

完整字段矩阵和真实范围见 [TYPE_AUDIT.md](TYPE_AUDIT.md)。

## 4. 仍未作为成功验收的接口

代码表没有作为成功验收项：原项目已记录服务端全市场响应持续缺包，Linux 为 `-83`，
Mac 为缺包超时。C++ 已实现同一补包规则，但没有伪造完整成功结果。

## 5. 重现命令

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=RelWithDebInfo
cmake --build build -j
ctest --test-dir build --output-on-failure

./build/tgw_pull --config /secure/path/galaxy_account.ini login
./build/tgw_pull --config /secure/path/galaxy_account.ini calendar
./build/tgw_pull --config /secure/path/galaxy_account.ini kline-daily
./build/tgw_pull --config /secure/path/galaxy_account.ini snapshot-sse
./build/tgw_pull --config /secure/path/galaxy_account.ini etf-sse
./build/tgw_pull --config /secure/path/galaxy_account.ini secinfo-pair
./build/tgw_type_audit --config /secure/path/galaxy_account.ini
```

在线命令应低频、串行运行。服务器状态和账号权限会变化，行数只是本次证据，不是永久
固定断言。
