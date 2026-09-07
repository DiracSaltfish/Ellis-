# 鉴权头与客户端检测评估报告

> 结论先行：**存在多层客户端检测与风控遥测**。用重建客户端直连生产服务器
> 技术上难以完全伪装，且有账号被停服的实际风险。建议不要直连，理由见 §4。
> 本评估基于纯静态分析，未连接服务器。

## 1. 鉴权头构成（静态还原）

一次完整登录 = **三重凭证 + 通道协商**：

| 凭证 | 载体 | 说明 |
|---|---|---|
| username/password | `AMAAuthReq` 明文字段 | 客户端侧长度校验 kUsernameLen/kPasswordLen |
| token | `AMAAuthRsp` 下发，后续请求携带 | 服务端签发；"Authentication for this token fails" |
| IP 白名单 | 服务端校验 | 字符串 "This machine IP doesn't exist in the IP White list" |

外加：`mode`(互联网/托管) 与 `heartbeat_ms`(心跳间隔协商) 在鉴权请求头中上报；
数据权限按 L1/L2/Push/Query/Replay/Factor/FunctionId 分位控制（PermissionCode），
服务端逐请求鉴权（"Your account is unauthorized to query this data type"）。

## 2. 客户端检测点（已确认的证据）

### 2.1 设备指纹（登录时采集）
- `AMAAuthReq.device_info[256]`：最多上报 256 条 `DeviceInfo{node_guid}` 记录
- `node_guid`(16字节) 的生成导入链：`IPHLPAPI.DLL!GetAdaptersInfo`
  → 基于**网卡 MAC 地址**构造机器指纹
- 同类机制通常还含主机名/磁盘序列号，但本 DLL 未导入相应 API（未发现
  GetVolumeInformation/GetComputerName/WMI），指纹面相对克制

### 2.2 连接期遥测（持续上报）
- `IndicatorCollect::SendConnectInfo`：**建立连接即主动上报客户端信息**
- `is_collection_indicator` 开关 + `ReportIndicator`/`ReportSubReq
  {data_type, instance_name, token, market_type}`：订阅行为、实例名、查询延迟
  （QueryDelayIndicator）均回传服务端
- 含义：服务端掌握每个会话的"客户端画像"；非官方实现的遥测字段缺失/格式异常
  是可识别特征

### 2.3 行为状态机强制
- "Send failed, the status is login"：未完成登录态禁止发送任何业务消息
- 心跳超时双向检测（kUMSHeartbeatTimeout），顶号时 force_logout 踢旧会话并留痕
- "have not receive logon in / logon timeout"：登录时序严格校验

### 2.4 传输层约束
- WSS + 自带 CA 证书(.ca.crt)：中间人抓包需先解 TLS（客户端可能还有证书固定，
  待动态验证）
- ZSTD 压缩帧由 comp_type/compress 标记，非压缩流量本身也是特征

## 3. 检测强度评级

| 维度 | 强度 | 说明 |
|---|---|---|
| 凭证伪造 | ★★★★☆ | 账密+token+白名单三重，无法绕过 |
| 设备指纹一致性 | ★★★☆☆ | node_guid 可仿造(MAC源)，但格式/数量分布可能与官方不同 |
| 遥测完整性 | ★★★★☆ | 缺失 SendConnectInfo/ReportSubReq 或内容异常易暴露 |
| 协议实现正确性 | ★★★☆☆ | 帧头/消息码已知，但 json schema/压缩/时序细节多 |
| 综合伪装难度 | **高** | 任一环节不一致都可能触发风控审计 |

## 4. 风险结论（决策依据）

1. **合规风险（最重）**：银河免责条款明确——账号仅限本人使用、多人/多端同时登录、
   非官方接入均可能导致**停止服务**。用逆向客户端直连生产属于"非官方接入"，
   一旦被风控标记，损失的是你的正式行情权限。
2. **技术风险**：遥测字段(node_guid 格式、indicator 内容)是黑盒，静态分析无法保证
   与官方二进制逐字节一致；首次连接即是一次"暴露测试"。
3. **收益对比**：你已有 Windows 桥接方案（方案A），官方二进制原生运行、零风险；
   macOS 直连的增量收益仅为摆脱一台 Windows 机，代价是正式账号风险。
4. **IP 白名单**：若营业部绑定了出口 IP，家庭宽带动态 IP 本身就会失败。

## 5. 可选路线（供决策）

| 方案 | 风险 | 工作量 | 说明 |
|---|---|---|---|
| A. Windows 桥接（现状最优） | 无 | 已完成 | 保持不变 |
| B. 沙箱环境协议验证 | 低 | 中 | 用**测试账号**在可控环境做 WSS 握手+AmdHeader 验证，不碰生产 VIP；先本地起 mock server 对拍官方 tgw_test 的线上字节流（Windows 机抓包 → Mac 重放对拍） |
| C. 重建客户端直连生产 | 高 | 大 | 需先完成 §5 动态验证清单全部条目，且接受 §4.1 风险 |
| D. 向银河申请 macOS 支持或开放接口 | 无 | 小 | 合规正道，机构用户通常有此通道 |

我的建议顺序：**D → A → B**；除非你有明确的测试账号授权边界，否则不建议 C。

## 6. 当前交付状态

- ✅ 协议结构/消息码/鉴权流程静态还原（docs/PROTOCOL_NOTES.md）
- ✅ C++ 骨架已具备 AmdHeader/消息码常量的填充位置（src_reconstructed/cpp）
- ⏸️ **按要求停止**：未发起任何到 101.230.159.234 / 140.206.44.234:8600 的应用层连接
  （此前 TCP 探测仅为端口可达性测试，无载荷）
- 等待你对上述 A/B/C/D 路线的决策
