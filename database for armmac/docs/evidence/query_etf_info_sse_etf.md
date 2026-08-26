# QueryETFInfo 单 ETF（SSE `510300`）对齐证据

- Scope: 互联网模式、原生 TGW `QueryETFInfo` 的**单市场单代码**子范围：
  `SubCodeTableItem{market=101(SSE), security_code="510300"}`、同步调用、官方 json 返回格式
  （`return_df_format=False`）。异步 SPI、多 item 列表、SZSE（102）样本、空结果/错误码分支、
  多响应帧分页均不在本范围，代码显式失败或保持 NotImplementedError。静态契约见
  [query_etf_info_static.md](query_etf_info_static.md)，本文为其在线闭环续篇。

- PDF: C++ 手册 PDF 页 39（正文 31）§3.5.2 原型与 `SubCodeTableItem`；页 44（正文 36）
  §3.5.6 `IGMDETFInfoSpi`；页 50/53（正文 42/45）demo 与生命周期；页 60（正文 52）CLI 示例
  `QueryETFInfo SSE 510050`（确认单市场+单代码为官方最小样本形态）；页 85–87（正文 77–79）
  §5.32/§5.33 输出结构。本轮未发现与静态证据冲突的新 PDF 事实。

- Header delta: V1.0.8 头文件无新增差异。`SubCodeTableItem.market` 为 **signed int32**（区别于
  `SubscribeItem.market` 的 uint8），pack(1) 本地 `sizeof=36`、offset market=0/security_code=4，
  已加测试锁定。`MDETFCodeTableRecord` 含 `std::vector` 非 POD，维持不镜像，Mac 按 wire 两级
  JSON 形状解析。

- Linux oracle: 2026-08-26 在 bj 用 Linux x86_64 官方 SDK（tgw 1.0.9.2 wheel，
  `/opt/galaxy-relay/venv/bin/python`，galaxyrelay 用户，凭据仅读远端受保护配置）执行。
  执行前后 `galaxy-relay` 均为 inactive。会话内合并完成：①同步最小查询
  `tgw.QueryETFInfo(item, return_df_format=False)`；②冷却 5 秒后同参异步收集器版本
  （自定义用户 SPI 经官方 wrapper 统计逐批交付）。第一次运行因执行方摘要函数误设容器形状
  （把官方 json 行当作 dict 而实为 `(basic_dict, constituent_list)` 元组）导致脱敏摘要缺失，
  按纪律做了一次完全同参受控重跑；两次登录/错误码/批数一致。脱敏结果：登录 true；同步
  `query_error` 为整数 **0**；返回 list 长度 **1**，元素为二元组；`basic_info` 恰 **35 键**
  （键集与 `MDETFCodeTableRecord` 固定字段一一对应），值类型仅 int/str（char 单字符→str，
  char 数组→str，int64/uint8→int）；成分股恰 **300 条 × 13 键**（与 `ConstituentStockInfo`
  一一对应）。不变量：`market_type=[101]`、trading_day/pre_trading_day 均 8 位整数、
  publish/creation/redemption 非空取值 ⊆ {'Y'}、creation_redemption_unit>0 存在、nav≥0 存在、
  security_code 无重复。异步收集器：**1 个数据批次、1 条记录、无 status 错误**——本样本不存在
  同步 wrapper 多批覆盖竞态（静态证据登记的风险在本样本未被触发）。未记录任何业务原值。

