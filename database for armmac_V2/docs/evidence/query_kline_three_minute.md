# QueryKline 历史 3 分钟 K 线对齐证据

> 最初任务卡将 `cyc_type=10001` 称为“2 分钟”；该名称与权威公开契约冲突。本项实际范围固定为
> **历史 3 分钟 K 线**：公开 `cyc_type=10001`。

- Scope: 仅 `QueryKline`、互联网模式、公开 `MDDatatype.k3KLine=10001`、历史查询。明确不
  包含实时订阅 K 线，也不包含 `10002–10007` 或其它未验周期。Linux/Mac 同参候选为既有 SZSE
  `159691`，单日窄窗口 `20260826`、`0900–1500`、`cq_flag=0`、`cq_date=0`、`qj_flag=0`、
  `cyc_def=0`、`auto_complete=1`、`return_df_format=False`。
- Naming correction: 初始派工称“2 分钟、`cyc_type=10001`”。TGW C++ 手册 PDF 64（正文 56）和
  V1.0.8 Linux `tgw_datatype.h` 均明确 `k3KLine=10001` 是 **3 分钟 K 线**；本证据及后续实现
  以权威枚举语义为准。不存在已发现的 2 分钟公开 K 线枚举。
- PDF: TGW C++ 手册 PDF 33–34（正文 25–26）定义互联网/托管共用的 `QueryKline` 和 `ReqKline`：
  `cyc_type:uint16_t`、日期 `yyyyMMdd`、时间默认 `HHmm` 且支持 `HHmmssSSS`、`cq_flag` 默认 0、
  `auto_complete` 默认 1。PDF 64 把 `k3KLine` 列为 10001；PDF 68 的 `MDKLine` 表列出 10 个
  字段，价格字段说明为除以 1,000,000。PDF 表不能决定成交量/成交额展示单位。
- Header / ctypes / official-Python static table:

  | Item | PDF | V1.0.8 Linux header | Local ctypes / official Python | Conclusion |
  | --- | --- | --- | --- |
  | public cycle | `k3KLine=10001`, 3-minute | same | official `tgw.MDDatatype.k3KLine=10001`; local `ReqKline.cyc_type` is writable `c_uint16` | public contract matched |
  | request layout | `ReqKline` fields and defaults | pack(1), 71 bytes | official `ReqKline` exposes all 12 fields and defaults `cq_flag=0` / `auto_complete=1`; local pack(1), 71 bytes; offsets `security_code=0`, `market_type=38`, `cq_flag=39`, `cq_date=40`, `qj_flag=44`, `cyc_type=48`, `cyc_def=50`, `auto_complete=54`, `begin_date=55`, `end_date=59`, `begin_time=63`, `end_time=67` | matched |
  | response container | `MDKLine` table has 10 fields | 11 fields, adds `variety_category:uint8_t` | official Python `MDKLine` exposes all 11 header fields; prior verified K-line samples use 9 wire CSV slots expanded to 11 public fields (`orig_time=0`, `variety_category=0`); `security_code:str`, remaining fields `int` | existing PDF/header delta; 3-minute runtime behavior unobserved |
- Local pre-network state: `10001` is deliberately absent from `VERIFIED_KLINE_WIRE_TYPES`; builder, backend
  expected-tag selection, and public interface therefore raise `NotImplementedError`. This is intentional until
  the official SDK capture proves `period_type` and response tag. No wire enum, tag, method variant, or unit
  conversion is inferred from adjacent periods.
- Oracle preparation: `tools/oracle/remote_sdk_oracle.py --kind kline --cyc-type 10001` constructs the public
  official `ReqKline` with every candidate field explicitly set. This task adds `--password-stdin`; together with
  the existing `--username-stdin` it uses `getpass` one-run non-echo input, never argv, environment, log, fixture
  or persistent file. The Mac smoke tool already has one-run username input. No credential value is recorded here.
- Tests: before the network window, `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p
  'test_native_protocol.py' -v` passed 43 tests; this includes `10001` explicit rejection, request key/type
  construction for verified cycles, tag/status, multi-packet ordering, duplicate/missing packet, malformed-row,
  and 11-field container/type checks. Final `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v`
  passed 146 tests, and `PYTHONDONTWRITEBYTECODE=1 python3 -m compileall -q src/python examples tools` passed.
- Linux static object check: during the authorized window, but before an API call, official Linux `tgw` reported
  `MDDatatype.k3KLine=10001`; `ReqKline` had all 12 writable public fields with defaults `cq_flag=0` and
  `auto_complete=1`; `MDKLine` exposed all 11 V1.0.8 header fields. This was local object introspection only.
- Linux oracle / exception: `galaxy-relay` was initially inactive. The first low-frequency attempt used the
  independently supplied username but the protected default-account password, returned `login=false`, and did
  **not** submit `QueryKline`; it is a credential-source mismatch, not a K-line outcome. After adding one-run
  password input, the final attempt with both independent credentials reached push login but received
  `OnRspLogon status=-95`, then a normal close. V1.0.8 calls `-95` `kPermissionError` (data permission); this is
  permission evidence only and does not establish why that account lacks access. It sent no query request and
  returned no rows, so there is no Linux result shape, response packet count, completion request, or container
  contract to compare.
- Wire: the final desensitized capture establishes only push path `/amd/dgw/push`, `ReqLogon` key order
  `Username, Password, MacAddress, Version, ProcessId, ForceLogout, PushBandWidth, QueryBandWidth`, and
  `OnRspLogon/status=-95`. No `ReqGetKline`, query path, public-to-wire `period_type`, response tag, packet
  counters, CSV shape, `ReqGetComplete`, or query close semantics was observed. In particular, do not assume the
  1-minute identity mapping or any daily-to-yearly offset pattern applies to `10001`.
- Arm / live diff: **not run.** Linux did not authorize the prerequisite oracle, so no Mac network query was made
  and no implementation branch was enabled; `10001` remains an explicit `NotImplementedError`.
- Cleanup: the original failed pre-SDK permission attempt, the two login attempts, and the final desensitized
  capture all remained in a dedicated remote temporary directory. Its script, interposer source/library, capture,
  analyzer and any bytecode were deleted; the directory no longer exists. `galaxy-relay` was rechecked as
  **inactive**. Local PDF renders created for this review were moved to the system Trash; pre-existing temporary
  renders are untouched.
- Proposed status: `STATIC_MATCHED(QueryKline historical 3-minute public contract only; cyc_type=10001)`.
- Open risks / blocker: the specified Linux independent account currently receives official `-95` at push login,
  so an account with the required historical-K-line entitlement (or an authorization change) is required before a
  single official query oracle can run. Then every wire control value and official result shape must be captured
  and compared to an identical Mac request. Cross-market/code/time-window, online multi-packet behavior, units
  beyond an independently corroborated scope, admission/flow control, reconnect, and resource soak remain outside
  this checkpoint.
