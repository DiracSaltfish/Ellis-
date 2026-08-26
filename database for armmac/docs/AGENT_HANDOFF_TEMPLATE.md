# 可直接交给执行 Agent 的任务模板

## 通用模板

把下面文本中的尖括号内容替换后，作为一个 Agent 的完整任务。一次只分配一个接口或一个已限定的枚举/周期。

```text
工作目录：/Users/ellis/工具程序开发/database for armmac

你只负责 <接口名 + 明确子范围>，不要顺手实现其它接口。

开始前完整阅读：
1. docs/DEVELOPMENT_STATUS_AND_HANDOFF.md
2. docs/AGENT_PARITY_WORKFLOW.md
3. docs/API_STATUS.md 和 docs/PDF_API_PARITY_MATRIX.md 中对应行
4. reference/manuals 中两份 PDF 的对应完整页面
5. reference/vendor-headers/v1.0.8 中对应头文件

严格按以下顺序工作：
1. 建立 PDF/头文件/官方 Python/本地 ctypes 的逐字段契约表。
2. 检查 ssh bj 上 galaxy-relay 的初始状态。仅在服务 inactive 时运行一次独立 Linux x86 官方 SDK 最小只读 oracle；结束必须 Close。
3. 输出只能包含返回码、行数、列名、类型、不变量、包数/回调计数和间隔；禁止输出账号、密码、token、MAC、原始价格或原始响应。
4. 对官方进程做一次最小 SSL_read/SSL_write 捕获，用现有安全分析器只保留 path/method/key/type/enum mapping/tag/pack controls/data shape。分析后删除完整捕获。
5. 只实现实际证明的 wire 分支；未知枚举/周期必须显式 NotImplementedError。
6. 添加结构、envelope、parser、错误包、多包重组和 Linux/Arm 同参测试。
7. 新建 docs/evidence/<接口小写>.md，使用工作流规定的固定格式。
8. 清理远端临时文件，确认 galaxy-relay 恢复任务开始时状态。

不要直接修改 PDF_API_PARITY_MATRIX.md 的状态。最终只汇报：修改文件、证据文件、测试命令/结果、拟议状态、未通过项和清理状态，等待主验收者复验。
```

## 首个推荐任务：QuerySnapshot

```text
接口范围：互联网模式 QuerySnapshot；只验证 SZSE ETF 159518、单个历史交易日、窄时间窗、data_type=0、level_type=0、return_df_format=False。

重点资料：
- C++ 手册 PDF 页 34（正文 26）：QuerySnapshot/ReqDefault。
- AmazingData 手册 PDF 页 25–26（正文 21–22）：query_snapshot。
- AmazingData 手册 PDF 页 140–141（正文 136–137）：Snapshot 输出字段。
- TGW-SDK_V1.0.8/Cplusplus/Redhat-7.6/c++/include/tgw_struct.h 的 ReqDefault。

必须先证明：
- PDF 未列出的 level_type 是否参与官方 Python 对象和 wire；
- request method、全部 key 及顺序；
- date/begin_time/end_time 的整数编码；
- data_type/level_type 是否转换；
- 响应 tag、包号、data 容器、单行字段数与字段类型；
- 官方 wrapper 对 Snapshot 的列名、类型、缩放和默认字段。

验收边界：
- 不扩展到港股委托挂单 data_type=1 或经纪席位 data_type=2；
- 不扩展到指数、期权、期货；
- 如果服务端返回 1000 / accept conn active close，停止密集重试，保留静态/协议阶段状态并报告；
- 只有 Linux 与 Arm 同参数结果通过，才能拟议 LIVE_ALIGNED(SZSE ETF snapshot query, data_type=0)。
```

## 第二个推荐任务：QueryCodeTable

```text
接口范围：互联网模式 QueryCodeTable；先验证单市场的安全最小返回形状，不做全市场大结果抓取。

重点资料：C++ 手册 PDF 页 36（正文 28）以及对应 CodeTable 回调和数据结构。先查明官方 Python wrapper 是否允许按市场/代码缩小范围；如果公开接口没有过滤参数，oracle 仍只能输出总行数、列名和类型，禁止保存原始代码表。

必须证明 method、tag、分页、完成消息、返回列/类型和内存/回调所有权。其余要求遵循通用模板。
```
