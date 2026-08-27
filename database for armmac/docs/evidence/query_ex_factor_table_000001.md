# QueryExFactorTable 单代码 `000001` 对齐证据

- Scope: 仅 `QueryExFactorTable` 互联网模式（kInternetMode）**单代码**最小只读子范围：
  `code="000001"`（市场随代码默认路由，官方 Python wrapper 无显式市场参数，wire 亦无
  市场字段）、同步调用返回 `(rows, 0)`。禁止批量、多市场、异步 query_spi、分页扩张——
  代码显式失败或 NotImplementedError。输出 `MDExFactorTable` 5 字段行，回调
  `OnMDExFactor`（+ 官方同步 wrapper 内部 `OnStatus` 经 `TmpQueryExFactorWaitSpi`）。
  **double 精度/缩放（头文件注 N38(15)）是验收重点**，以下 Linux oracle 定形后实现。
- PDF: C++ 手册 PDF 页 36–37（正文 28–29）`8) QueryExFactorTable` 方法：
  `static int32_t QueryExFactorTable(const IGMDExFactorSpi* ex_factor_spi,
  const char* code)`，参数仅 `code(in)`（示例 `000001`），模式标“托管机房和互联网模式
  适用”；回调 `IGMDExFactorSpi::OnMDExFactor` PDF 页 43（正文 35）；输出结构
  `MDExFactorTable` PDF 页 81（正文 73）：`inner_code[16]`、`security_code[16]`、
  `ex_date uint32_t`（yyyyMMdd）、`ex_factor double`、`cum_factor double`。PDF 无模式
  边界矛盾；无 `level_type` 类 HDR-only 差异。
- Header delta: V1.0.8 linux 头文件 `tgw_struct.h:855-862` `MDExFactorTable` 与 PDF
  字段集一致，无发行包差异；`inner_code`/`security_code` 均取 `ConstField.kSecurityCodeLen
  =16`。`tgw.h:227` 方法签名与 PDF 一致；`tgw_history_spi.h:401-426`
  `IGMDExFactorSpi` 含 `OnMDExFactor(MDExFactorTable*, uint32_t cnt)` 与
  `OnStatus(RspQueryStatus*)`（PDF 3.5.6 未记载 OnStatus，属 E-3 既有登记面）。本地
  pack(1) ctypes 镜像 `sizeof=52`（16+16+4+8+8），offset：inner_code=0 /
  security_code=16 / ex_date=32 / ex_factor=36 / cum_factor=44。
- Linux oracle: 2026-08-26 在 bj 用 Linux x86_64 官方 SDK（tgw 1.0.9.2 wheel，
  `/opt/galaxy-relay/venv/bin/python`，galaxyrelay 用户，凭据仅读远端受保护配置）
  执行；前后 `galaxy-relay` 均 inactive。会话内合并：①同步最小查询
  `tgw.QueryExFactorTable("000001", return_df_format=False)`；②冷却 5 秒后同参异步
  收集器（自定义用户 SPI 经官方 wrapper 统计逐批交付）。脱敏结果：登录 true；同步
  `query_error` 为整数 **0**；返回 list 长度 **33**；元素为 dict，恰 **5 键**：
  `inner_code/security_code`（str）、`ex_date`（int）、`ex_factor`/`cum_factor`
  （**Python float**，非 Decimal/str）；列键顺序 `inner_code, security_code, ex_date,
  ex_factor, cum_factor`（与结构一致）。不变量：`ex_date_digit_length=[8]`（全部 int）、
  `ex_factor` 非负、全部 float、`ex_factor_decimal_places=[0,6,13,14]`；
  `cum_factor` 非负、全部 float、`cum_factor_decimal_places=[0,5,6]`、
  `cum_factor_positive_count=33/33`、`cum_factor_row_count=33`；
  **`cum_factor_monotonic_nondecreasing=false`、`cum_factor_monotonic_violations=2`**
  （服务端数据本身有两处累计因子回落，非解析错误）；`inner_code_len=[9]`、
  `security_code_len=[6]`、各自 distinct=1。异步收集器：**1 个数据批次、33 条记录、
  无 status 错误**、submit_return=(True,None)。double 端到端精度：官方 float 经
  `format(v,".18f")` 往返与自身相等 33/33（wire 双字段固定 18 位小数，C++ double →
  JSON → Python float 无精度损失）。未记录任何业务原值。
