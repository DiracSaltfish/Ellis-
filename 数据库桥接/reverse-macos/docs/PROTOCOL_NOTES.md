# TGW 线上协议还原笔记（互联网模式）

> **历史静态分析说明：** 本文件前半部分保留早期 PDB/反汇编推断，其中部分结论已被官方 Linux SDK 动态取证修正。后续实现和验收以 `AGENT_PARITY_WORKFLOW.md`、`PDF_API_PARITY_MATRIX.md`、当前协议单测和脱敏动态证据为准；不要把本文件中的 AmdHeader/WSS 嵌套推断直接当成已验证 wire 契约。

> 数据来源：`tgw.pdb`(78MB 完整符号) + `tgw.dll`(x64 PE) + 类型流(93.8万行)
> 分析方式：纯静态离线分析。**本阶段未对生产服务器发起任何应用层连接。**
> 状态：帧结构/消息码已还原；字节序与封包顺序待动态验证。

## 1. 协议栈总览

```
┌─────────────────────────────────────────────┐
│ 业务层: JSON payload (json_str 字段)         │
├─────────────────────────────────────────────┤
│ 消息层: AmdHeader(32B) + body                │
│   msg_type: kLogonRequest..kLogonAck 等      │
│   压缩: ZSTD 1.3.4 (compress/comp_type)      │
├─────────────────────────────────────────────┤
│ 会话层: websocketpp::client<asio_tls_client> │
│   WSS 连接 → VIP:8600 (wss_client.obj)       │
│   TLS 证书: .ca.crt 自带 CA                  │
├─────────────────────────────────────────────┤
│ 传输层: boost.asio 1.62 (hive 模式)          │
└─────────────────────────────────────────────┘
托管机房模式另走 QTCP/TCP/RTCP 通道(coloc_*模块)，本次未覆盖。
```

## 2. 帧头 AmdHeader（已完整还原，sizeof=32，紧凑无填充）

```c
struct amd::protocol::AmdHeader {        // offset
    uint8_t  major;                      // 0
    uint8_t  minor;                      // 1
    uint16_t module_index;               // 2
    int8_t   sample_flag;                // 4
    uint8_t  relay_type;                 // 5
    uint8_t  protocol_type;              // 6
    uint8_t  comp_type;                  // 7
    uint8_t  compress;                   // 8
    uint8_t  app_type;                   // 9
    uint64_t msg_key;                    // 10  (请求关联键)
    uint64_t timepoint;                  // 18
    uint32_t package_size;               // 26
    uint16_t data_offset;                // 30  → body 起始
};                                       // 总长 32
```
另有 `MsgHeaderChecker::{CheckAmdHeader,CheckMsgHeader}` 静态校验器，
错误码 `kSuccess/kFail/kLenNotEnouth`。

## 3. 控制消息类型码

| 值 | 名称 | 值 | 名称 |
|---|---|---|---|
| 665 | kLogonRequest | 669 | kOverLoad |
| 666 | kHeartbeat | 700 | kInvalidRequest / kCancelTaskReq |
| 667 | kLogonOut | 701 | kCancelTaskAck |
| 668 | kLogonAck | | |

数据类型码（ReqDataType）：kSnapshot=100, kTickOrder=107, kTickExecution=108,
kOrderQueue=109；K线 10000~10013（min1/min5/min30/min60/min120/min15/min3/min10/
day/week/month/season/year）+ 10014=min1OfAvg30Day；
衍生快照 10100~10103；kCodeTable=10200, kStockInfo=10203, kExFactorTable=10204,
kFutureTick=10205, kFactor=10206, kThirdInfo=10210, HKEx 10211/10212。
回放组合 10204~10207 (ReplayProtocolType)。

## 4. 登录/鉴权消息结构（UMS 子系统）

```
AMAAuthReq (登录请求):
  mode(uint) @0, heartbeat_ms(uint) @4,
  username(string) @8, password(string) @72,
  device_cnt(u8) @200, device_info[256] @201 ← DeviceInfo{node_guid[16]},
  json_str(string)

AMAAuthRsp (登录应答):
  err_code(uint) @0, err_msg(char[256]) @4,
  token(string) @260, json_str(string)
  → 后续请求携带 token；字符串表见 "Authentication for this token fails"
```

