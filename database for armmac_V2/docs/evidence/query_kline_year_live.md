# QueryKline 年线对齐证据（互联网模式 live 闭环）

- Scope: 仅 `QueryKline` 公开周期 `cyc_type=10012`（年 K 线）的完整闭环：Linux x86 官方 SDK
  最小只读 oracle → 脱敏 wire 取证 → Mac arm64 实现放行 → 合成测试 → Mac 同参 live。
  互联网模式（kInternetMode）单代码 SSE `510300`、单完整年份窗口 `20250101–20251231`、
  `cq_flag=0`、`cq_date=0`、`qj_flag=0`、`cyc_def=0`、`auto_complete=1`、
  `begin_time=end_time=0`。分钟族（10000–10007）与其它周期不在本范围，实现继续显式
  `NotImplementedError`。**`10012→10104` 由独立实捕证明，未从日/周/月/季映射外推。**
- PDF: C++ 手册 `TGW-C++`（187 页；正文页 = PDF页 − 8）：
  - `QueryKline`/`ReqKline`：PDF 33–34（正文 25–26），手册标注"K 线查询接口，托管机房和
    互联网模式适用"；`ReqKline` 逐字段表与 V1.0.8 HDR 一致（`cyc_type` 为 `uint16_t`、
    `auto_complete` 默认 1、`cq_flag` 默认 0）。
  - `MDDatatype` 周期条目：PDF 64–65（正文 56–57）；`kYearKline 枚举(=10012) 年 K 线`
    在正文 57（PDF 页 65 首行）；`MDDatatype` 段首标注"托管机房模式和互联网模式适用"。
  - `MDKLine` 输出结构：PDF 68（正文 60）`5.1 K 线(MDKLine)`；表格列出 10 字段，未列
    `variety_category`（既有 PDF/HDR 差异，见 Header delta）。
  - 回调：`IGMDKlineSpi::OnMDKLine + OnStatus`（HDR `tgw_history_spi.h:290–316`）。
- Header delta: V1.0.8 `tgw_datatype.h:277` `kYearKline = 10012`，适用"托管机房模式、
  互联网模式"，与 PDF 一致；`tgw_struct.h:133–152` `ReqKline` pack(1)，本地
  `sizeof(ReqKline)=71` 已复验。`MDKLine`（tgw_struct.h:284–297）11 字段含末尾
  `uint8_t variety_category` —— PDF 表格漏列该字段为既有差异，非年线特有。权限枚举位
  `ColocationDataPermission.kYearKline=13`（托管）/`InternetDataPermission.kYearKline=28`
  （互联网）为本地权限位，非 wire 周期值。本轮无新增 ABI 差异。
- Linux oracle: 2026-08-26 在 bj 用 Linux x86 官方 SDK（`/opt/galaxy-relay/venv/bin/python`、
  galaxyrelay 用户、凭据仅从远端受保护配置读取）做只读年线查询：`--kind kline
  --cyc-type 10012 --security-code 510300 --market-type 101 --begin-date 20250101
  --end-date 20251231`。执行前后 `galaxy-relay` 均为 inactive；查询结束走官方 `Close()`。
  脱敏摘要（无任何业务原值）：
  - `login: true`（布尔）；`query_error_type: "int"`、`query_error: 0`；
  - `result_shape`: list length 1；11 列键
    （close_price/high_price/kline_time/low_price/market_type/open_price/orig_time/
    security_code/value_trade/variety_category/volume_trade）逐列类型为
    **10 个 int + 1 个 str（security_code）**；
  - `result_invariants`: `distinct_market_type=[101]`、`kline_time_digit_lengths=[8]`
    （yyyyMMdd 形态）、`orig_time_is_zero=true`、`variety_category_is_zero=true`、
    `orig_time_equals_kline_time=false`（orig_time 补 0、kline_time 为年/交易日日期）；
  - `req_default_fields: {}`。
- Wire: 对一次官方客户端会话做脱敏 `SSL_write/SSL_read` 取证（`ssl_write_interpose.c` +
  `analyze_ssl_write_capture.py`；原始捕获在分析后立即销毁）：
  - push 路径 `/amd/dgw/push` 完成 `ReqLogon/OnRspLogon` 鉴权后，独立 query WSS
    `/amd/dgw/dgw2_query` 登录鉴权；
  - 请求 method=`ReqGetKline`；params key 顺序 `security_code, market_type, cq_flag,
    auto_complete, period_type, begin_date, end_date, begin_time, end_time,
    QueryBandWidth`，类型为 str/int/int/int/int/int/int/int/int/float；
  - **从请求帧直接提取到公共 `cyc_type=10012` 被转换为 wire `period_type=10104`**
    （独立实捕证明，非从日/周/月/季外推）；
  - 响应单包 `status=0`、tag=`10104`、`pack_num=1/all_pack_num=1`；`data` 为字符串数组
    （1 行 × 9 个 CSV 槽，`int*9`，all_rows_same_shape=true）；
  - 随后发送 `ReqGetComplete`，双端以 WebSocket close 帧（opcode 8）正常关闭。时序与
    日/周/月/季契约一致。
  - 本次未观测到 `0x59+ZSTD`（年线单包明文帧），与日/周/月/季同参单包样本一致。