- Wire: 对同一会话做 SSL_write/SSL_read 脱敏取证（原始捕获在本地系统临时目录分析后删除；
  分析器输出经核查不含凭据/业务值）。关键结论（全部实捕，非 strings 推断）：
  - **通道**：ETF 查询走**常驻 push 连接** `/amd/dgw/push`（与订阅同连接），**不建立**
    dgw1/dgw2_query 一次性端点——静态证据的 codelist 通道假设成立；
  - request method = **`ReqGetETFCodeTableList`**；headers key 顺序为 **`id, userName, token`**
    （id 在前，与 dgw\*_query 系 builder 的 `userName,token,id` 不同）；params 仅一个键
    **`Security`**（字符串类型），值为 **`"510300|101"`**（代码|市场，竖线分隔）；
  - 完成消息 = 专用 **`ReqGetCodelistComplete`**（headers 同序，**无 params 键**），通用
    `ReqGetComplete` 未出现；请求 id 从 **1** 起独立递增（id=1、id=2 两次查询均被响应回显）；
  - response：`status=0`、headers.tag 为**字符串 `"111"`**（非整数）、**无 pack_num/
    all_pack_num 分页控制**、顶层键 `data/headers/status`；每帧 `data` 为对象数组（本样本
    1 条），记录为数字字符串键 `"1".."36"` 的 JSON 对象；
  - 记录槽位与结构体字段顺序一一对应（1–35 固定字段 + 36 成分股数组；成分股条目键
    `"1".."13"`）；**单字符字段以 ASCII 整数上线**（实测 publish=89('Y')、switch=49('1')、
    creation/redemption/all_cash_flag/rtgs/buy_or_sell=0(NUL→'')、substitute_flag∈{49,50}）、
    字符数组为 str、整数字段为 int；数值不做缩放（与官方 wrapper 一致，缩放留待调用方按头文件注释处理）；
  - 响应帧带 ZSTD 标记（`0x59+ZSTD`），push 连接保持打开，未观测 close 帧；官方客户端在收到
    响应后发送 complete（W 流顺序：request→complete 相邻，两次查询间隔 ≥5s）。

- Arm: 
  - `_structures.py` 新增 `SubCodeTableItem`（c_int32 + char[32]，pack(1)=36）；
  - `_protocol.py` 新增 `ETF_WIRE_TAG="111"`、`VERIFIED_ETF_INFO_MARKETS={101}`、
    `build_etf_info_request`（单 item、market 白名单、代码 ≤32 字节、`code|market` 值、
    id-first headers）、`build_etf_codelist_complete_request`（无 params）、
    `ETF_RECORD_FIELDS`/`ETF_CONSTITUENT_FIELDS` 槽位表、`decode_etf_record`（精确槽位集校验、
    ASCII 码→chr/NUL→''、bool≠int、越界拒绝）、`parse_etf_info_packets`（status/tag/id 回显/
    data 容器校验后两级展开，输出对齐官方 json 容器 `[(basic_dict,[cons...]),...]`）；
  - `_backend.py` 新增 `_query_etf_info`：在**常驻 push 连接**上 `request_many` 等首个响应帧 →
    发送 `ReqGetCodelistComplete` → 解析；不关闭 push 连接，不触碰 dgw_query 端点池；
  - `interface.py` 新增公开 `QueryETFInfo(req_etf_info_cfg, query_spi=None,
    return_df_format=True)`：单 item 合约、`query_spi` 显式 NotImplementedError、
    df 格式需 pandas，并输出 `[(DataFrame([basic]), DataFrame(cons)), ...]`，基础信息固定为一行；
  - `__init__.py` 导出 `SubCodeTableItem`/`QueryETFInfo`；`tools/live_smoke.py` 新增
    `--etf-info CODE` 脱敏 shape 路径；
  - 工具侧按工作流 §4.3 在 `tools/oracle/remote_sdk_oracle.py` 新增独立 `--kind etf-info`
    （含异步逐批收集器与元组容器的脱敏摘要）。
  - **离线等价验证**：把实捕的两帧 tag-111 ZSTD 原始 payload 直接喂给 Mac
    `decode_server_payload`+`parse_etf_info_packets`，两帧均解析成功且形状/枚举与 Linux
    官方容器一致（35 键、300×13、publish='Y'、substitute_flag∈{'1','2'}、成分股 market_type
    ⊆{101,102}）。

