# `machome.ibkr.quote.v1` 本机行情合同

传输为仅限当前用户访问的 `QLocalServer` Unix socket。每一帧是一行 UTF-8 JSON，
以 `\n` 结束；请求上限由配置控制，响应硬上限 1 MiB。它不是公网协议，也不提供下单、
账户、持仓或取消订单能力。

客户端首帧必须为：

```json
{"request_id":"1","type":"hello","protocol":"machome.ibkr.quote.v1"}
```

握手成功后支持：

| 请求 | 必填字段 | 响应 |
|---|---|---|
| `status` | 无 | TWS 连接、心跳、订阅数、重连次数 |
| `quotes` | 无 | 配置内全部合约的当前快照 |
| `quote` | `subscription_id` | 单一合约快照 |
| `subscribe` | `subscription_id`, `contract` | 建立客户端租约；`created` 表示是否新建底层 TWS 订阅 |
| `unsubscribe` | `subscription_id` | 释放客户端租约；`removed` 表示是否取消了底层 TWS 订阅 |
| `ping` | 无 | `pong` 和 UTC 时间 |

所有响应都含 `protocol`、`type`、`ok`，并原样回送 `request_id`。错误响应含
`error.code` 与 `error.message`。价格与 Decimal 数量在 JSON 中均为十进制字符串，
避免不同语言的浮点二次取整；未收到的字段为 `null`。`fresh` 只表示 bridge 最近收到该
合约更新，不代表交易所一定处于连续交易状态。消费者必须同时检查 `status.ready`、
单合约 `fresh`、`market_data_type` 和自身交易时段。

## 动态订阅租约

`contract` 必须明确提供 `symbol`、`security_type`、`exchange` 和 `currency`；
可选提供 `con_id`、`primary_exchange`、`expiry`、`multiplier`、
`trading_class` 和 `generic_ticks`。同一 `subscription_id` 只允许一个完全相同的
合约身份；如果 ID 相同但任一字段不同，bridge 必须返回
`subscription_conflict`，不得猜测或覆盖。

多个本地客户端订阅同一 ID 时，bridge 只向 TWS 发一次
`reqMktData`。客户端断开会自动释放其全部租约；最后一个租约释放后才调用
`cancelMktData`。配置文件中的静态订阅为 pinned，不因客户端断开而取消。
TWS 重连后，bridge 必须重放 pinned 和尚有租约的动态订阅。

`status` 中的 `configured_subscription_count`、`dynamic_subscription_count` 和
`subscription_count` 用于核对合并效果。`quote`/`quotes` 还必须返回
`bridge_ready`；即使 socket 存活，TWS 尚未完成握手时也不得将缓存值冒充为就绪。

bridge 使用非阻塞本地 socket 写出，并对每个客户端设置有界队列。单个慢消费者
超过队列上限时只断开该客户端，不阻塞 EReader 线程或其他上传器。

合同演进规则：新增可选字段不升主版本；删除/改义/改类型必须新增协议版本。旧 Python
消费者接入时必须严格拒绝未知主版本，不能静默回退到直接新建 TWS 连接。
