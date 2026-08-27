# TGW 协议逆向工程 · 完整审计报告

> 审计对象：`reverse-macos/` 目录下全部工作产物
> 工作周期：2026-08-26（单日多轮会话）
> 目的：供第三方 agent 独立复核所有分析方法、证据链与结论
> 合规基线：用户自有授权账号 + 自有设备，用于 macOS arm64 互操作适配；用户已明确授权对生产服务执行有限的只读对照验证

> **2026-08-26 实测更正：** 早期 U1/U2 对“客户端请求体附加加密”的推断由错误的 WebSocket masking 解析导致，已被完整 TLS 明文对照推翻。当前 macOS arm64 实现已真实完成登录、订阅/取消订阅、ThirdInfo 交易日历和日 K 线查询。本更正优先于文档中任何早期阶段性推断。

---

## 0. 任务演进脉络

| 阶段 | 用户指令 | 交付 |
|---|---|---|
| P1 | 反汇编 whl/cpp 二进制 → 伪源码 → macOS arm64 编译 | wheel 解包/pyc 反编译/符号分析/arm64 dylib ✅ |
| P2 | 深度协议逆向（原估周~月级） | 发现 78MB PDB → 结构级还原 ✅ |
| P3 | 评估鉴权头与客户端检测，连接前停下 | 检测评估报告 ✅（未连接） |
| P4 | 逐函数反汇编补齐 | json schema/字节序/WSS 帧形态 ✅ |
| P5 | 经 SSH 在用户服务器抓官方客户端明文流量验证 | 握手→全会话逐级突破 ✅ |

## 1. 分析对象清单

| 文件 | 性质 | 来源 |
|---|---|---|
| tgw-1.0.9.2-py3-none-any.whl (70MB) | Python 绑定包（明文 .py + 各平台原生库） | 用户持有 |
| AmazingData-1.1.9-cp3xx.whl ×7 | 纯 Python 业务层（.pyc, Py3.12 magic `cb0d0d0a`） | 用户持有 |
| TGW-SDK_V1.0.8/Cplusplus | 官方公开头文件(2686行) + demo + Windows/Linux 库 | 用户持有 |
| tgw.dll (6.9MB PE32+x64) | Windows 协议核心 | SDK 附带 |
| **tgw.pdb (78MB)** | **厂商遗留完整调试符号库（未 strip）★决定性材料** | SDK 附带 |
| libtgw_python312.so (8.9MB ELF) | SWIG 扩展（静态链入 CPython+核心） | wheel 内 |

## 2. 工具链与方法（可复现）

| 工具 | 用途 | 关键命令 |
|---|---|---|
| unzip | wheel 解包 | `unzip -q xxx.whl -d dir` |
| pycdc (zrax/Decompyle++, 自编译) | Py3.12 字节码反编译 | 59/59 文件产出；3.12 新 opcode 有部分缺口 |
| nm/strings/file | 导出表/字符串静态特征 | `nm -D --defined-only` |
| llvm-pdbutil (brew llvm) | PDB 符号/类型流解析 | `dump -modules/-publics/-globals/-types` |
| radare2 (+idp 加载 PDB) | PE 反汇编/交叉引用 | `pdf`, `axt`, `/a bswap` |
| python3 脚本 | 类型流结构化提取 | 见 §4.2/§5 自写解析器 |
| LD_PRELOAD 钩子 (gcc on bj) | SSL_write/read 明文截获 | 见 §7 |
| tcpdump | 流量方向关联定位 | `-i any -w cap.pcap host <vip>` |
| cmake+clang (macOS) | arm64 编译验证 | `CMAKE_OSX_ARCHITECTURES=arm64` |

## 3. 架构结论（多源互证）

```
AmazingData(.pyc) ──► tgw 包装层(明文.py/SWIG代理) ──► 原生核心(SO/PYD)
                                                        │ galaxy::tgw / amd::* 命名空间
        互联网模式: WSS(websocketpp+asio_tls_client) ◄──┘
        托管模式: QTCP/TCP/RTCP(coloc_*模块, 本次未覆盖)
```
- SWIG 证据：tgw.py 头部注释、`SwigDirector_*` 符号族
- 静态链 CPython：so 内含全部 `Py*` 运行时符号（1667 个导出）
- 类结构恢复自 mangled symbols，与公开头文件一一对应

## 4. PDB 情报挖掘（P2 核心）

### 4.1 模块流 → 内部架构图
218 个 .obj 模块路径泄露完整工程结构：
`mdga(+impl/tools/utils)` · `session` · `wss_client/wss_connect_conn(+manager)` ·
`net/{tcp_client,tcp_session,hive}` · `modules/tcp_query/*` · `modules/history_replay/*` ·
`internet_{query,push,factor,thirdinfo}_*` · `coloc_*` · `rqa/rqs` ·
`ums/ama_client(+impl2)` · `aes/derived_data_client` · `indicator_collect` ·
`check_permission` · `update_pw_manager`
构建环境：MSVC 14.0 x64 release + debug-symbols-on，boost 1.62，websocketpp，ZSTD 1.3.4

