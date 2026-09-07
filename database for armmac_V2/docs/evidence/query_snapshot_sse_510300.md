# QuerySnapshot SSE 510300 历史 L1 对齐证据

- Scope: 仅互联网模式低层 `QuerySnapshot` 的 SSE `510300`，单日 20260825、
  09:30:00.000–09:30:30.000（`begin_time=93000000`、`end_time=93030000`）、
  同步 `return_df_format=False`、`data_type=0`、`level_type=0`。不涉及异步、多包、
  其它日期/代码/市场/data_type/level_type 或盘中实时行情。
- PDF: TGW C++ 手册 PDF 页 34（正文 26）定义互联网/托管均可用的
  `QuerySnapshot(IGMDSnapshotSpi, ReqDefault)`；`ReqDefault` 的日期为 YYYYMMDD、
  时间为 HHmmssSSS、`data_type=0` 为快照。AmazingData 手册 PDF 页 25–26（正文 21–22）
  对历史 `query_snapshot` 规定 8 位日期、8/9 位 HHmmssSSS 时间和股票/ETF 支持范围；
  本轮只验其低层请求，不外推 AmazingData 的 dict-of-DataFrame 封装。
- Header delta: V1.0.8 Linux `tgw_struct.h` 的 pack(1) `ReqDefault` 含
  `security_code[38]`、`market_type:uint8`、`date/begin_time/end_time:uint32`、
  `data_type:uint16`、`level_type:uint16`；大小 55，后者 offset 53，构造默认均为 0。
  C++ PDF 表格只列至 `data_type`，故实现以发行 header 为准；官方 Linux Python 请求对象也
  观测到两个默认值均为 0。
- Linux oracle: 2026-08-30 以 `flock -n /tmp/tgw_official_acceptance.lock` 串行保护，确认
  `galaxy-relay` 为 inactive 后，使用 x86 官方 SDK、`galaxyrelay` 身份、受保护配置密码和
  stdin 一次性用户名运行一个同步请求。登录成功；错误码为整数 0；返回 `list` 11 行，每行
  57 个低层公开字段，`security_code`、`trading_phase_code` 为 `str`，其余 55 个字段为
  `int`。所有行键集合一致、值均为标量、`market_type` 仅 101、`variety_category` 仅 0、
  `orig_time` 均为 17 位整数；交易阶段仅记录为单一字符串类别，未保存其业务值。
- Wire: 同一官方会话做一次脱敏 SSL_write/SSL_read 分析，原始 capture 只在远端临时目录内
  存在并立即删除。推送登录后，查询走 `/amd/dgw/dgw1_query`；请求 method 为
  `ReqGetSnapshot`，参数键顺序为 `security_code, market_type, date, begin_time, end_time,
  data_type, QueryBandWidth`，对应类型为 str/int/int/int/int/int/float，`level_type` 不上线。
  响应 `status=0`、整数 tag `11000`、`pack_num=all_pack_num=1`，数据为 11 条字符串 CSV，
  每条 36 槽：整数/字符串形状与既有 L1 parser 相符，四个十档槽均为字符串编码。随后官方
  客户端发送 `ReqGetComplete`，查询 WebSocket 正常 close。这个单包观察不外推为多包语义。
- Arm: `src/python/tgw_macos/_protocol.py` 将已独立验证目标从 SZSE `159518` 扩为
  `(101, "510300")`，不改变 `ReqGetSnapshot`/tag `11000`、完成消息或 36→57 字段解析。
  `data_type!=0`、`level_type!=0` 和所有不在两个精确 tuple 中的 market/code 仍明确
  `NotImplementedError`。`tools/live_smoke.py` 的同步 Snapshot 摘要显式设为 0/0，并只打印
  列、类型与不变量，不打印业务值；`tools/oracle/remote_sdk_oracle.py` 抑制官方 SDK 的
  登录/关闭诊断，只输出其明确构造的脱敏 JSON。
- Tests: `tests/test_native_protocol.py` 新增 SSE L1 envelope 回归，锁定 market 101、
  `data_type=0`、无 `level_type` wire key；既有结构大小/offset、参数键序、57 字段解析、
  tag/status/包号/错误形状测试继续覆盖。`python3 -m unittest tests.test_native_protocol -v`
  49 项通过；`PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v`
  176 项通过（2 项因未安装 pandas 跳过）；`python3 -m compileall -q src/python examples tools
  experimental` 通过。
- Live diff: Linux/Mac 用同一代码、市场、日期、时间窗、同步格式和 0/0 请求各一次。两侧都是
  整数错误码 0、11 行、完全相同的 57 字段集合，字段类型均为 55 个 int 加 2 个 str；
  `market_type=101`、`variety_category=0`、17 位 `orig_time`、一类交易阶段和逐行键集合一致。
  Mac 在 finally 中执行 `Close()`；未比对或写入价格、数量、时间戳原值或完整返回。
- Cleanup: Linux 临时 oracle、interposer、分析器、原始 capture 及 staging 目录均已删除；
  flock 在会话退出时释放；`galaxy-relay` 查询前后均为 inactive。Mac 用户名仅经 stdin
  注入，密码只从 mode 0600 的受保护本地配置在进程内读取；不持久化凭据。
- Proposed status: `LIVE_ALIGNED(SSE 510300 historical L1 snapshot; one synchronous
  20260825 narrow window; data_type=0, level_type=0)`，待验收者复核。本任务不修改中央状态表。
- Open risks: 多包/异步回调、错误/空数据的 SSE 特定同参、其它 SSE 代码/日期/时间窗、其它
  市场、`data_type=1/2`、非零 `level_type`、断线重连、流控和长期资源生命周期均未验。
