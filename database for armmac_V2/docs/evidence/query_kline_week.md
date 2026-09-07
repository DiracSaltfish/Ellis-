# QueryKline 周线对齐证据

- Scope: 互联网模式、SSE 单代码 `510300`、公开周期 `cyc_type=10009`（周K线）、单周窗口
  `20260817–20260821`、`cq_flag=0`、`auto_complete=1`、`begin_time=end_time=0`、
  `return_df_format=False`。分钟/月/季/年周期不在本范围。
- PDF: C++ 手册 PDF 页 33–34（正文 25–26）`QueryKline`/`ReqKline`；PDF 页 64–65
  `MDDatatype` 表 `kWeekKline=10009` 周K线；输出结构 `MDKLine` 见 §5.1（PDF 页约 68）。
  手册标注 K 线查询"托管机房和互联网模式适用"。
- Header delta: V1.0.8 `tgw_struct.h` 的 `ReqKline` 与 PDF 字段一致（pack(1)，本地
  `sizeof=71` 不变）；V1.0.8 `tgw_datatype.h` 明确 `kWeekKline=10009` 适用
  "托管机房模式、互联网模式"。本轮无新增 ABI 差异。
- Linux oracle: 2026-08-26 在 bj 用 Linux x86 官方 SDK 做一次独立只读查询（执行前后
  `galaxy-relay` 均为 inactive；凭据仅从远端受保护配置读取）：登录 true、查询错误码为
  整数 0、返回 list 长度 1；列排序后为 `close_price, high_price, kline_time, low_price,
  market_type, open_price, orig_time, security_code, value_trade, variety_category,
  volume_trade`，类型为 10 个 int + 1 个 str；不变量：所有行同列集合、distinct
  market_type=[101]、`orig_time` 位数为常量 1 位（即恒 0）、`kline_time` 位数为 8 位
  （yyyyMMdd 形态）。未记录任何业务值。
- Wire: 对同一次官方客户端会话做了一次脱敏 SSL_write/SSL_read 取证（原始捕获在分析后
  立即销毁）：push 路径 `/amd/dgw/push` 登录后，独立 query WSS `/amd/dgw/dgw2_query`
  完成登录鉴权；method=`ReqGetKline`；param key 顺序为 `security_code, market_type,
  cq_flag, auto_complete, period_type, begin_date, end_date, begin_time, end_time,
  QueryBandWidth`；公共 `cyc_type=10009` 被转换为 wire `period_type=10101`；响应单包
  `status=0`、tag=`10101`；`data` 为字符串数组，单行 9 个 CSV 槽且均为整数字符串形态；
  公开解析保持代码为 `str`、其余 8 槽为 `int`；随后发送 `ReqGetComplete` 并双向正常关闭。
  时序与日线契约完全一致。
- Arm: `_protocol.py` 新增 `VERIFIED_KLINE_WIRE_TYPES={10008:10100, 10009:10101}` 与
  `kline_wire_period()`；`build_kline_request` 仅按该表映射，未列周期抛
  `NotImplementedError`；`parse_kline_packets(packets, expected_tag)` 改为必须显式传入
  已验证 tag；`_backend.py` 按请求周期推导期望 tag。`tools/live_smoke.py` 增加
  `--cyc-type/--begin-date/--end-date` 以便复跑形状输出。
- Tests: `python -m unittest discover -s tests -v` 共 26 项全部通过（原 23 + 本轮新增
  周线映射/tag 测试、未验证周期拒绝测试（含映射表键位锁定）、错误周期 tag 拒绝测试）；
  `python -m compileall -q src/python examples tools` 通过。
- Live diff: 同参（510300/SSE/10009/20260817–20260821/cq_flag=0/auto_complete=1/时间 0）
  低频顺序各查一次：Linux 错误码 0、1 行、11 列（10 int + 1 str）；Mac 登录成功、错误码
  0、1 行、列集合与逐列 Python 类型完全一致；`orig_time=0`、`variety_category=0` 常量
  语义两端一致。全程未比对或保存任何原始行情值。备注：本次 Mac 复跑工具在查询成功并打印
  形状后因当次编辑引入的 CLI 参数缺陷触发异常分支，但该分支仍执行了 `Close()`；工具随后
  已修复并仅离线验证，未再发起服务器请求。
- Cleanup: bj 上原始捕获、interposer `.so`、临时脚本（含 `__pycache__`）已删除；
  `galaxy-relay` 任务前后均为 inactive；本地临时脚本目录已删除。
- Accepted status: `LIVE_ALIGNED(weekly only; SSE 510300 sample)`；验收者已复核实现并运行全部
  离线协议/ABI 测试。线上结论仍只覆盖本文件列出的同参样本；
  不申请 `PILOT_READY`（无重连/资源/持续观测验收）。
- Open risks: 月/季/年及分钟族周期仍未验证且显式失败；查询通道准入此前对其它账号/端点
  出现过 `1000 active close`，本轮虽通过但不代表准入规则已知；周线多包分页、跨多周窗口、
  其它市场/代码尚未取样；TLS 服务端仍为旧 profile；自动重连仍不存在。
