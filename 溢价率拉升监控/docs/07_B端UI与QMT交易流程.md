# 07｜B 端 UI 与 QMT 交易流程

## 平台

Qt6 Widgets/C++20 单一源码。首阶段已编译 macOS arm64 `.app`；`windows-msvc-release` preset 和平台中性 Qt API 已提供，但没有 Windows 主机实测/安装包承诺。

## 首页双列表

首页是两个互不影响的页签，启动时默认打开“信号列表”，只有手工选择才进入“全局列表”。两表都显示标的、名称、价格、涨跌幅、可卖 IOPV 溢价、IOPV 状态和信号，并都从 `Qt::UserRole` 读取稳定 symbol，双击进入同一个详情流程。

- 全局列表包含 A 当前观察清单，始终按证券代码升序固定排列。行情刷新和信号触发均不改变行位置。
- 信号列表按标的去重；同一标的再次触发时覆盖为最新事件并移动到最上方，整体由最近到最远排列。A 重连、全量同步和 B 重启都不自动清除。
- 每个信号行最右侧有“本次移除”。它只修改本机 B 的信号缓存，不改变 A 观察清单、TGW 订阅或其他 B；被移除事件的水位也会保存，重连时同一条历史补发不会立刻恢复，更新的后续信号仍会重新加入。
- 信号列表保存在 `client-signal-list.json`，与 `client-settings.json` 同目录。已读序号仍写 `QSettings`，不影响其他 B。

B 连接 A 后只接受 A 自动发的一次全量同步，不再重复请求。全量期间关闭重绘，实时行情按证券合并后最多每 100ms 刷新一次；信号不节流，立即更新独立信号列表并鸣笛。表头使用固定/可拖动列宽，禁止 `ResizeToContents` 在每帧扫描全表。202 标的高频仿真下，修复前彩虹圈进程持续 100% CPU，修复后稳定约 2% 且 macOS Accessibility 状态可立即读取。

场内最新价、盘口与缓存卖出价固定三位小数；核心仍是 e6 整数。IOPV 保留四位；无 IOPV 显示“无 IOPV · 盘口模型”而非伪造 0% 溢价。

同步完成后的实时 signal 使用设置中选择的内置音色和 1–3 次重复播放，并可发系统托盘消息；历史补发只恢复信号列表，不响铃、不弹窗。

## 详情数据

双击主表后才创建 B 共享 detail socket 并订阅，立即收 A 缓存十档，之后每新 TGW 快照主推。全部详情关闭后 detail socket 也中断，主表只保留 summary 连接。每 B 最多 4 详情；关闭即 unsubscribe。

新鲜度同时检查 B 最后收帧时间和 TGW `orig_time`。实测盘中帧间隔中位数约 3.016 秒，因此连续竞价时段超过 10 秒（约连续丢 3 帧）才提示过旧；午休等低频阶段为 75 秒。原 3.000 秒阈值低于正常帧间隔，会频繁误报，已取消。

盘口不再用交叉表格，固定纵向显示卖5、卖4、卖3、卖2、卖1、买一分隔线、买1、买2、买3、买4、买5。每档是「档位/价格/数量」按钮；单击只将该档三位小数价格填入当前 QMT 页签的限价框，绝不发单。

顶部「设置…」打开持久化设置页，可编辑 A WebSocket IP/端口、QMT1/QMT2 TCP IP/端口、声音、弹窗和主表刷新间隔。声音提供标准短音、双音提醒、上扬提醒三种内置预置，可试听并选择响 1/2/3 次。保存使用 `QSaveFile` 原子替换 `client-settings.json`；A 立即重连，已打开详情页关闭，下一次打开时按新 QMT 地址建立连接。

B 默认开放人工申购、赎回、快速卖出和撤单；`--read-only` 只保留为紧急验收开关。放开按钮并不授权程序自动发单：真实 QMT 下单/撤单只能由用户本人操作，自动化、冒烟和 Codex 界面检查一律不得点击或调用。

## QMT 页签与消息

两个 profile 独立 TCP JSONL，按钮只发当前页签。

```json
{"type":"etf_order","action":"PURCHASE","code":"159518.SZ","qty":1,"client_order_id":"..."}
{"type":"etf_order","action":"REDEEM","code":"159518.SZ","qty":1,"client_order_id":"..."}
{"type":"order","code":"159518.SZ","side":"SELL","price":1.234,"qty":100000,"client_order_id":"..."}
{"type":"cancel_order","order_id":"..."}
```

还使用 `sync_request(target=all)/query_orders/query_positions`。不改 Backend 10.1，不增服务端幂等字段。

B 按 Backend 10.1 的真实推送格式解析 `orders_data(sync_mode=full/delta, data/upserts/remove_ids)` 与 `positions_data(sync_mode=full, data)`；明确结果类型为 `order_result`、`etf_order_result`、`cancel_result`。`client_order_id` 是 10.1 已有的回认/备注字段，不在后端新增幂等逻辑。兼容解析旧测试端的 `orders_full/positions_full/order_ack/cancel_ack`，但不依赖这些旧别名。

## 人工操作语义

- 申购/赎回固定 1 篮子，双击立即发，无确认框。
- 快速卖出默认 `floor_to_lot(min(100000,可用持仓))`，数量控件最大值就是此默认，只允许手工减小。两个卖出按钮共用这一数量。
- `双击买一价快速卖出`始终使用最后非零买一；没有非零买一则不发。
- `双击限价卖出`使用限价框当前值；可单击盘口档位填入或手工输入。输出前以四舍五入恢复为 `price_e6` 整数，避免浮点尾差。
- 报价超过时段阈值或 A 断线保持红色警告；按用户决定，有缓存买一/限价时仍不禁止卖出。
- 委托表双击 order_id 立即撤单。部分成交委托保留，不因 delta 误删。

## 未知结果和回放

发送后直到明确 result 或 10s，同标的同方向锁定。10s 自动查当日委托并解锁，绝不自动重发。

回放模式保留全部真实交易按钮，按用户决定不禁用；主表和详情顶部持续红色 `REPLAY` 水印，并逐帧显示回放时间、实时时间差和真实 QMT 风险文字。