### 4.2 类型流（938,011 行）
- 自写解析器提取 28 个 amd:: 枚举全量值 → `analysis/native_win/pdb_enums_amd.txt`
- 关键线格式结构体字段级还原 → `analysis/native_win/pdb_structs_key.txt`

### 4.3 决定性结构体（字节偏移实测自 LF_MEMBER）

```c
// amd::protocol::AmdHeader —— sizeof=32, pack(1) 紧凑
uint8  major@0; uint8 minor@1; uint16 module_index@2; int8 sample_flag@4;
uint8 relay_type@5; protocol_type@6; comp_type@7; compress@8; app_type@9;
uint64 msg_key@10; uint64 timepoint@18; uint32 package_size@26; uint16 data_offset@30;

// amd::protocol::ums::AMAAuthReq / Rsp
Req{mode u32@0, heartbeat_ms u32@4, username str@8, password str@72,
    device_cnt u8@200, DeviceInfo[256]@201, json_str str@8393}
DeviceInfo{char node_guid[16]}
Rsp{err_code u32@0, err_msg char[256]@4, token str@260, json_str}
```

### 4.4 控制消息码与数据类型码
控制：kLogonRequest=665 kHeartbeat=666 kLogonOut=667 kLogonAck=668 kOverLoad=669
kCancelTaskReq/Ack=700/701
数据（amd::modules::query::ReqDataType）：kSnapshot=100 kTickOrder=107 kTickExecution=108
kOrderQueue=109 K线族 10000~10014 衍生 10100~10103 kCodeTable=10200 kStockInfo=10203
kExFactorTable=10204 kFactor=10206 kThirdInfo=10210 HKEx 10211/10212 等（全表见枚举文件）

## 5. 逐函数反汇编结论（P4）

| 函数 | 地址(VA) | 结论 |
|---|---|---|
| Tools::SnapshotL1ToJson | 0x180113dc0 | 响应 JSON 全键名（见 §6.2） |
| Tools::*ToJson 家族 30+ | 导出表 | 每种行情结构一个序列化器 |
| fcn.180222680 / 18027c2a0 | GetAdaptersInfo 双调用 | node_guid 生成点 |
| MsgHeaderChecker::{CheckAmd,Msg}Header | globals | 帧校验器 kLenNotEnouth 等 |
| IGMDApi::SetThirdInfoParam | 0x1800dea30 | →map 存储→发送时序列化 |
| IMDGAapi::QueryThirdInfo 分发 | 0x180239fb0 族 | 四子类入口 |

**node_guid 算法**：GetAdaptersInfo×2（尺寸探测+HeapAlloc 重取）→
`sprintf("%02x:%02x:%02x:%02x:%02x:%02x", MAC[0..5])` 冒号分隔小写 hex → 截入 16B。
证据：格式串 @0x1804f5f28、长度校验 `cmp X,0x10`。

**字节序判定 = 小端**：① 全镜像 bswap 扫描 6 处命中均为 call 指令编码假阳性；
② pack(1) 结构 MSVC 直接内存布局；③ websocketpp 载荷透传。
**WSS 封装（实测更正）**：互联网模式的 binary frame 承载 JSON，服务端推送/查询响应可为 `0x59 + ZSTD frame + JSON`。`AmdHeader` 属于其他原生通道/内部数据帧，不是本次 internet WSS 请求信封。

**JSON 库 = rapidjson**（GenericDocument<UTF8<>> 符号）；响应侧 SnapshotL1 键全集：
security_code/market_type/variety_category/orig_time/last_price/open_price/high_price/
low_price/close_price/pre_close_price/bid_price[10]/offer_price[10]/bid_volume[10]/
offer_volume[10]/total_volume_trade/total_value_trade/num_trades/trading_phase_code/
high_limited/low_limited/IOPV

## 6. 客户端检测评估（P3，docs/CLIENT_DETECTION_ASSESSMENT.md）

三层检测确认：凭证层（账密+token+IP白名单）/ 指纹层（node_guid←MAC，
device_info[256]）/ 行为遥测层（IndicatorCollect::SendConnectInfo 连接即上报、
ReportSubReq 订阅上报、QueryDelayIndicator）。伪装难度评级"高"，
直连生产存在账号停服合规风险——该结论不受后续技术进展影响。

## 7. 实机验证（P5，bj = 用户 Ubuntu 服务器 49.233.83.64）

部署：galaxy-relay 服务(systemd, venv+uvicorn) 驱动官方 tgw wheel；
`libtgw.so` 动态链接捆绑 libssl.so.10 → LD_PRELOAD 可行。

### 7.1 捕获管线（三轮迭代）
1. `/tmp/hook.so` 基础版 → systemd 沙箱拒载 → `PrivateTmp=no` 解决
2. 多进程 wb 截断 → 改 PID 后缀追加模式
3. 双 OpenSSL 符号错位（只截到握手）→ **dlopen 直呼 libssl.so.10 实现** → 全量明文 ✅
最终钩子协议：`方向字符(W/R) + len(u32 LE) + payload`