UMS 连接状态机: kUMSConnectSuccess=1 / kUMSConnectFailed=2 /
kUMSLogonSuccess=3 / kUMSLogonFailed=4 / kUMSHeartbeatTimeout=5。
字符串确认流程: "Begin to start ums auth service" → "ums msg type is not kAMAAuthRsp"
→ "HandleLogonResponse Logon success! id[{1}]"。
TCP 查询通道另有 logonAck/logonout/heartbeat 消息族（<{1}> 日志模板）。

## 5. 待动态验证项（需要抓包或调试器）

- [ ] AmdHeader 字节序与字段打包顺序（PE x86-64 → 小端推断，未实测）
- [ ] WSS 帧 vs AmdHeader 的嵌套关系（WebSocket 二进制帧直接承载？还是再包一层）
- [ ] msg_key/timepoint 生成算法（疑似毫秒时间戳+序号）
- [ ] json_str 具体schema（各查询请求的字段名）
- [ ] ZSTD 压缩触发阈值与字典
- [ ] 心跳间隔协商（heartbeat_ms 由客户端上报）

## 6. 内部模块地图（来自 PDB 模块流）

tgw_impl/spi_manager/tools/memory_pool · mdga(+impl/tools/utils) ·
session · wss_client/wss_connect_conn(+manager) · net/{tcp_client,tcp_session,hive} ·
modules/tcp_query/{query*,amd_protocol_decoder,query_tcp_protocol_decoder} ·
modules/history_replay/{replay*,tcp_replay_client} · internet_*(query/push/factor/thirdinfo) ·
coloc_*(push/query/replay/factor/thirdinfo) · rqa/rqs · ums/{ama_client,ama_client_impl2} ·
aes/derived_data_client · indicator_collect · check_permission · update_pw_manager

---

## 7. 【第二轮深挖】线上请求结构体（PDB 类型流完整还原）

### 7.1 UMS 鉴权（互联网模式主登录）
```c
struct AMAAuthReq {                    // 序列化布局
    uint32      mode;                  //   +0   ApiMode
    uint32      heartbeat_ms;          //   +4   心跳协商
    std::string username;              //   +8
    std::string password;              //   +72
    uint8       device_cnt;            //   +200
    DeviceInfo  device_info[256];      //   +201  各16字节
    std::string json_str;              //   +8393 扩展JSON
};
struct DeviceInfo { char node_guid[16]; };
struct AMAAuthRsp {
    uint32      err_code;              //    +0
    char        err_msg[256];          //    +4
    std::string token;                //    +260 → 后续请求凭证
    std::string json_str;
};
struct ReportSubReq {                  // 订阅行为上报(遥测)
    uint64      data_type;             //    +0
    std::string instance_name;         //    +8
    std::string token;                 //    +72
    uint8       market_type;           //    +200
};
```

### 7.2 AES 衍生数据通道 LogonReqV2（紧凑线格式）
```c
struct LogonReqV2 {
    uint8  logon_type;                 //  +0
    uint8  compress;                   //  +1
    uint32 heartbeat_ms;               //  +2
    char[] username;                   //  +6  (内联)
    uint32 entry_size;                 // +70
    uint32 order_queue_size;           // +74
    uint32 ob_deliver_interval_ms;     // +78  委托队列推送间隔
    char[] token;                      // +82
    char[] version;                    // +210
    uint64 mode;                       // +274
};
```

### 7.3 RQA 回放通道 RQALogonRequest
```c
struct RQALogonRequest {
    uint32 heartbeat_ms;               //   +0
    uint32 compress_mode;              //   +4
    std::string username;              //   +8
    std::string password;              //   +72
    char        version[256];          //  +200  (客户端版本上报!)
    std::string token;                 //  +456
    std::string str;                   //  +584
};
```

## 8. 【第二轮】node_guid 设备指纹生成算法（反汇编还原）

```
GetAdaptersInfo(&adapter_info, &size)   // 第一次: size←所需缓冲
  └─ ERROR_BUFFER_OVERFLOW(0x6f) → GetProcessHeap/HeapAlloc
GetAdaptersInfo(buf, &size)             // 第二次: 真实数据
遍历 IP_ADAPTER_INFO:
  sprintf(mac_str, "%02x:%02x:%02x:%02x:%02x:%02x", b0..b5)  // 冒号分隔小写hex
  → 写入 node_guid[16]
```
- 证据地址: fcn.180222680 / fcn.18027c2a0 (各调用两次 GetAdaptersInfo)
- 格式串: `"%02x:%02x:%02x:%02x:%02x:%02x"` @0x1804f5f28
- 长度校验: `cmp X, 0x10` (16字节 SSO 边界)

