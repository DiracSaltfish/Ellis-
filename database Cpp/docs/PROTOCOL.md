# 网络、鉴权与数据包协议

本文件记录本实现必须保持不变的 wire 契约。来源是授权账号下的官方 Linux SDK
动态证据和原 Mac Python 主线的已验收测试，不采用早期未验证的 AmdHeader 推断。

## 1. 连接层

互联网模式使用：

```text
TCP -> TLS -> RFC 6455 WebSocket -> UTF-8 JSON / 0x59+Zstandard JSON
```

- 持久 push 路径：`/amd/dgw/push`；
- 一次性查询路径：`/amd/dgw/dgw1_query`、`/amd/dgw/dgw2_query`；
- WebSocket v13，客户端帧必须 mask；数据帧 opcode `0x2`；
- upgrade User-Agent 保持 `WebSocket++/0.8.2`；
- push 登录后每 5 秒发 ping payload `Heartbeat`；
- 服务器可能发普通 JSON、标准 ZSTD frame，或前置字节 `0x59` 的 ZSTD frame；
- 解压结果可能是 `JSON`，也可能是用 ASCII 0x60 及空白分隔的多个 JSON object。

TLS 只在该连接的独立 `SSL_CTX` 中降低 OpenSSL security level 以兼容旧端点，仍保留：

- CA 链验证；
- SNI；
- `www.dgw.com` hostname verification；
- TLS 上限 1.2。

不得通过全局环境关闭证书校验。

## 2. 登录鉴权

登录 id 固定为 0：

```json
{
  "headers":{"id":0,"userName":"<user>"},
  "method":"ReqLogon",
  "params":{
    "Username":"<user>",
    "Password":"<password>",
    "MacAddress":"aa:bb:cc:dd:ee:ff",
    "Version":"V4.3.0.260626-rc2.0-YHZQ",
    "ProcessId":12345,
    "ForceLogout":false,
    "PushBandWidth":0.0,
    "QueryBandWidth":0.0
  }
}
```

只有响应同时满足以下条件才建立会话：

- `status == 0`；
- `headers.tag == "OnRspLogon"`；
- `headers.token` 是非空字符串。

后续请求继续携带同一 `userName` 与 token。库不把 token 返回给业务层，不在日志中
打印完整 envelope。MAC 可用 `TGW_MAC_ADDRESS` 明确覆盖；默认从当前 Mac 的首个
UP、非 loopback、6 字节 AF_LINK 接口生成小写冒号格式。

## 3. 请求 ID 与路由

- 查询 task id：本地时间 `MMDDHHmmSS` + 同秒 6 位序号；
- push 订阅序列：从 1,000,000 开始；
- ETF/证券信息 codelist 序列：独立从 1 开始；
- 一次性查询从 `(task_id - 1) % endpoint_count` 选择首路径，然后顺序 fallback。

一次性查询按 `pack_num=1..all_pack_num` 收齐。成功后发 `ReqGetComplete`；错误包后
直接关闭，不发送完成。代码表缺包额外使用 `ReqGetPackage {"pack_num":"N,"}`。

## 4. 解析不变量

所有 parser 都执行 fail-closed 校验：

- status、tag、request id（适用时）；
- 分包计数范围、一致性、重复和完整性；
- JSON 容器类型；
- CSV/反引号字段数；
- 数字/字符串/ASCII 字段类型，以及 `uint8_t`/`uint32_t` 目标范围；
- ETF 35+1 与成分 13 个 numeric slot；
- 证券信息严格 43 个 numeric slot；
- 历史快照严格 36 个 CSV wire 字段和四组十档数组；未取证的 20..35 槽保留原文。

未知 tag、混合成功/错误快照、未观察到的 packet counter、额外/缺失 slot 均抛
`ProtocolError`。禁止“尽量解析”后静默返回部分数据。

固定 schema 不模仿 Python 的动态类型：wire 缺失字段用 `optional`，ETF/证券信息用
静态结构体，复权小数同时保存 binary64 和原始十进制 token。完整矩阵见
[类型审计](TYPE_AUDIT.md)。

## 5. 线程模型

push 和每个 query WebSocket 各有一个 reader；写操作有独立互斥锁。reader 按
`headers.id` 投递给 waiter，其余消息进入 push event 队列。心跳是独立线程。

关闭顺序必须是：设置 stop → 尝试 WebSocket close → `shutdown()` 唤醒 reader →
join reader/heartbeat → `SSL_free()`。先释放 SSL 再 join 会在 OpenSSL 3.x 产生真实
use-after-free；该顺序已有 ASan 在线登录验证。
