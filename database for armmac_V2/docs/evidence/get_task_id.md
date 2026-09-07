# GetTaskID 对齐证据

- Scope: `IGMDApi::GetTaskID()` 的无入参、本地任务号生成；验证返回 Python `int`、本地时间片格式、同秒序号、跨秒重置与线程并发唯一性。没有登录、业务查询或实时行情。
- PDF: `reference/manuals/中国银河证券格物金融服务平台(TGW)开发手册(C++版).pdf`，PDF 第 25 页 / 正文第 17 页，基础接口 `GetTaskID`。声明为 `static int64_t GetTaskID();`，托管机房和互联网模式适用；返回“唯一的回放任务 id”，格式为 `MMDDHHmmSS + 序列号(1~1000000)`。手册示例 `524153030000001` 对应 5 月 24 日 15:30:30、序号 1。
- Header delta: V1.0.8 `reference/vendor-headers/v1.0.8/linux/tgw.h:65-71` 同样声明 `static int64_t GetTaskID();`，并注明供需要 `task_id` 的查询和回放使用、两种模式适用。头文件没有改变 PDF 的时间/序号规则，也没有规定并发、时钟回拨或序号达到 1,000,000 后的行为。

## 静态与运行时对照

| 来源 | 返回类型与组成 | 时间与序号规则 | 已知边界 |
|---|---|---|---|
| PDF | `int64_t`；`MMDDHHmmSS + sequence` | sequence 为 `1..1,000,000`；普通 sequence 1 的示例为 6 位零填充 | 只给范围；未说明超出上限、时钟回拨或线程规则 |
| V1.0.8 header | `static int64_t GetTaskID()` | 无额外格式差异 | 供查询/回放；双模式 |
| 官方 Linux Python wrapper | Python `int`；本次脱敏样本长度为 15、全为数字 | 16 个无登录样本均匹配服务器本地秒前缀；两个秒各从 sequence 1 连续递增（分别 1–12、1–4） | 同秒唯一、跨秒前缀改变均已观测；没有执行百万次或人工时钟回拨 |
| 修改前 Mac | Python `int`，但仅为 backend 上的 `_task_seq = previous + 1` | 1、2、3…；且调用会惰性创建 backend | 不符合 PDF/官方时间片；无锁 |
| 修改后 Mac | Python `int`；`int(MMDDHHmmSS + f"{sequence:06d}")` | 全局生成器、单锁内读本机本地时钟；新秒从 1 开始 | sequence 1,000,000 仍保留为 7 位十进制而不截断；同一秒第 1,000,001 次明确抛 `RuntimeError`，不伪造未取证的官方回绕行为 |

- Linux oracle: 开始与结束均通过 `systemctl is-active galaxy-relay` 观测为 `inactive`。以 `galaxyrelay` 用户、`/opt/galaxy-relay/venv/bin/python` 的官方 `tgw` 模块从标准输入运行一次纯本地探针：不调用 `Login`、不读取凭据、不建立业务连接或网络会话。探针只输出返回类型、长度、纯数字、与服务器本地时间的前缀匹配、相邻序号关系和范围；输出为：16 个样本、类型仅 `int`、长度仅 15、全为数字、全部前缀匹配服务器本地秒、两个时间前缀、跨秒改变、各秒序号范围分别 1–12 和 1–4、各范围相邻差为 1、全部唯一。
- Wire: N/A。该方法无请求、无登录、无 TLS/WSS/wire 数据，未捕获也不需要捕获。
- Arm: 新增 `src/python/tgw_macos/_task_id.py`。`TaskIdGenerator` 以锁保护“读本地秒→判断秒变化→序号递增→组装”的单一临界区；`interface.GetTaskID()` 只调用该生成器，不再创建或修改 backend。`compose_task_id` 只接受手册允许的 `1..1,000,000`；未文档化的同秒溢出显式失败，下一本地秒自然重置为 1。
- Tests: `PYTHONPATH=src/python python3 -m unittest -v tests/test_get_task_id.py`：5/5 通过（公开返回形状、PDF 示例/上下界、跨秒、上限与下一秒、32 线程 × 128 次唯一性）。最终全量 unittest 与 compileall 结果见本文件末尾。
- Live diff: 无入参的本地方法，没有账号、市场、日期或 wire 可作“同参”比较。Linux 官方与 Mac 的可比脱敏不变量均为 Python `int`、本地秒前缀、同秒连续唯一序号和跨秒序号重置；两台机器各自使用本机本地时钟，因此不比较实际 id 数值。
- Cleanup: 远端探针经 stdin 执行，未写临时脚本、动态库、capture 或凭据；服务前后保持 `inactive`。本地只删除本项生成的 PDF 渲染和本项 `.pyc`，不触碰其他并行任务的临时文件。
- Proposed status: `LIVE_ALIGNED(local task-id format, same-second sequence, cross-second reset; overflow behavior unobserved)`。
- Open risks: 官方对 sequence 达到 1,000,000 后在同一秒的行为、系统时钟回拨/跨日期（尤其跨年）和跨进程唯一性均未文档化且未观察。Python 实现在同秒上限后显式失败，避免无根据地声称官方会等待、回绕或发出重复 id。

## 最终验证

- `PYTHONPATH=src/python python3 -m unittest -v tests/test_get_task_id.py`：5/5 通过。
- `PYTHONPATH=src/python python3 -m unittest discover -s tests -v`：141/141 通过。
- `PYTHONPATH=src/python python3 -m compileall -q src/python examples tools`：成功（无输出、退出码 0）。
