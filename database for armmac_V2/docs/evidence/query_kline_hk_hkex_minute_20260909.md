# QueryKline 港股通(HKEx)与 A 股跨市场 1 分钟 —— Linux 官方/Mac 同参闭环

日期：2026-09-09；实现：`tgw_macos 1.0.9.2.macos.re8`（`pull_history_batch` 批量路径）。
本文只验证 `QueryKline` 的 1 分钟历史 K 线在以下样本上 Linux 官方 SDK 与 Mac 逐行一致；
不对外推「全部市场/全部标的/长区间」已验收。

## 1. 背景

用户目标是批量拉取**港股通/港股（HKEx 路由）**与 **A 股**的历史 1 分钟行情。
排查中先遇到的是登录层而非行情层问题，随后在同一会话内完成跨市场 1 分钟验证。

## 2. 排查结论（先修登录，再验证行情）

1. `ReqLogon` 收到 `OnRspLogon status=-98` 时，SDK 不发放 token、`Login()` 返回
   False，因此任何 `QueryKline`（无论 A 股或港股）都不会发出。这正是"A 股/港股通历史
   拉不到"的直接原因，与 K 线代码、分钟参数或压缩逻辑无关。
2. 实测两类 `-98`：
   - 账号只在其**专属的互联网 VIP 集群**上有授权；用另一集群的 VIP 登录稳定返回 `-98`
     （该集群是 Linux/原生账号的接入点）。修正 host 指向本账号所属集群后登录成功。
   - 同账号**上一个会话仍存活**（进程异常退出、未登出/未正常关 WS）时，新登录会 `-98`；
     `force_logout=True` 可顶掉旧会话（仅限确认无其它在用进程时使用）。
   - 登录响应里的 `act_instanceid` 说明实际命中的接入实例；负载均衡会在多实例间漂移，
     所以同一 VIP 下登录偶尔成功、偶尔 `-98`。批量工具因此采用有界重试。
3. 修复：
   - `Cfg.server_port=0` 现在解析为文档化互联网默认端口 `8600`（此前直接拨 0 号端口必败）。
   - `-98` 拒绝消息带 `instance` 与可执行提示（验账号↔VIP 映射、确认无其它会话后用
     `force_logout=True`）。
   - `tools/pull_history_batch.py`：符号列表/区间/周期、host 序列登录 + 有界重试、
     `force_logout` 开关、单标的查询通道 `accept conn active close` 的退避重试与
     `--query-gap-sec`，输出 gzip JSONL（拒绝写入仓库）。

## 3. 验证样本与命令类别

| 项 | 值 |
|---|---|
| 周期 | `cyc_type=10000`（1 分钟） |
| 窗口 | 2026-09-07 09:30:00 – 2026-09-09 16:00:00（HKEx）/ 15:00（A 股） |
| HK 标的（market=103/kHKEx） | `00001` `00177` `00187` `00200` `00300` `00700` |
| A 股标的 | `000001.SZ`（market=102）、`600000.SH`（market=101）；另 `510300.SH` 日线 `10008` 2026-09-01..09 |
| Mac 端 | `tools/pull_history_batch.py` + 会话脚本（登录后逐标的 `QueryKline(return_df_format=False)`） |
| Linux oracle | bj 上官方 `AmazingData+tgw` Linux SDK，同参数逐标的 `QueryKline` |
| wire | `ReqGetKline`；`period_type=10000`，响应 tag `10000`（与已验证的日/周等同一枚举规律） |

## 4. Mac 结果摘要

- HK 每交易日 332 根；`00177/00187/00200/00300/00700.HK` 三会话各 996 行，
  `first=202609070930 last=202609091600`。
- `000001.SZ` 三会话 720 行、`600000.SH` 三会话 720 行，
  `first=202609070930 last=202609091459`。
- `510300.SH` 日线 2026-09-01..09 返回 7 行（2026-09-09 当天含在区间内）。
- 每行 11 字段，与已验证 K 线公开形状一致（`market_type/security_code/orig_time/
  kline_time/open/high/low/close/volume/value/variety_category`），值为协议原整数。

## 5. Linux 官方 / Mac 同参

对 `00177.HK`、`00700.HK`、`000001.SZ`（各 3 会话）以 Linux 官方 SDK 同参数重跑：

| 标的 | native 行数 | mac 行数 | 逐日行数（native, mac） | canonical 行摘要 |
|---|---:|---:|---|---|
| `00177.HK` | 996 | 996 | 332×3 = 332×3 | 一致 |
| `00700.HK` | 996 | 996 | 332×3 = 332×3 | 一致 |
| `000001.SZ` | 720 | 720 | 240×3 = 240×3 | 一致 |

按排序后键 canonical 化的逐行摘要（SHA-256）两侧完全相同。即同参数下
`QueryKline` 输出与 Linux 官方 SDK **逐行一致**，缩放与官方语义等同。

## 6. 已补测试与工具

- `tests/test_native_protocol.py::LoginRobustnessTests`：端口 0→8600 默认；
  LiveBackend `init` 在连接前规整端口；`-98` 拒绝消息含 instance 与可执行提示。
- `tools/pull_history_batch.py`：批量入口（见 §2.3）。
- 全量 unittest：186 通过（1 跳过），`compileall` 通过。

## 7. 清理

- 本轮临时脚本/输出均在系统临时目录，未写入仓库；仓库无账号、token、MAC、原始行情。
- bj oracle 每次进程内登录后 `logout`；无残留会话进程。

## 8. 拟议状态与开放风险

拟议状态：`LIVE_ALIGNED`（限定为 2026-09-07..09 的 HKEx 与 A 股 1 分钟样本，及日线
复验；Linux/Mac 逐行摘要一致）。

开放风险：
1. 长区间（数周/月/年）单请求多包尚未在 Mac 上验收；HK 全年样例此前只在 Linux 侧
   跑通过。
2. 账号级 1 会话与实例负载均衡使登录可能间歇 `-98`；批量工具用 host 序列 + 有界重试
   缓解，未替代服务端侧会话管理。
3. 查询通道会主动关闭高频的 one-shot 连接（`code=1000 accept conn active close`）；
   已用 `--query-gap-sec` + 重试缓解，仍非长期 soak 验收。
4. 原始值为协议整数；本验证只保证与官方输出一致，未在行情界面独立核对除 159691 外
   的元/股/元单位换算。
5. 无自动重连/订阅恢复，进程仍需监管。
