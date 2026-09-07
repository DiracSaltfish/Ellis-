# QueryKline 季线静态契约核对证据

本任务为**纯静态**三方契约核对：仅 `QueryKline` `cyc_type=10011` 季线一个周期。
无登录、无查询、无 wire 捕获、无 Mac live、不修改任何源代码/测试/中央文档。

- Scope: 仅 `QueryKline` 季线 `cyc_type=10011` 的 PDF ↔ V1.0.8 发行头文件 ↔ 官方
  Python wrapper 静态契约。年线（10012）、分钟族（10000–10007）与其它周期**不在本范围**，
  不得由日/周/月/季外推。季线→wire `period_type/tag 10103` 仅为**外推假设，禁止写为结论**，
  标注"待实捕证明"。
- PDF: C++ 手册 `TGW-C++`（187 页；正文页 = PDF页 − 8）。
  - `QueryKline`/`ReqKline`：PDF 33–34（正文 25–26），手册标注"K 线查询接口，托管机房和
    互联网模式适用"；`ReqKline` 逐字段表与 HDR 一致，`cyc_type` 为 `uint16_t`、"数据周期
    （参考 tgw_datatype.h 中的 MDDatatype 描述）"，`auto_complete` 默认 1、`cq_flag` 默认 0。
  - `MDDatatype` 周期条目：PDF 64–65（正文 56–57）。`kSeasonKline 枚举(=10011) 季 K 线`
    在第 5 页（正文 57）首行；`MDDatatype` 段首标注"托管机房模式和互联网模式适用"。
  - `MDKLine` 输出结构：PDF 68（正文 60）`5.1 K 线(MDKLine)`。注意该页表格列出 10 个字段
    （market_type, security_code, orig_time, kline_time, open/high/low/close_price,
    volume_trade, value_trade），**未列 `variety_category`**（见 Header delta）。
  - 回调：`IGMDKlineSpi::OnMDKLine + OnStatus` 契约见 HDR `tgw_history_spi.h:290–316`；
    PDF 3.5.5 查询数据方法处引用 `IGMDKlineSpi` 的 `OnMDKLine`（PDF 33，正文 25）。
- Header delta: V1.0.8 `tgw_datatype.h:276` 明确 `kSeasonKline = 10011`，适用"托管机房模式、
  互联网模式"，与 PDF 双模式表述一致，**无冲突**。权限枚举位：`ColocationDataPermission`
  中 `kSeasonKline = 12`（tgw_datatype.h:336，托管机房）；互联网账号权限 `InternetDataPermission`
  中 `kSeasonKline = 27`（tgw_datatype.h:383）。这两个是本地权限枚举位，**不是** wire 周期值，
  不构成周期→wire 映射。`ReqKline`（tgw_struct.h:133–152）pack(1)，含 `uint16_t cyc_type`；
  本地 ctypes `sizeof(ReqKline)=71` 已复验（security_code[38] + u8 + u8 + u32 + u32 +
  u16 + u32 + u8 + u32*4 = 38+1+1+4+4+2+4+1+16 = 71）；构造默认 `cq_flag=0`、`auto_complete=1`。
  `MDKLine`（tgw_struct.h:284–297）为 11 字段，含末尾 `uint8_t variety_category` —— 这是
  PDF 与 HDR 的**既有差异**（PDF 表格漏列 variety_category），与日/周/月证据一致，非季线特有。
  linux 与 windows 头文件经 CRLF 归一后对 `tgw.h / tgw_datatype.h / tgw_struct.h /
  tgw_export.h / tgw_history_spi.h` 逐字节**一致**，无平台差异。
- Official Python: `ssh bj` 只读自省官方 wheel 源码（`galaxy-relay` 任务前后均为 inactive；
  未运行任何官方 SDK 登录/查询/订阅）。
  - SWIG `ReqKline`（tgw.py）12 字段与 HDR 完全一致，`cyc_type` 为 uint16 字段，无周期专用
    分支；`__init__` 调 `new_ReqKline()`，C++ 构造默认 cq_flag=0/auto_complete=1。
  - SWIG `MDKLine`（tgw.py）11 字段含 `variety_category`，与 HDR 一致。
  - 高层查询回调 `TmpQueryKlineSpi.OnMDKLine(klines, cnt, kline_type)`（tmp_spi.py:523+）：
    调用 `tgw.Tools_KLineToJson(klines, cnt)` 把 C++ `MDKLine[]` 转 JSON 列表，`return_df_format`
    False 时直接返回该列表；`OnStatus` 调 `spi(None, status.error_code)`。该转换只依赖 `MDKLine`
    结构与 `Tools_KLineToJson`，**与 `cyc_type`/`kline_type` 无关**——因此"9 个 CSV 列 + 补列
    orig_time=0、variety_category=0"的 11 字段 dict 契约对季线 10011 同样成立（静态推断，
    待 live 实捕证实）。
