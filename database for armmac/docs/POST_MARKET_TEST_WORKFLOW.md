# 盘后接口逐项测试与修复工作流

更新时间：2026-08-26。本文是 `AGENT_PARITY_WORKFLOW.md` 的盘后执行补充；通用证据等级、
安全约束和验收条件仍以该文档为准。

## 1. 盘后范围

15:00 后默认领取只读、可重复、无需实时推送的任务：

1. `QueryKline` 的一个周期；日/周/月/季/年必须分别取证。
2. `QuerySnapshot` 的一个市场、一个 `data_type/level_type` 子范围。
3. `QueryCodeTable`、`QuerySecuritiesInfo`、`QueryExFactorTable`、`QueryETFInfo`。
4. `QueryThirdInfo` 的一个 `function_id`；每次只取单代码或窄日期窗。
5. AmazingData 高层历史/基础/财务接口的一个 wrapper；先证明其真实底层通道。
6. 不需要网络的结构大小、offset、默认值、枚举和本地金融算子测试。

实时订阅、实时 K 线、VCM 等事件驱动项标记为“待开盘”，盘后没有回调不能判失败。密码修改
等写操作只登记；仅托管机房和 RTCP 回放接口不进入 internet-mode 盘后队列。

## 2. 子 Agent 启动约定

使用 OpenCode 的免费 Ox Alpha 模型。一个 Agent 只领取一个接口或一个明确子范围：

```bash
cd "/Users/ellis/工具程序开发/database for armmac"
opencode run \
  --model opencode-go/ox-alpha-free \
  --variant max \
  --agent build \
  --format json \
  "<粘贴单接口任务卡>"
```

任务卡必须要求 Agent 完整阅读 `AGENTS.md`、`DEVELOPMENT_STATUS_AND_HANDOFF.md`、
`API_STATUS.md`、`AGENT_PARITY_WORKFLOW.md`、本接口 PDF 页和发行头文件。PDF 全量候选项见
`PDF_API_INVENTORY.md`；中央验收状态见 `PDF_API_PARITY_MATRIX.md`。

不要让两个 Agent 同时改 `_protocol.py` 的同一函数。远端官方 SDK oracle 原则上串行执行；
即使有两个授权账号，也必须通过受保护配置或 stdin 注入账号覆盖，不得写入命令、任务卡、
仓库或日志。

## 3. 单接口修复循环

```text
PDF/头文件逐字段静态表
→ Linux 官方 SDK 最小只读请求
→ 脱敏 wire 形状（method/keys/types/enum/tag/分页/完成）
→ Mac 构造器与 parser
→ 合成协议单测
→ Mac 同参数 live 请求
→ 返回码、行/包数、列、类型、不变量、完成/关闭差分
→ 成功则提交证据；失败则回到差异点修复并复验
```

修复必须只覆盖已证明分支。未知周期、市场、类型、tag 或回调要显式失败，不能返回空成功。
若服务端出现 `1000 / accept conn active close`，停止密集重试；最多换一个 query endpoint
低频重试一次，并在证据中保留为流控/回收结果。

## 4. Agent 交付与验收者动作

Agent 只提交：

- `docs/evidence/<接口_子范围>.md`；
- 对应的 `src/python/tgw_macos` 小范围实现；
- `tests/test_native_protocol.py` 或独立测试文件；
- 最终回复中的范围、测试数、拟议状态和未通过项。

Agent 不直接把中央矩阵标绿。验收者复核 PDF/HDR、敏感信息、源代码 diff、单测、Mac live
摘要和远端清理后，才更新 `API_STATUS.md`、`PDF_API_PARITY_MATRIX.md`、使用文档和 Excel。

Excel 中每项至少填写：文档/PDF 页、接口/功能号、模式、盘后可测性、状态、Linux/Mac 日期与
脱敏结果、wire method/tag、请求构造、请求/响应字段类型、回调/返回合约、证据、下一步和
备注。台账由下列脚本从受审文档重建：

```bash
node tools/build_api_acceptance_tracker.mjs
```

输出位置为 `outputs/01a03c21-40ce-7230-8082-fa2313f6d1c6/TGW_Mac_API_验收台账.xlsx`。

## 5. 当前推荐盘后顺序

| 顺序 | 接口/子范围 | 原因 |
|---:|---|---|
| 1 | `QuerySnapshot` 已有 SZSE ETF 子范围 | ~~补 `kDataEmpty`、同步/异步错误合约~~ **已完成（2026-08-26，`LIVE_ALIGNED`，证据 `docs/evidence/query_snapshot_error_async_contract.md`）** |
| 2 | `QueryCodeTable` 全市场 shape | ~~必须使用异步回调完整累计所有批次，再取脱敏摘要~~ **已完成（2026-08-26，wire 已证 + Mac `ARM_IMPLEMENTED`；服务端全市场缺第 3 包阻塞成功同参，证据 `docs/evidence/query_code_table_live_closure.md`）** |
| 3 | `QueryKline` 分钟族（随后分钟族逐周期） | ~~季线/年线~~ **季线与年线已完成（2026-08-26，`LIVE_ALIGNED(quarterly only)`/`LIVE_ALIGNED(yearly only)`）**；分钟族开始 |
| 4 | `QuerySecuritiesInfo` 下一市场/多 item | ~~SSE 单代码~~ **已完成（2026-08-26，`LIVE_ALIGNED(SSE single code only)`，证据 `docs/evidence/query_securities_info_sse.md`）**；SZSE/NEEQ 另取证 |
| 5 | `QueryExFactorTable` 下一代码/多代码 | ~~`000001` 单代码~~ **已完成（2026-08-26，`LIVE_ALIGNED(000001 only)`，证据 `docs/evidence/query_ex_factor_table_000001.md`）**；其它代码另取证 |
| 6 | `QueryETFInfo` SZSE 单 ETF | SSE `510300` 已闭环；新市场必须另做 oracle/wire/Mac |
| 7 | `get_code_info/get_code_list` | 验证 AmazingData wrapper 到底层接口的真实映射 |
| 8 | 一个 ThirdInfo 功能号 | 按 PDF 第 7 章从低风险、窄结果接口逐个推进 |

## 6. 每轮结束检查

- Linux/Mac 都调用 `Close()`；订阅任务另须 `UnSubscribe()`。
- 远端临时目录、`.so`、原始捕获和 `__pycache__` 删除。
- `galaxy-relay` 的 active/inactive 状态恢复到任务开始状态。
- `python -m unittest discover -s tests -v` 与 `python -m compileall -q src/python examples tools` 通过。
- 仓库中没有账号、密码、token、MAC、原始行情、完整响应或捕获文件。
