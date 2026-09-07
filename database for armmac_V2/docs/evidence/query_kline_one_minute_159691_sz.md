# QueryKline 1 分钟线对齐证据（互联网模式 live 闭环）

- Scope: 仅 `QueryKline` 公共周期 `cyc_type=10000`（1 分钟 K 线）的完整闭环：Linux x86
  官方 SDK 最小只读 oracle → 脱敏 wire 取证 → Mac arm64 实现放行 → 合成测试 → Linux/Mac
  同参 live。范围严格限定为互联网模式、SZSE `159691`、2026-08-26、09:00–15:00、
  `cq_flag=0`、`cq_date=0`、`qj_flag=0`、`cyc_def=0`、`auto_complete=1`。其它分钟周期
  `10001–10007`、其它标的、其它市场和其它时间窗仍未验证并继续显式拒绝。
- PDF:
  - TGW C++ 手册（187 PDF 页，正文页 = PDF 页 - 8）PDF 33–34（正文 25–26）：
    `QueryKline` 是托管机房和互联网模式适用的 K 线查询；`ReqKline` 明确
    `cyc_type:uint16_t`、`begin_date/end_date:yyyyMMdd`、`begin_time/end_time` 默认 HHmm，
    亦支持 HHmmssSSS，`cq_flag` 默认 0、`auto_complete` 默认 1。
  - TGW C++ 手册 PDF 64（正文 56）`MDDatatype.k1KLine=10000`，为 1 分钟 K 线；
    `MDDatatype` 段说明托管机房和互联网模式均适用。
  - AmazingData 手册 PDF 26–27（正文 22–23）：`query_kline` 接受 `Period`、日期和可选
    HHmm 时间窗，返回按代码分组的 Kline DataFrame。
  - TGW C++ 手册 PDF 68（正文 60）`MDKLine` 的价格字段实际值需除以 1,000,000。
    静态字段说明不足以决定本服务端实际 `volume_trade/value_trade` 的展示单位，不能据此
    推断其它标的的单位。
- Header delta: V1.0.8 `tgw_datatype.h` 的 `k1KLine=10000` 与 PDF 一致；
  `tgw_struct.h:133–152` 的 `ReqKline` 为 pack(1)，`cyc_type` 是 `uint16_t`，本地
  `sizeof(ReqKline)=71`。`MDKLine` 在 `tgw_struct.h:284–297` 有 11 字段，含 PDF 表格
  未列出的末尾 `variety_category:uint8_t`；这是既有 PDF/HDR 差异，非分钟线特有。
- Linux oracle: 2026-08-26 在 bj 以 `/opt/galaxy-relay/venv/bin/python` 和官方 SDK 运行一次
  最小只读同参请求。登录为 true，`query_error` 为整数 0，返回 242 行；行的 11 个字段为
  `market_type/security_code/orig_time/kline_time/open_price/high_price/low_price/close_price/
  volume_trade/value_trade/variety_category`，其中 `security_code` 是 `str`、其余均为 `int`。
  不变量：市场仅 102、`kline_time` 均为 12 位、`orig_time=0`、`variety_category=0`。
  未打印或保留业务行情值。前置与后置 `galaxy-relay` 均为 inactive。
- Wire: 对同一官方 SDK 单次会话使用 `SSL_write/SSL_read` interposer，随后仅运行脱敏分析器：
  - push 路径 `/amd/dgw/push` 完成 `ReqLogon/OnRspLogon`，独立 query WSS 为
    `/amd/dgw/dgw1_query`；
  - 请求 method=`ReqGetKline`，params key 顺序为 `security_code,market_type,cq_flag,
    auto_complete,period_type,begin_date,end_date,begin_time,end_time,QueryBandWidth`，类型为
    str/int/int/int/int/int/int/int/int/float；
  - 公共 `cyc_type=10000` 被实际发送为 `period_type=10000`，没有日线式偏移；
  - 响应 `status=0`、tag=`10000`、`pack_num=1/all_pack_num=1`，data 是 242 行字符串数组，
    每行 9 个 CSV 槽（`int*9`）；随后发送 `ReqGetComplete` 并以 close frame 正常关闭。
- Arm: `_protocol.py` 将经过证明的映射扩为 `10000:10000`；`_backend.py` 不需改动，继续从
  映射表派生 response tag。`tools/live_smoke.py` 可接受 10000，并增加独立的 K 线 HHmm
  时间窗参数。`tools/oracle/remote_sdk_oracle.py` 的 K 线分支改为实际使用传入的代码、市场和
  时间，避免验证工具悄悄回退到固定 SSE 样本。
- Tests: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p
  'test_native_protocol.py' -v`：37 项通过。新增 1 分钟映射/请求 key 顺序/HHmm 窗口/10000 tag/
  双包乱序重组/11 字段类型测试；未知 `10001`、`10007`、`9999` 继续显式失败。
  `PYTHONDONTWRITEBYTECODE=1 python3 -m compileall -q src/python tools/live_smoke.py
  tools/oracle/remote_sdk_oracle.py` 通过。
- Live diff: Mac arm64 用独立授权账号执行完全相同的代码、市场、日期、时间窗、复权和周期。
  登录成功、`query_error=0`、242 行、字段集合和类型与 Linux 完全一致；市场仅 102，
  `kline_time` 为 12 位，`orig_time=0`、`variety_category=0`。结果 CSV 仅写到用户
  Downloads 目录，未写入仓库、日志或 fixture。
- Unit reconciliation: 同日完整 242 行用独立行情界面的开/高/低/最新价、成交额、总手和成交
  均价交叉核对。仅在此范围内成立的精确换算为：价格原值 ÷ 1,000,000（元）、
  `volume_trade` 原值 ÷ 100（股）、`value_trade` 原值 ÷ 100,000（元）。逐行 OHLC 与界面
  一致；汇总后的成交额、总手及以 `sum(value_trade_yuan) / sum(volume_shares)` 得到的均价，
  与界面显示的舍入值一致。无原始行情值、截屏值或凭据写入本证据文件。
- Cleanup: 远端 interposer、官方 oracle 副本、脱敏分析器和原始 capture 均从
  `/tmp/tgw_min1_wire.*` 删除并复核不存在；`galaxy-relay` 仍为 inactive。本地临时 pull
  脚本和手册渲染文件已移入系统废纸篓；仓库未写入账号、密码、token、MAC、原始行情或完整捕获。
- Proposed status: `LIVE_ALIGNED(one-minute only; SZSE 159691, 2026-08-26 09:00–15:00)`。
  不申请 `PILOT_READY`：没有自动重连、恢复、长期资源或压力验收；最终中央矩阵状态由验收者
  复核后提升。
- Open risks:
  1. 其它分钟周期 `10001–10007`、市场、标的和时间窗未验证；
  2. 单包线上样本不覆盖多包分钟历史；多包路径仅有合成测试；
  3. query 通道仍可能受 `1000 / accept conn active close` 准入/流控影响；
  4. TLS 服务器仍使用旧 profile，且客户端没有自动重连或会话恢复。