- Wire: 对同会话做 SSL_write/SSL_read 脱敏取证（原始捕获仅存本地系统临时目录，分析后
  已删除）。关键结论（全部实捕，非 strings/外推）：
  - **通道**：查询走 **one-shot** `/amd/dgw/dgw1_query` 端点（非常驻 push 通道），
    登录仍走 `/amd/dgw/push`；不建立额外 dgw2_query（id=1、id=2 共用 dgw1_query）；
  - 请求 method = **`ReqGetExFactor`**（推翻静态候选 `ReqGetExFactorTable`）；headers
    key 顺序 **`id, userName, token`**（id 在前）；params 仅两键按序
    **`security_code`(str) → `QueryBandWidth`(float 0.0)**；
  - request id：从 **1** 起独立递增（sync id=1、async id=2，均被响应回显）；
  - 响应：`status=0`、headers.tag 为**整数 `11102`**、含 **`pack_num=1`/`all_pack_num=1`**
    分页控制、顶层键 `data/headers/status`；`data` 为**字符串数组**（本样本 33 行）；
  - data 每行为 **5 字段 CSV**（逗号分隔），字段顺序与 `MDExFactorTable` 一致：
    `inner_code, security_code, ex_date, ex_factor, cum_factor`；`ex_date` 以整数字符
    串上线；**两个 double 字段以固定 18 位小数十进制字符串上线**（如 `1.000000000000000000`
    → Python float 1.0），非科学计数、非 base64；
  - 响应帧带 ZSTD 标记（**`0x59+ZSTD`**）；完成后客户端发 **`ReqGetComplete`**
    （无 params，headers 同序），随后服务端主动 close（opcode 8），官方客户端等待关闭后
    自行 close；双端正常关闭语义与 kline/快照 one-shot 通道一致。
- Arm:
  - `_structures.py` 新增 `MDExFactorTable`（pack(1) ctypes 镜像，sizeof=52 + offset/
    位宽/默认值测试；`set_code` 便捷写入）；
  - `_protocol.py` 新增 `EX_FACTOR_WIRE_TAG=11102`、`EX_FACTOR_ROW_FIELD_COUNT=5`、
    `build_ex_factor_request`（单代码、≤32 字节、id-first headers、
    `security_code`→`QueryBandWidth` 键序）、`_decode_ex_factor_row`（精确 5 字段、
    ex_date 必须整数字符、double 字段必须可 float 解析并解码为 Python float）、
    `parse_ex_factor_packets`（经 `_ordered_query_packets` 校验 status/tag/包号完整性后
    展开 CSV 行）；未知分支显式失败；
  - `_backend.py` 新增 `ex_factor` kind（走 one-shot dgw*_query + `ReqGetComplete` +
    `wait_closed` 通道，与 kline/快照同一生命周期路径）；
  - `interface.py` 新增公开 `QueryExFactorTable(security_code, query_spi=None,
    return_df_format=True)`：同步元组 `(rows, 0)`，`query_spi` 显式
    NotImplementedError，bytes code 先解码；df 格式需 pandas；
  - `__init__.py` 导出 `MDExFactorTable`/`QueryExFactorTable`；
    `tools/live_smoke.py` 新增 `--ex-factor CODE` 脱敏 shape 路径；
  - 工具侧按工作流 §4.3 在 `tools/oracle/remote_sdk_oracle.py` 新增独立 `--kind
    ex-factor`（sync probe + 异步收集器，脱敏 shape 含列键/类型/行数、ex_date 位长、
    factor 小数位分布、非负性、**cum_factor 单调性与违例计数**、inner_code 长度分布）。
  - 同步与异步交付一致：本样本单批 33 行。
