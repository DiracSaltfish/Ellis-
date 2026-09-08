# QueryETFInfo：bj 原生 Linux PCF 独立复查

2026-09-08 08:13:47（北京时间）开始。针对“上海限额缺失是否由 Mac ARM 移植映射导致”的复查。

**结论：513350 的 creation_limit、redemption_limit 在 bj 原生官方 SDK 的直接返回中也是整数零；不是仅在 Mac 移植结果中出现。** 本轮并未证明所有字段均无移植问题，也没有继续区分官方 SDK 内部包装和银河服务端原始数据的责任。

## Scope / PDF / Header delta

只查询互联网模式 QueryETFInfo，深圳 159518 和上海 513350，各单 item、同步 JSON 返回。沿用[前次报告](query_etf_info_159518_513350_pcf_assessment.md)中已核对的 PDF、V1.0.8 结构与单位，未修改 ABI。头文件明确限定该组限额字段仅深圳有效。本轮不是运行 Mac 模拟后端，也不是用本地兼容层转发。

## Linux oracle

通过 `ssh bj`，使用原服务的 `/opt/galaxy-relay/venv/bin/python`，以 `galaxyrelay` 用户执行，只从既有受保护配置在内存中读取凭据。没有重启原生常驻数据库服务；运行前服务为 inactive，使用它已安装的官方 SDK 做独立只读会话。

运行时证据：

- 系统 Linux，架构 x86_64。
- 导入 `/opt/galaxy-relay/venv/lib/python3.12/site-packages/tgw/__init__.py`。
- 进程实际加载官方 `libtgw.so`、`libtgw_python312.so` 等原生动态库。
- `mac_module_loaded=false`，没有导入 tgw_macos。
- `tgw.QueryETFInfo(..., return_df_format=False)` 返回后直接检查 basic 字典字段名、Python 类型和是否为零；这些检查在任何单位换算前进行。

| 样本 | 返回码 | PCF 数量 | 成分数 | 查询耗时 | 重点结果 |
|---|---:|---:|---:|---:|---|
| 159518 | 0 | 1 | 52 | 241 ms | 与深交所同日期样本相比，已检查的 12 项基础数字及 4 项逐成分数字全部一致 |
| 513350 | 0 | 1 | 51 | 48 ms | creation_limit / redemption_limit 均为 int，且等于零；与前次 Mac 的零字段集合一致 |

[原生复查 JSON](pcf_eval_20260908/linux_native_recheck.json)保存运行时路径、字段与类型、零值字段名和比较不变量，没有保存原始 PCF 行或凭据。

## 必须区分的数据日期问题

本次两份 API PCF 仍不是当天；深圳按 API 日期获取的文件与 API 同日。**上交所“最新文件”已切换交易日期，same_trading_day=false。**

原生复查 JSON 如实保留当时探针的比较输出，但其中 513350 对上交所的 numeric_matches、flags_match、component_matches 和 api_unavailable_limit_fields 是跨日期比较，**不能用来认定字段映射错误、当天缺失限额或同日值差异**。例如本次替代金额 0/51 相等不构成映射失败证据。

本次能够独立证明的是：官方 SDK 本身返回了该两项整数零。前次 Mac 07:58 样本与交易所 same_trading_day=true 时的限额差异，仍是独立的历史同日证据；不能把两次不同日期状态混为一次比较。富国按 API 日期请求的三个核心数字比较仍相等，但本轮没有新增富国返回日期的完整验收，不能把它作为跨源日期已对齐的证明。

为防止后续误判，已修正评估工具：发现交易日期不同，记录 `comparisons_skipped=trading_day_mismatch`，立即跳过数值与标志比较。该修正是在本次请求后完成，未重新登录或伪造重跑结果。

## Wire / Arm / Live diff

未抓包，没有新增 wire 验证。没有修改 Mac SDK 的字段映射、构建 wheel 或部署程序。仅为评估工具增加原生运行时来源记录和跨日期比较门禁。

与前次成功 Mac 查询比较：两只的基础字段集合／类型、成分字段集合／类型／行数，以及基础零值和空值字段集合一致；见 [native_recheck_diff.json](pcf_eval_20260908/native_recheck_diff.json)。这不是本轮同一时刻所有业务值的逐值差分，不能推广为整个 ARM SDK 无误。

## Tests

- 新增跨交易日不能生成字段差异结论的合成回归测试。
- `python3 -m unittest discover -s tests -v`：182 项，180 通过、2 跳过，见 [native_recheck_tests.txt](pcf_eval_20260908/native_recheck_tests.txt)。
- 修改工具与测试的 compileall 通过。

## Cleanup / Proposed status / Open risks

finally Close 成功；bj 任务专用脚本和临时目录已删除。galaxy-relay 前后均 inactive，没有停止运行中的服务，也没有任何交易／订阅调用或 machome／DMIT 部署。

保持本次官方样本为 LINUX_OBSERVED 补充证据，不提升中央矩阵。未验证官方包装与服务端原始报文间的字段差异、API 当日 PCF 到达时间、Mac 真批量及全字段业务等价性。

因此维持建议：可以评估 API 主取，但上海限额需要有有效的补充来源；应同时将“清单日期正确”作为接入门禁。