- Arm gap: Mac 侧 `_protocol.py:157` `VERIFIED_KLINE_WIRE_TYPES = {10008:10100, 10009:10101,
  10010:10102}`；10011 不在表中 → `kline_wire_period`（_protocol.py:164）抛
  `NotImplementedError`。`_backend.py:240` 由 `kline_wire_period` 推导响应期望 tag。
  `interface.py:158` `QueryKline` 委托 `_backend().query("kline", ...)`。故 Mac 当前对
  `cyc_type=10011` 显式 `NotImplementedError`，符合工作流"未证周期显式失败"。本任务不改实现。
- Proposed oracle plan: 复用现有 `tools/oracle/remote_sdk_oracle.py` 的 `--kind kline`
  入口（已参数化 `--cyc-type/--begin-date/--end-date`，默认保持日线样本不变）。建议季线最小
  只读样本：
  - SSE 单代码 `510300`；`--market-type 101`（kSSE）；
  - 单季窄窗口，如 `--begin-date 20260401 --end-date 20260630`（2026 年 Q2）；
  - `--cyc-type 10011`；`--begin-time 0 --end-time 0`；`cq_flag=0`、`auto_complete=1`。
  - 复用可行性：`--kind kline` 分支直接构造 `tgw.ReqKline()` 并逐字段赋值（含
    `request.cyc_type = int(args.cyc_type)`），对 10011 无需改动代码即可提交到官方 SDK；
    输出走统一 `safe_shape` + `result_invariants` 脱敏摘要。
  - 预期脱敏摘要字段（沿用月线证据模板，禁止业务原值）：
    - `login` 布尔；`query_error_type`（期望 `int`）、`query_error`（期望整数 0）；
    - `result_shape.type= list`、`length`（期望 1 行，单季窄窗）；
    - 11 列名集合与逐列 Python 类型（`result_shape` 由 `safe_shape` 递归给出；若为 dict 列表，
      oracle 的 `safe_shape` 对 dict 逐键递归，可看到列键与值类型；建议 oracle 在 kind kline
      分支补充一次性"列键集合 + 列类型"输出以与月线模板对齐，但本轮**不改代码**，仅记录建议）；
    - `result_invariants`：`orig_time_equals_kline_time`、`orig_time_is_zero`、
      `variety_category_is_zero`、`kline_time_digit_lengths`（期望 yyyyMMdd 8 位）、
      `distinct_market_type`（期望 [101]）；
    - `req_default_fields`（cq_flag/auto_complete 等）。
- Tests: 本任务无代码变更，故**无新增测试**。建议给实现任务卡复用的未来测试清单：
  1. builder 枚举映射：断言 `kline_wire_period` 对已证 `{10008:10100,10009:10101,
     10010:10102}` 返回正确 wire 值，对 `10011/10012/10000–10007/9999` 抛
     `NotImplementedError`；锁定映射表键位 `[10008,10009,10010]`；
  2. parser tag 校验：`parse_kline_packets(packets, expected_tag)` 对错误 tag 抛错；对
     9 列 CSV 行解析出 11 字段 dict（含 orig_time=0、variety_category=0）并断言列类型
     （security_code str，其余 int）；
  3. 负形状：非 9 列行、非整数字段、非字符串数组、多包乱序/缺包/重复包、错误 status/tag
     均须明确失败；
  4. 季线 wire 值一旦实捕证实后，新增 `10011 → 10103` 映射单测与响应 tag=10103 解析测试。
- Live diff: 本轮未执行任何 live 请求，无 Linux/Mac 同参数据。留给下一棒：经实捕证明
  `10011 → wire period_type/tag` 后，用同一 `--kind kline --cyc-type 10011` 参数在 Linux 官方
  SDK 与 Mac 分别运行，比对 err 类型/数值、行数、11 列集合与类型、`kline_time` 位长不变量、
  完成/关闭语义；Mac 实现须待 wire 证实后进行。
- Cleanup: bj 上未创建任何文件（仅只读 `grep/sed` 自省，未启动 SDK、未写 oracle 副本、未做
  capture）；`galaxy-relay` 任务前后均 `inactive`（已复核）。本地 PDF 抽取临时文本位于系统临时
  目录，已删除（见下方清理）。`git status` 中除新增 evidence 外无其它变更。
- Proposed status: `STATIC_MATCHED(QueryKline quarterly static contract only)`。三方
  （PDF / V1.0.8 HDR / 官方 Python wrapper）在 10011 请求结构、双模式、11 字段输出与回调
  契约上一致；`10011 → 10103` 映射未实捕，不写入中央矩阵，状态仅按本证据拟议。
- Open risks:
  1. 季线→wire `period_type/tag 10103` 仅为外推假设，未实捕证明；在实捕前禁止实现/放行；
  2. PDF `MDKLine` 表漏列 `variety_category`（HDR 有），为已知既有差异，非季线特有；
  3. 本地权限枚举位（托管 12 / 互联网 27）不等于 wire 周期值，勿混淆；
  4. query 通道准入/`1000 accept conn active close` 流控风险延续（见月线证据）；
  5. 单季窗口的跨季多包分页与其它市场/代码未取样。
