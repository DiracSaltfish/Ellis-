# QueryKline 月线对齐证据

- Scope: 互联网模式、SSE 单代码 `510300`、公开周期 `cyc_type=10010`（月K线）、单月窄窗口
  `20260701–20260731`、`cq_flag=0`、`auto_complete=1`、`begin_time=end_time=0`。
  季线（10011）、年线（10012）与分钟族（10000–10007）不在本范围，代码继续显式失败。
- PDF: C++ 手册 PDF 页 33–34（正文 25–26）`QueryKline`/`ReqKline`，手册标注"托管机房和
  互联网模式适用"；PDF 页 64–65（正文 56–57）`MDDatatype` 表 `kMonthKline 枚举(=10010)
  月 K 线`；输出结构 `MDKLine` 见第 5 章（PDF 页约 68）。本轮已用 pypdf 重新抽取上述四页
  正文核对，字段与既有日线/周线契约一致。
- Header delta: V1.0.8 `tgw_datatype.h:275` 明确 `kMonthKline = 10010` 适用
  "托管机房模式、互联网模式"；`ReqKline` pack(1) 本地 `sizeof=71` 不变，`MDKLine`
  字段集不变。本轮无新增 ABI 差异。
- Linux oracle: 2026-08-26 在 bj 用 Linux x86 官方 SDK 做只读月线查询（执行前后
  `galaxy-relay` 均为 inactive；凭据仅从远端受保护配置读取；以 galaxyrelay 用户运行
  `/opt/galaxy-relay/venv/bin/python`；每次查询均走官方 `Close()`）。因执行方两次操作失误
  （第一次漏传 `LD_PRELOAD` 未生成捕获、第二次临时提取脚本未解 WebSocket mask），共进行三次
  完全同参的最小查询，三次结果完全一致：登录 true、错误码为整数 0、返回 list 长度 1；
  列排序后为 `close_price, high_price, kline_time, low_price, market_type, open_price,
  orig_time, security_code, value_trade, variety_category, volume_trade`，类型为
  10 个 int + 1 个 str；不变量：distinct `market_type=[101]`、`kline_time` 位数为 8 位
  （yyyyMMdd 形态）、`orig_time` 恒 0、`variety_category` 恒 0。未记录任何业务值。
- Wire: 对一次官方客户端会话做脱敏 SSL_write/SSL_read 取证（原始捕获在分析后立即销毁）：
  push 路径 `/amd/dgw/push` 登录后，独立 query WSS `/amd/dgw/dgw2_query` 完成登录鉴权；
  method=`ReqGetKline`；param key 顺序为 `security_code, market_type, cq_flag,
  auto_complete, period_type, begin_date, end_date, begin_time, end_time,
  QueryBandWidth`；从请求帧直接提取到公共 `cyc_type=10010` 被转换为 wire
  `period_type=10102`；响应单包 `status=0`、tag=`10102`、`pack_num=1/all_pack_num=1`；
  `data` 为字符串数组（1 行 × 9 个 CSV 槽，全部为整数字符串形态）；随后发送
  `ReqGetComplete` 并双向正常关闭。时序与日线/周线契约完全一致。
- Arm: `_protocol.py` 的 `VERIFIED_KLINE_WIRE_TYPES` 扩为 `{10008:10100, 10009:10101,
  10010:10102}`，未列周期仍抛 `NotImplementedError`；`parse_kline_packets(packets,
  expected_tag)` 契约注释更新。`_backend.py` 无需改动（期望 tag 由 `kline_wire_period`
  推导）。`tools/live_smoke.py` 的 `--cyc-type` 帮助文本补充月线取值。
  工具侧按工作流 §4.3 在 `tools/oracle/remote_sdk_oracle.py` 新增独立 `--kind month_kline`
  （并参数化 `--cyc-type/--begin-date/--end-date`，默认值保持原日线样本不变）；
  `analyze_ssl_write_capture.py` 对 `ReqGetKline` 帧新增 `request_period_type` 枚举值输出
  （协议元数据，非业务值），避免后续 Agent 再写临时提取脚本。
- Tests: `python -m unittest discover -s tests -v` 共 27 项全部通过（原 26 + 新增月线
  映射/envelope key 顺序/tag 测试含三包乱序重组与逐列类型断言；未验证周期拒绝测试更新为
  `(10000,10007,10011,10012,9999)` 并锁定映射表键位 `[10008,10009,10010]`）；
  `python -m compileall -q src/python examples tools` 通过。
- Live diff: Linux 错误码 0、1 行、11 列（10 int + 1 str）。Mac 同参复验由主验收 Agent 以
  独立授权账号经 `--username-stdin` 完成：登录成功、`QueryKline error=0`、rows=1、列集合与
  Linux 官方 11 列完全一致，`security_code` 为 str、其余 10 列均为 int。全程未比对或保存任何
  原始行情值。备注：本执行 Agent 先前用仓库默认授权配置发起的一次尝试在 push 登录鉴权成功后，
  query WSS 被服务端以 `1000 / accept conn active close` 回收——与既往登记的准入/流控行为
  一致；按工作流立即停止密集重试，未做第二次重试，该结果保留为流控证据而非 parser 失败。
- Cleanup: bj 上 `/tmp/tgw_month_oracle`（oracle 副本、interposer `.so`、原始捕获、分析
  输出及 `__pycache__`）已删除并复核不存在；`galaxy-relay` 任务前后均为 inactive；本地临时
  PDF 提取 venv 与全部 `__pycache__` 已删除；仓库内无账号、token、MAC、原始行情或捕获文件。
- Proposed status: `LIVE_ALIGNED(monthly only; SSE 510300 sample)`。不申请
  `PILOT_READY`（无重连、资源与持续观测验收）；最终状态由验收者复核后提升。
- Open risks: 季/年与分钟族周期仍未验证且显式失败；query 通道准入规则未知，本轮再次观测到
  `1000 accept conn active close`（换账号后复验成功，说明与构造无关，但准入条件仍未建模）；
  月线跨多月窗口的多包分页仅合成测试覆盖、线上单包样本；其它市场/代码未取样；TLS 服务端仍为
  旧 profile；自动重连仍不存在。