- Tests: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v` 共
  **129 项全部通过（1 skip pandas 缺失分支）**（原 101 + 新增 28：结构 sizeof=52/
  offset/位宽/默认值；builder key 顺序/`security_code`+`QueryBandWidth`/空·超长代码
  拒绝；5 字段解码类型；double 18 位小数精度往返、高精度 float64、非整 ex_date/
  非数值 double/字段数不符负形状；单包/多包乱序重排/缺包/重复包/错 tag/错 status/
  容器错型/缺包计数/计数不一致；公开合约同步元组、异步 SPI 显式失败、bytes code、
  re-export）。
  `PYTHONDONTWRITEBYTECODE=1 python3 -m compileall -q src/python examples tools`
  通过。fixture 全部合成值（无真实行情/账号/token）。
- Live diff: 2026-08-26 Mac live 用第二授权账号（stdin 注入，未持久化，未打印值）一次
  低频同参请求 `000001`：登录与鉴权成功，`ex_factor_query_error=0`、返回 **33** 行、
  5 列、列键顺序 `inner_code,security_code,ex_date,ex_factor,cum_factor`、每列类型
  str/str/int/float/float 与 Linux oracle 逐项一致；`ex_date_digit_lengths=[8]`、
  `ex_factor_decimal_places=[0,6,13,14]`、`cum_factor_decimal_places=[0,5,6]`、
  `cum_factor_monotonic_nondecreasing=false`、**`cum_factor_monotonic_violations=2`**
  （与 Linux 完全一致）；`inner_code_len=[9]`、`security_code_len=[6]`。命令类别
  （凭据未入命令/仓库）：
  ```bash
  cd "/Users/ellis/工具程序开发/database for armmac"
  export TGW_MAC_USERNAME_OVERRIDE="$(cat <acct2>)"
  printf '%s\n' "$TGW_MAC_USERNAME_OVERRIDE" | \
    python3 tools/live_smoke.py \
      --config "/Users/ellis/工具程序开发/数据库桥接/reverse-macos/config/galaxy_account.ini" \
      --username-stdin --ex-factor 000001
  ```
- Cleanup: bj 上 `/tmp/tgw_exfactor_oracle/`（oracle 副本、interpose.so、capture.bin、
  分析脚本）与 `/tmp/tgw_exfactor_oracle.py`、`/tmp/tgw_exfactor_capture.bin`、
  `/tmp/tgw_eff_*.py` 已 sudo 删除并复核不存在；原始捕获仅在本地系统临时目录短暂存在，
  分析后已删除；`galaxy-relay` 任务前后均为 inactive（两次 `systemctl is-active`
  确认）。仓库扫描无账号、密码、token、MAC、原始行情或捕获文件。
- Proposed status: **`LIVE_ALIGNED(000001 only)`**。
  依据：Linux oracle（错误码 0、33 记录、5 键形状/类型、double 以 Python float 呈现且
  18 位小数往返无损、异步单批）+ wire 全要素实捕（one-shot dgw1_query / method
  `ReqGetExFactor` / headers id-first / `security_code`+`QueryBandWidth` 键序 / tag
  `11102` / pack_num=1 / `ReqGetComplete` / 0x59+ZSTD / CSV 5 字段含 18 位小数 double /
  双端正常关闭）+ Mac 实现、合成测试与独立授权账号 Mac 同参 live 成功（错误码 0、33
  记录、5 列、类型/不变量逐项一致，含 cum_factor 2 处回落为服务端数据本身）。这个状态
  只覆盖 `000001` 单代码同步返回；不外推到下列开放风险。
- Open risks:
  1. 仅 `000001` 一个样本：其它代码（含带小数位不同的因子、首次 cum_factor<1 等）未
     取样，builder 对任意代码放行（≤32 字节），但 double 小数位分布/单调性形态未逐代码
     取证；
  2. 空结果（kDataEmpty=-76 族）与非零 status 的 wire 形状未取证：parser 对非零 status
     一律报错；
  3. 多包响应（all_pack_num>1）语义按 one-shot 通道 `_ordered_query_packets` 通用逻辑
     支持（乱序/缺包/重复包均有负形状测试），但 live 本样本仅单包，未线上观测多包；
  4. 市场路由为代码默认（wire 无市场字段），若服务端对不同代码要求显式市场则本实现无
     该参数；官方签名无市场参数，风险低；
  5. 异步 query_spi 未实现（显式 NotImplementedError），与 K 线/ETF/证券信息/代码表
     一致；官方同步 wrapper 内部 `TmpQueryExFactorWaitSpi` 的等待/超时语义未逐一复刻；
  6. one-shot dgw*_query 通道与 kline/快照/代码表共享连接池与流控；`1000 / accept conn
     active close` 流控未见于此样本，但纪律仍按工作流执行（不密集重试）；
  7. 数值缩放由调用方按头文件 N38(15) 注释处理，本实现如实交付服务端 double 的 Python
     float，不做乘除。

证明文件按工作流 §5 固定小节输出。