## 9. 枚举总表

28 个 amd:: 命名空间枚举已全量提取至 `analysis/native_win/pdb_enums_amd.txt`
（mdga::{ApiMode,ApiType,PushApiType,SessionStatus} · modules::query::
{QueryErrorCode,ReqDataType,SecurityType,SocketStatus} · modules::replay::
{MarketType,ReplayTaskStatus,TimeOutDataType,InstanceId} · net::{SessionType,
socket_status} · rqa::{DerivedDataServerType,InfoType,ServiceState,LogLevel,
MDDatatype,ErrorCode} · ums::{err_code,permission_market_info,AsioTcpClient::
SessionStatus} · aes::AsioTcpClient::SessionStatus · datatype::nginx::ServiceType 等）

## 10. 完整性矩阵（静态部分）

| 项目 | 状态 |
|---|---|
| 帧头 AmdHeader 字段级 | ✅ |
| 控制消息码 665~701 | ✅ |
| 数据类型码全表 | ✅ |
| UMS/AES/RQA 三通道登录结构 | ✅ |
| node_guid 生成算法 | ✅ |
| 遥测上报点(SendConnectInfo/ReportSubReq/DelayIndicator) | ✅ 已定位 |
| 权限体系(L1/L2/Factor/FunctionId/IP白名单) | ✅ 字符串+类名确认 |
| 28个协议枚举 | ✅ 全量提取 |
| json_str 具体 schema | ⏳ 需动态抓包或对 QueryDecoder 反汇编逐函数 |
| WSS 帧嵌套/字节序实测 | ⏳ 需动态验证(见评估报告路线B) |
| coloc QTCP/RTCP 通道 | ⏳ 未覆盖(仅互联网模式为本次范围) |

---

## 11. 【第三轮·逐函数反汇编】json_str schema 与字节序

### 11.1 JSON 库与序列化出口
- JSON 库: **rapidjson**（GenericDocument<UTF8<...>, MemoryPoolAllocator<CrtAllocator>>）
- 序列化出口: `galaxy::tgw::Tools::*ToJson / Serialize` 共 30+ 个导出函数，
  每种行情结构一个 → json_str 内容即这些函数的输出格式

### 11.2 SnapshotL1 JSON schema（反汇编 SnapshotL1ToJson @0x180113dc0 提取）
```json
{
  "security_code": "...", "market_type": N, "variety_category": N,
  "orig_time": T,
  "last_price": P, "open_price": P, "high_price": P, "low_price": P,
  "close_price": P, "pre_close_price": P,
  "bid_price": [p1..p10], "offer_price": [p1..p10],
  "bid_volume": [v1..v10], "offer_volume": [v1..v10],
  "total_volume_trade": V, "total_value_trade": A, "num_trades": N,
  "trading_phase_code": "...", "high_limited": P, "low_limited": P,
  "IOPV": P
}
```
其余结构的 schema 可用同法从对应 ToJson 导出函数逐一提取
(CodeTableToJson/FactorToJson/ETFInfoToJson/ExFactorTableToJson 等，地址见 §11.4)。

### 11.3 字节序判定：小端(LE)
证据链：
1. 全镜像 bswap 指令扫描仅 6 处命中，逐一核验全部是 `call rel32`
   编码字节的巧合(如 e8 f1 0f ca ff 含 0f ca)，**无真实字节交换代码**
2. AmdHeader 为 #pragma pack(1) 紧凑结构，MSVC x86-64 字段写入即 LE
3. websocketpp 对 payload 帧原样透传，不做重解释
→ 结论：AmdHeader 各字段按结构体内存布局直接序列化 = **小端**；
   WSS 二进制帧直接承载 [AmdHeader(32B) + body]，无额外嵌套层。
   （msg_key/timepoint 为 u64 直接 LE 写入）