- Arm:
  - `src/python/tgw_macos/_protocol.py`：`VERIFIED_KLINE_WIRE_TYPES` 扩为
    `{10008:10100, 10009:10101, 10010:10102, 10011:10103, 10012:10104}`；注释与
    `parse_kline_packets` 契约注释补年线 tag；未列周期（10000–10007/9999）仍抛
    `NotImplementedError`。
  - `_backend.py`：无需改动（期望 tag 由 `kline_wire_period` 推导，10012 自动放行）。
  - `interface.py`：无需改动（`QueryKline` 委托 backend，无周期专用分支）。
  - `tools/live_smoke.py`：`--cyc-type` 帮助文本补充年线取值 10012。
- Tests: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v` 共 **77 项
  全部通过（skipped=1 pandas 缺失分支）**；`PYTHONDONTWRITEBYTECODE=1 python3 -m
  compileall -q src/python examples tools` 通过。新增/更新：
  - `test_year_kline_maps_to_verified_wire_period_and_tag`：`kline_wire_period(10012)`
    ==10104；`build_kline_request` 的 params key 顺序与类型断言；响应 tag=10104 的
    9 列 CSV 双包乱序重组 → 11 字段 dict（`orig_time=0`、`variety_category=0`、
    security_code 为 str、其余 10 列为 int、`kline_time` 顺序断言）；
  - `test_kline_rejects_unverified_cycles`：更新为 `(10000,10001,10007,9999)` 显式失败，
    并锁定映射表键位 `[10008,10009,10010,10011,10012]`。
- Live diff: Linux 与 Mac 同参（同代码 `510300`、市场 101、`cyc_type=10012`、
  `begin=20250101/end=20251231`）各一次：
  - Linux：login true、`query_error` 整数 0、1 行、11 列（10 int + 1 str）、
    `kline_time` 8 位、`distinct_market_type=[101]`、`orig_time=0`、`variety_category=0`；
  - Mac（第二授权账号，`TGW_MAC_USERNAME_OVERRIDE` + `--username-stdin` 注入，未持久化）：
    登录成功、`OnRspLogon`、`kline_query_error=0`、rows=1、11 列集合与类型
    （`security_code` str、其余 10 列 int）与 Linux 完全一致，列顺序一致；查询完成/关闭
    语义与月线/季线样本一致。
  - 第一次 Mac 尝试被服务端以 `1000 / accept conn active close` 回收（与既往登记的
    准入/流控行为一致）；按工作流立即停止密集重试，仅一次低频换备用 query 端点后成功，
    该结果保留为流控证据而非 parser 失败。
  - 两侧均未比对或保存任何原始行情值。全部一致 → 拟议 `LIVE_ALIGNED(yearly only)`。
- Cleanup: bj 上 `/tmp/tgw_y_o2/`（oracle 副本、interposer `.so`、`ssl_write_interpose.c`、
  `analyze_ssl_write_capture.py`、capture.bin）已删除并复核不存在；原始捕获 `capture.bin`
  在分析后立即销毁；本地临时捕获摘要 `/tmp/tgw_y_year_wire_summary.json` 已删除；
  `galaxy-relay` 任务前后均 **inactive**；仓库内无账号、密码、token、MAC、原始行情或捕获文件。
- Proposed status: `LIVE_ALIGNED(yearly only; SSE 510300 sample)`。不申请
  `PILOT_READY`（无重连、资源与持续观测验收）；最终状态由验收者复核后提升，不自行更新
  中央矩阵。
- Open risks:
  1. 分钟族（10000–10007）与其它周期仍未验证且显式 `NotImplementedError`；
  2. 年线跨年多包分页仅合成测试覆盖，线上为单包样本；
  3. 其它市场/代码/账号未取样；query 通道准入/`1000 accept conn active close` 流控风险
     延续（本次再次观测到，低频换端点后恢复）；
  4. TLS 服务端仍为旧 profile；自动重连仍不存在；
  5. 线上响应未观测 `0x59+ZSTD` 帧（年线单包明文），压缩分支为其它接口已有证据复用。
