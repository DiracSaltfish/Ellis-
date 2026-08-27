# QuerySecuritiesInfo 单市场单代码（SSE `510300`）对齐证据

- Scope: 仅 `QuerySecuritiesInfo` 互联网模式（kInternetMode）的**单市场、单代码**最小
  只读子范围：`SubCodeTableItem{market=101(SSE), security_code="510300"}`、同步调用。
  禁止全市场（market=kNone）大结果查询；多 item 列表、SZSE（102）/NEEQ 样本、异步
  query_spi、空结果/错误码分支、多响应帧分页均不在本范围，代码显式失败或保持
  NotImplementedError。输出 `MDCodeTableRecord` 大结构，回调 `OnMDSecuritiesInfo`。
- PDF: C++ 手册 PDF 页 36–37（正文 28–29）`7) QuerySecuritiesInfo` 方法与
  `SubCodeTableItem`（int32 market + char[32] security_code，市场 kNone 表示全市场、
  空代码表示全代码——两者均不在本范围）；回调 `IGMDSecuritiesInfoSpi::OnMDSecuritiesInfo`
  PDF 页 43（正文 35）；输出结构 `MDCodeTableRecord` PDF 页 82–83（正文 74–75，43 字段
  完整清单含涨跌停/单位/期权属性等）。PDF 无模式边界矛盾：方法页与回调均标双模式。
- Header delta: V1.0.8 linux==windows 逐字节一致，本接口无发行包差异。
  `MDCodeTableRecord`（tgw_struct.h:895-943）43 字段与 PDF 一致；本地 pack(1)
  `sizeof=555`、offset 已用 ctypes 测试锁定（security_code=0 / market_type=32 /
  symbol=33 / english_name=161 / security_type=225 / currency=241 /
  variety_category=249 / pre_close_price=250 / security_status=318 /
  regular_share=474 / product_code=499 / position_type=551）。字符数组长度取自
  ConstField：kFutureSecurityCodeLen=32、kSymbolLen=128、kSecurityAbbreviationLen=64、
  kMaxTypesLen=16、kTypesLen=8、kSecurityCodeLen=16、kCodeTableSecurityStatusMaxLen=16、
  RegularShare=9。`SubCodeTableItem.market` 为 **signed int32**（区别于
  SubscribeItem.market 的 uint8），pack(1) 本地 `sizeof=36`、offset market=0/
  security_code=4，与既有 ETF 证据一致。
- Linux oracle: 2026-08-26 在 bj 用 Linux x86_64 官方 SDK（tgw 1.0.9.2 wheel，
  `/opt/galaxy-relay/venv/bin/python`，galaxyrelay 用户，凭据仅读远端受保护配置）执行。
  执行前后 `galaxy-relay` 均为 inactive。会话内合并：①同步最小查询
  `tgw.QuerySecuritiesInfo(item, return_df_format=False)`；②冷却 5 秒后同参异步收集器
  版本（自定义用户 SPI 经官方 wrapper 统计逐批交付）。脱敏结果：登录 true；同步
  `query_error` 为整数 **0**；返回 list 长度 **1**，元素为 dict，恰 **43 键**（键集与
  `MDCodeTableRecord` 固定字段一一对应），值类型仅 int/str（char 数组→str，int64/
  uint8/uint32→int）。不变量：`distinct_market_type=[101]`、
  `distinct_variety_category=[2]`、date_digit_lengths=[1,8]（list_day 8 位、expire_date
  对非期权样本为 0）、currency 长度为 3、option 专属字段（underlying_security_id/
  contract_type/product_code/regular_share/english_name）非空比例 0。异步收集器：
  **1 个数据批次、1 条记录、无 status 错误**、submit_return=(True,None)——本样本无
  多批/竞态。未记录任何业务原值。
- Wire: 对同会话做 SSL_write/SSL_read 脱敏取证（原始捕获在本地系统临时目录分析后删除）。
  关键结论（全部实捕，非 strings/外推）：
  - **通道**：查询走**常驻 push 连接** `/amd/dgw/push`（与 ETF/订阅同连接），**不建立**
    dgw1/dgw2_query 一次性端点；
  - request method = **`ReqGetCodeTableList`**（推翻静态候选 `ReqGetSecuritiesInfo`；
    实测与 ETF 的 `ReqGetETFCodeTableList` 不同名）；headers key 顺序为
    **`id, userName, token`**（id 在前）；params 仅一个键 **`Security`**（字符串类型），
    值为 **`"510300|101"`**（代码|市场，竖线分隔）；
  - 完成消息 = 专用 **`ReqGetCodelistComplete`**（headers 同序，**无 params 键**），通用
    `ReqGetComplete` 未出现；请求 id 从 **1** 起独立递增（sync id=1、async id=2，均被
    响应回显）；
  - response：`status=0`、headers.tag 为**字符串 `"109"`**、headers 含 **`code_num`**
    （=1），**无 pack_num/all_pack_num 分页控制**、顶层键 `data/headers/status`；每帧
    `data` 为对象数组（本样本 1 条），记录为数字字符串键 `"1".."43"` 的 JSON 对象；
  - 记录槽位与 `MDCodeTableRecord` 字段顺序一一对应（1–43 固定字段）；字符数组字段以
    JSON **字符串**上线（区别于 ETF 的单字符 ASCII 码——本结构无单 char 字段），数值字段
    以 JSON **int** 上线；数值不做缩放（与官方 wrapper 一致）；
  - 响应帧带 ZSTD 标记（**`0x59+ZSTD`**），push 连接保持打开，未观测 close 帧；官方
    客户端在收到响应后发送 complete（W 流顺序：request→complete 相邻，两次查询间隔 ≥5s）。