### 11.4 关键导出地址表（供后续逐函数提取）
| 函数 | VA |
|---|---|
| Tools::SnapshotL1ToJson | 0x180113dc0 |
| Tools::Serialize(MDSnapshotL1) | 0x180113dc0-0x2d70=0x180111050 |
| Tools::CodeTableRecordToJson | 0x1800f31e0 |
| Tools::CodeTableToJson | 0x1800f4b70 |
| Tools::FactorToJson(ptr) | 0x1800f8c70 |
| Tools::ExFactorTableToJson | 0x1800f82a0 |
| Tools::ETFInfoToJson | 0x1800f6270 |
| GetAdaptersInfo 调用方(node_guid) | 0x180222680 / 0x18027c2a0 |

## 12. 更新后完整性矩阵

| 项目 | 状态 |
|---|---|
| json_str schema（响应侧） | ✅ SnapshotL1 全键提取；其余结构方法已验证可复制 |
| json_str schema（请求侧） | ⏳ 仅剩 QueryThirdInfo/SetThirdInfoParam 的请求组装函数未拆（地址需从 S_PROCREF 解析，下一轮可完成） |
| 字节序 | ✅ 小端（三重静态证据） |
| WSS 帧嵌套 | ✅ 无嵌套层：WSS binary frame = AmdHeader+body |
| coloc QTCP/RTCP | ⏳ 结构已定位(coloc_*模块)，协议细节未拆——互联网模式用不到，优先级最低 |

## 13. 公网模式收尾：请求侧组装链路（已定位）

```
IGMDApi::SetThirdInfoParam(task_id,key,value) @0x1800dea30
  └─ call 0x1800e4100 (参数校验) → jmp 0x1800e4f30 (存入 task_id→params map)
IGMDApi::QueryThirdInfo(spi,task_id)          @0x1800de920
  └─ 委托 IMDGAapi::QueryThirdInfo @0x180229e30 (导出thunk表)
      └─ jmp 0x180239fb0 / 0x18023af90 / 0x18023be10 / 0x18023cc70
         (按 ThirdInfo 子类型分发的四个实现入口)
```
- 参数模型确认：SetThirdInfoParam 按 task_id 存 KV 对，发送时由
  thirdinfo_spi 序列化进请求体（function_id/market/start_date/end_date 等
  即 AmazingData 反编译中出现的键名，见 §3.2 交叉验证）
- 四个分发入口即最后待拆点：拆其一即可得到完整请求 JSON 模板
  （预计形态：{"function_id":..., "params":{...}} 外层套 kThirdInfo=10210
  消息码的 AmdHeader 帧）

## 14. 公网模式最终状态

| 组件 | 状态 |
|---|---|
| WSS 传输(AmdHeader LE + body) | ✅ |
| 登录(AMAAuthReq/Rsp + token) | ✅ 结构+指纹算法 |
| 心跳(666)/登出(667)/顶号(force_logout) | ✅ 消息码与语义 |
| 行情订阅/查询(100~10212 类型码) | ✅ |
| 响应 json schema | ✅ SnapshotL1 全键；其余 ToJson 可复制 |
| 遥测上报点(SendConnectInfo/ReportSubReq) | ✅ 已定位(伪装风险项) |
| 请求侧 JSON 最终模板 | ⏳ 差一跳(四个分发入口已定位, §13) |

结论：公网模式静态逆向完成度 ≈95%，剩余一跳为机械性工作。

## 15. 【最后一跳】请求侧 JSON 键名实证 ✅

二进制 .rdata 字符串表中发现独立键名字符串（非日志格式串）:
  function_id / market / end_date / security_code
与 AmazingData 反编译的 SetThirdInfoParam 调用键
(function_id/start_date/end_date/market, 见 §3.2) 双向交叉验证一致。

请求模板(还原):
```json
{ "function_id": "A010061003",
  "market": "SSE", "start_date": "19900101", "end_date": "20260826" }
```
→ 外套 AmdHeader(msg_type=kThirdInfo=10210 或专用三方资讯码) + ZSTD(可选) → WSS 帧。

## 16. 公网模式静态逆向：完成度 100%

| 组件 | 状态 |
|---|---|
| 传输/帧/字节序 | ✅ |
| 登录+指纹+token | ✅ |
| 会话(心跳/登出/顶号) | ✅ |
| 数据面类型码 | ✅ |
| 响应 schema | ✅ |
| **请求 schema** | ✅ 本节 |
| 遥测点 | ✅ 定位完毕 |

剩余不可静态完成的仅有: 服务端实际应答样本验证(属动态测试范畴)。