- Tests: `python3 -m unittest discover -s tests -v` 共 **50 项全部通过**（原 27 + 新增 23：
  结构 sizeof/offset/符号性/默认值；envelope key 顺序与 `Security` 值格式；多 item/未证市场/
  空·超长代码拒绝；complete 无 params；单包解码与命名形状；ASCII char 转换含 NUL；空 data；
  双帧拼接；错 tag（含整数 111）/错 status/缺槽·多槽/int·str·char·bool 错型/越界码/容器错型/
  id 不匹配/成分股错形；公开合约同步元组、异步 SPI 显式失败、df 格式 pandas 缺失报错及
  基础信息一行 DataFrame 形状）。
  `python3 -m compileall -q src/python examples tools` 通过。fixture 全部合成值。

- Live diff: 2026-08-26 主验收者使用独立授权账号在 Mac 发起一次低频同参请求：登录与鉴权
  均成功，`etf_query_error=0`、返回 1 条 ETF 记录、基础信息 35 列、成分股 300 条 × 13 列，
  与 Linux oracle 的脱敏行数、列集合和类型逐项一致。验收命令类别如下（凭据未入命令/仓库）：
  ```bash
  cd "/Users/ellis/工具程序开发/database for armmac"
  python tools/live_smoke.py --etf-info 510300 --market 101
  # 或双账号同参验收时：
  # python tools/live_smoke.py --etf-info 510300 --market 101 --username-stdin
  ```
  已观测输出 `etf_query_error=0 records=1 basic_columns=<35 键> constituent_counts=[300]
  constituent_columns=<13 键>`；未保存业务原值或完整响应。

- Cleanup: bj 上 `/tmp/tgw_etf_oracle/`（oracle 副本、interpose.so、capture.bin/capture_copy.bin、
  分析脚本）已全部删除并复核不存在；原始捕获仅在本地系统临时目录短暂存在，分析后已删除；
  `galaxy-relay` 任务前后均为 inactive（两次 `systemctl is-active` 确认）。仓库内无账号、密码、
  token、MAC、原始行情或捕获文件（注：一次调试输出曾在终端打印过 logon 帧，内容未落盘任何文件，
  且该会话 token 已随会话结束失效）。

- Accepted status: `LIVE_ALIGNED(single SSE ETF only)`。
  依据：Linux oracle（错误码 0、1 记录、35+300×13 形状、异步单批）+ wire 全要素实捕
  （通道/method/keys/tag/分页缺失/completion/ZSTD/ASCII-char 编码）+ Mac 实现、合成测试与
  真实帧离线重放等价验证，以及主验收者独立授权账号的 Mac 同参 live 成功。这个状态只覆盖
  SSE `510300`、单 item、同步返回；不外推到下列开放风险。

- Open risks:
  1. SZSE（102）ETF 查询未取样：builder 显式拒绝，需单独一轮取证；
  2. 多 item 列表请求的 wire 形状未知（官方 wrapper 支持 cnt>1）：接口层显式 NotImplementedError；
  3. 多响应帧/分页语义未观测（该通道无 pack_num 控制）：backend 只接受单帧应答；若服务端未来
     对大 ETF 拆帧，将触发超时而非静默截断；
  4. 空结果（kDataEmpty=-76 族）与非零 status 的 wire 形状未取证：parser 对非零 status 一律报错；
  5. `creation_redemption_switch` 等枚举字符只观测到 {'1','0'(NUL)} 子集；其它取值按同一
     ASCII 规则转换，但未逐一实证；
  6. 数值缩放仍由调用方按头文件注释处理（÷100/1e5/1e6），oracle 仅做了符号/位数不变量复核；
  7. 流控字符串提示存在最小查询间隔与服务预热窗口：本次两次查询间隔 5s 安全通过，密集调用
     行为未建模；
  8. AmazingData 高层 `get_etf_pcf` 底层通道仍未证明与本接口等同，边界维持独立取证；
  9. push 连接复用使 ETF 查询与订阅共享生命周期：push reader 断开时 ETF 查询随之失败，
     自动重连仍未实现。
