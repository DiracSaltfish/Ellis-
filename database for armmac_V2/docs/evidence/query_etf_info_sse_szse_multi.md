# QueryETFInfo 固定 SSE/SZSE 双 item 同步分支：阻塞记录

- Scope: 本轮计划验收一次**单次同步** `QueryETFInfo`：官方已经分别验收过的
  `SubCodeTableItem{market=101, security_code="510300"}` 与
  `SubCodeTableItem{market=102, security_code="159919"}` 以此顺序组成一个两 item 列表。范围不含
  其它 ETF、反序/重复/空代码、异步 SPI、全市场、实时行情或 wheel 构建。本文不改变既有两个单 item
  的验收结论。

- Static contract: TGW C++ 手册 PDF 页 39（正文 31）`QueryETFInfo` 原型的 `item` 是查询数组首地址，
  `cnt` 是请求个数；页 44（正文 36）的 `IGMDETFInfoSpi::OnMDETFInfo` 也携带记录首地址和 `cnt`。
  V1.0.8 `tgw.h` 与 `tgw_struct.h` 一致：`SubCodeTableItem` 为 pack(1) 的
  `int32_t market` + `char security_code[32]`（36 字节，offset 0/4），接口仅适用于沪深市场。
  官方 Python 1.0.9.2 wrapper 的静态检查确认它接受一个 item 或 list，并把 list 打包为
  `IGMDApi_QueryETFInfo(spi, items, n)`。因此“可传两个 item”由公开 API 证明；实际 `Security`
  串联格式、响应帧数/顺序和完成语义仍必须由本轮 Linux wire 实捕确定，不能从该契约猜测。

- Prior accepted baselines: 单 item SSE 与 SZSE 的独立证据分别在
  [query_etf_info_sse_etf.md](query_etf_info_sse_etf.md) 与
  [query_etf_info_szse_etf.md](query_etf_info_szse_etf.md)。两者都实证了 push 通道、
  `ReqGetETFCodeTableList`、字符串 tag `"111"` 和 `ReqGetCodelistComplete`，但都明确不外推到
  multi item。

- Linux oracle attempt: 2026-08-30，开始前确认 bj 的 `galaxy-relay` 为 `inactive`。使用官方
  x86_64 TGW 1.0.9.2、独立授权账号的非回显 stdin 覆盖和新建 0700 临时目录，执行恰一条
  `--kind etf-info --etf-sse-szse-pair --etf-sync-only` 探针。该工具只构造上述固定顺序的两个
  `SubCodeTableItem`，不会接受泛化的多 item 输入，也不会启动异步收集器。登录阶段返回
  `OnRspLogon` 状态 **-95**，官方 `Login()` 返回 false，工具输出 `query=not_run`；因而
  **没有调用 `QueryETFInfo`，没有发出 ETF 请求或完成消息，也没有得到官方容器、顺序、每 item/
  整体完成语义**。这是凭据账户在服务端的前置权限/可用性阻塞，不能归类为 ETF 多 item 的业务失败。
  按低频纪律未重试。

- Login-only wire control: 对这一次会话的短生命周期 SSL read/write 捕获做脱敏分析后，只发现
  `/amd/dgw/push` 的 `ReqLogon` 与服务端 `OnRspLogon=-95`，随后 close；无
  `ReqGetETFCodeTableList`、无 tag `"111"` 数据响应、无 `ReqGetCodelistComplete`。分析输出仅含
  方法名、路径、键/类型、状态和帧长度，不含帐号、密码、token、MAC、证券代码、行情字段或原始
  capture。

- Mac disposition: 不执行 Mac live，也不修改 `_protocol.py`、`_backend.py` 或 `interface.py`。
  目前 Mac 对任意多 item 的显式拒绝仍是正确的收窄行为：Linux 未能证明双 item 的实际 wire
  `Security` 格式、响应顺序、帧数或 completion 时机。若将来 Linux 官方探针在登录后成功，后续仅可
  按该实捕结果放行上述**固定顺序精确二元组**，其余多 item 仍保持 `NotImplementedError`；不能把
  单 item `code|market` 格式拼接外推为多 item 格式。

- Tooling: `tools/oracle/remote_sdk_oracle.py` 增加受约束的
  `--etf-sse-szse-pair` 旗标，且强制要求 `--kind etf-info --etf-sync-only`。其输出额外仅记录
  回包中 `market_type` 与成分数量的**顺序**（若查询能够运行），不会输出代码或业务值。这个变更是
  后续一次低频 oracle 的安全准备，不代表 Mac API 已经放行。

- Tests: `python3 -m py_compile tools/oracle/remote_sdk_oracle.py` 通过；既有
  `python3 -m unittest tests.test_etf_info_protocol -v` 为 **27/27 通过**，其中保留多 item 显式
  拒绝覆盖。`python3 -m compileall -q src/python examples tools` 通过。全量
  `python3 -m unittest discover -s tests` 本轮为 161 项，出现 **3 failures + 1 error**，均位于
  `test_securities_info_protocol`：共享工作区中另一条 securities-info 多 item 验收正在同时改变
  实现范围，而该测试仍断言旧的 single-item 文案/行为。该失败不涉及 `QueryETFInfo`，未由本轮修改，
  已报告协调者；不把它计入 ETF 双 item 结论。由于没有协议证据，未伪造多 item fixture 或更改其预期。

- Cleanup: 已删除 bj 的专用临时目录（oracle 副本、interposer、capture、分析器和 `.so`），并确认
  目录不存在；结束后再次确认 `galaxy-relay=inactive`。未在仓库写入凭据、原始 capture 或行情值。
  本轮生成的两张 PDF 渲染图和项目内 `__pycache__` 已移出工作区至系统废纸篓，未触碰其它源码或
  证据文件。

- Proposed status: `BLOCKED_PRE_QUERY_PERMISSION`。两个单 item 验收状态不变；固定 SSE/SZSE 双
  item 同步分支仍为 `NOT_ACCEPTED`，等待一次能通过官方登录的 Linux 同参 probe 后再进行 Mac
  实现与同参验收。