- Arm:
  - `_structures.py` 新增 `MDCodeTableRecord`（pack(1) ctypes 镜像，sizeof=555 + offset 测试）；
  - `_protocol.py` 新增 `SECINFO_WIRE_TAG="109"`、`VERIFIED_SECINFO_MARKETS={101}`、
    `build_secinfo_request`（单 item、market 白名单、代码 ≤32 字节、`code|market` 值、
    id-first headers）、`SECINFO_RECORD_FIELDS`（43 槽位表）、`decode_secinfo_record`
    （精确 43 槽位集校验、int≠bool、char→str、越界槽拒绝）、`parse_secinfo_packets`
    （status/tag/id 回显/data 容器校验后展开）；
  - `_backend.py` 新增 `_query_securities_info`：在**常驻 push 连接**上 `request_many`
    等首个响应帧 → 发送 `ReqGetCodelistComplete` → 解析；不关闭 push 连接，不触碰
    dgw_query 端点池；
  - `interface.py` 新增公开 `QuerySecuritiesInfo(req_securities_info_cfg,
    query_spi=None, return_df_format=True)`：单 item 合约、`query_spi` 显式
    NotImplementedError、df 格式需 pandas，返回 `(list[dict] | DataFrame, err)`；
  - `__init__.py` 导出 `MDCodeTableRecord`/`QuerySecuritiesInfo`；
    `tools/live_smoke.py` 新增 `--securities-info CODE` 脱敏 shape 路径；
  - 工具侧按工作流 §4.3 在 `tools/oracle/remote_sdk_oracle.py` 新增独立 `--kind
    securities-info`（含异步逐批收集器与 43 键容器脱敏摘要）。
- Tests: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v` 共
  **101 项全部通过（1 skip pandas 缺失分支）**（原 77 + 新增 24：结构 sizeof=555/
  offset/默认值；builder key 顺序/`Security` 值格式/多 item/未证市场/空·超长代码拒绝；
  43 槽位解码类型；int-as-bool/str-as-int/缺槽·多槽/非对象错型；单包解析/id 回显不匹配/
  错 tag/错 status/data 容器错型/空响应/多帧拼接；公开合约同步元组、异步 SPI 显式失败、
  未知 kind 显式失败、re-export）。
  `PYTHONDONTWRITEBYTECODE=1 python3 -m compileall -q src/python examples tools` 通过。
  fixture 全部合成值。
- Live diff: 2026-08-26 Mac live 用第二授权账号（stdin 注入，未持久化，未打印值）一次
  低频同参请求：登录与鉴权成功，`securities_info_query_error=0`、返回 1 条、43 列、
  每列类型 str/int 与 Linux oracle 逐项一致、`distinct_market_types=[101]`、
  `distinct_variety_categories=[2]`。命令类别（凭据未入命令/仓库）：
  ```bash
  cd "/Users/ellis/工具程序开发/database for armmac"
  export TGW_MAC_USERNAME_OVERRIDE="$(cat <acct2>)"
  printf '%s\n' "$TGW_MAC_USERNAME_OVERRIDE" | \
    python3 tools/live_smoke.py \
      --config "/Users/ellis/工具程序开发/数据库桥接/reverse-macos/config/galaxy_account.ini" \
      --username-stdin --securities-info 510300 --market 101
  ```
- Cleanup: bj 上 `/tmp/tgw_secinfo_oracle/`（oracle 副本、interpose.so、capture.bin、
  分析脚本）与 `/tmp/tgw_secinfo_oracle.py`、`/tmp/secinfo_capture.bin`、
  `/tmp/ssl_write_interpose.c`、`/tmp/analyze_ssl_write_capture.py` 已全部删除并复核
  不存在；原始捕获仅在本地系统临时目录短暂存在，分析后已删除；
  `galaxy-relay` 任务前后均为 inactive（两次 `systemctl is-active` 确认）。仓库扫描无
  账号、密码、token、MAC、原始行情或捕获文件。
- Proposed status: **`LIVE_ALIGNED(SSE single code only)`**。
  依据：Linux oracle（错误码 0、1 记录、43 键形状/类型、异步单批）+ wire 全要素实捕
  （通道 push / method `ReqGetCodeTableList` / headers 顺序 / `Security` 值 / tag "109" /
  code_num / 无分页 / `ReqGetCodelistComplete` / 0x59+ZSTD / char→str·int→int）+ Mac 实现、
  合成测试与独立授权账号 Mac 同参 live 成功（错误码 0、1 记录、43 列、类型/不变量一致）。
  这个状态只覆盖 SSE `510300`、单 item、同步返回；不外推到下列开放风险。
- Open risks:
  1. SZSE（102）与 NEEQ（2）未取样：builder 显式拒绝，需单独一轮取证；
  2. 多 item 列表请求的 wire 形状未知：接口层显式 NotImplementedError；
  3. 多响应帧/分页语义未观测（该通道无 pack_num 控制，code_num 与 data 长度关系仅见
     code_num=1）：backend 只接受单帧应答；若服务端对大证券拆帧，将触发超时而非静默截断；
  4. 空结果（kDataEmpty=-76 族）与非零 status 的 wire 形状未取证：parser 对非零 status
     一律报错；
  5. 数值缩放仍由调用方按头文件注释处理（价格÷1e6、数量÷100 等），oracle 仅做了符号/
     位数不变量复核；
  6. 期权/期货期权专属字段（contract_type/exercise_price/expire_date/product_code/
     delivery_*/create_date/position_type）在本 SSE 股票样本均为空/0，其非空 wire 形状
     未实证；
  7. 流控：push 连接查询与订阅/ETF 共享生命周期与带宽；密集调用行为未建模；
  8. AmazingData 高层通道底层与本接口是否等同未证明，边界维持独立取证。