### 7.2 线上实证（官方 Linux SDK + macOS arm64 重建客户端）

| 观测 | 内容 | 对应还原 |
|---|---|---|
| WSS upgrade | `/amd/dgw/push`，RFC 6455 binary frame，客户端标准 masking | 传输层 ✅ |
| Login | `ReqLogon` → `OnRspLogon`，`status=0`，token 由服务端签发 | 真实鉴权 ✅ |
| Subscription | `ReqSubscribeBatch` / `ReqUnSubscribeBatch`，请求 ID 从 1000000 起；公开 `kSnapshot=10` 转 wire flag/tag=14 | 订阅链路 ✅ |
| Heartbeat/push | PING/PONG `Heartbeat`；推送样本解码为 `0x59 + ZSTD + JSON` | 心跳/推送 ✅ |
| 159518 L1 | Linux 官方 SDK 30s=10 条；Mac arm64 60s=19 条，tag=14，2 全量+17 增量，中位间隔 2.981s | 持续推送 ✅ |
| ThirdInfo | 独立 `/amd/dgw/dgw{1,2}_query`，`ReqGetThirdInfo`，tag=11101，18 行交易日历 | Linux/Mac 对照 ✅ |
| Kline | `ReqGetKline`，公开日线枚举 10008 转 wire `period_type=10100`，tag=10100，1 行/11 字段 | Linux/Mac 对照 ✅ |
| Query lifecycle | `ReqGetComplete` 后等待服务端 WebSocket Close；典型 close reason=`accept conn active close` | 一次性查询连接 ✅ |

tcpdump 关联：单条 TCP 承载全部流量（Out 134B 登录帧/In 266B 应答可见于密文层），
排除独立数据通道假设。

## 8. 当前未决项（审计重点核查区）

| # | 事项 | 当前状态 | 证据位置 |
|---|---|---|---|
| U1 | 非日线 K 线周期 | 日线已验证；分钟/周/月/季/年的公开枚举到 wire period_type 映射尚未逐一对照 | official Kline oracle |
| U2 | QuerySnapshot | 公开结构 ABI 已还原，internet envelope/响应 CSV 尚未闭环 | ReqDefault/Tools::*ToJson |
| U3 | 推送数字键映射 | L1 全量/增量 JSON 已持续接收，但数字键到公开结构字段的完整语义化和增量状态合并尚未完成 | 159518 L1 samples / public structs |
| U4 | 查询流控/重试 | 已正确处理服务端 Close；尚未建立依据官方配额的速率限制和幂等重试 | close reason / login quota |
| U5 | coloc QTCP/RTCP | 用户声明不需要，未覆盖 | — |
| U6 | 服务端风控阈值 | 黑盒，无法静态获知 | — |

## 9. 产物清单（审计索引）

```
reverse-macos/
├─ AUDIT_REPORT.md                    ← 本文件
├─ docs/
│  ├─ REVERSE_NOTES.md                P1-P2 静态分析总笔记
│  ├─ PROTOCOL_NOTES.md               §1-16 协议还原全记录（含完整性矩阵演进）
│  └─ CLIENT_DETECTION_ASSESSMENT.md  检测评估+路线决策依据
├─ analysis/
│  ├─ tgw_whl/ amazingdata_whl/       wheel 原样解包
│  ├─ decompiled/                     pycdc 输出 59 文件
│  ├─ native_win/                     tgw.dll/pdb 及全部提取物：
│  │   pdb_modules.txt(模块流) pdb_publics.txt(22503符号) pdb_globals.txt
│  │   pdb_types.txt(93.8万行) pdb_enums_amd.txt(28枚举) pdb_structs_key.txt
│  │   tgw_strings.txt all_syms.txt
│  ├─ mdga_plain*.bin                 握手期样本(207B×3)
│  └─ full_session.bin                ★完整明文会话 7629B（核心证据）
├─ src_reconstructed/
│  ├─ python/tgw_macos/ amazingdata_re/   可运行行为级重建
│  └─ cpp/include/{tgw_core_api,tgw_protocol_consts}.hpp + src/tgw_core.cpp
├─ macos_build/                       arm64 产物(libtgw_core.dylib/tgw_demo)+CMake
├─ config/galaxy_account.ini          测试凭据(chmod600, 勿外传)
└─ test_live_login.py                 本地冒烟(不触生产)
```

## 10. 复核建议路径

1. 抽查 §4.3 结构体：在 pdb_types.txt 中 grep `AMAAuthReq`/`AmdHeader` 对照偏移
2. 抽查 §5 node_guid：r2 -c "s 0x180222680;pd 60" native_win/tgw.dll 查看格式串引用
3. 核验 §7.2：本地运行解析脚本重放 full_session.bin（解析器逻辑见会话记录）
4. 运行 `tests/test_native_protocol.py` 核验 ABI、WebSocket、登录/订阅/ThirdInfo/Kline 信封与响应防御性解析
5. 仅在授权环境中运行 `tools/live_smoke.py --calendar --kline 510300 --market 101`；工具只输出行数/列名
