# AmazingData `BaseData.get_code_info(EXTRA_ETF)` 对齐证据

- Scope: 仅互联网模式高层 `BaseData.get_code_info(security_type='EXTRA_ETF')`。
  不处理 `get_code_list`、默认 `EXTRA_STOCK_A`、其它 `security_type`、期货/期权高层接口，
  也不把本次证据扩大为低层 `QuerySecuritiesInfo` 的全市场通用支持。
- PDF:
  - AmazingData 手册 PDF 页 11–12（正文 7–8）§3.5.2.1：唯一入参是可选
    `security_type:str`，默认 `EXTRA_STOCK_A`；示例明确传入 `EXTRA_ETF`。输出表写
    DataFrame，index 为证券代码，列依次为
    `symbol/security_status/pre_close/high_limited/low_limited/price_tick`。
  - 同手册 PDF 页 133（正文 129）附录 §4.1.1：`EXTRA_ETF` 表示上交所与深交所 ETF，
    并列出 `SH_ETF` 与 `SZ_ETF`。
  - TGW C++ 手册 PDF 页 36（正文 28）：`QueryCodeTable` 是无业务入参代码表接口；
    `QuerySecuritiesInfo(const IGMDSecuritiesInfoSpi*, const SubCodeTableItem*, uint32_t)`
    接收市场+代码数组，两者均标注互联网/托管机房适用。该页和 V1.0.8 头文件将
    `market=kNone`、空 `security_code` 解释为全市场/全代码，但这不能替代 wire 取证。
- Header delta: V1.0.8 `SubCodeTableItem` 是 pack(1) 的
  `int32_t market + char security_code[32]`（sizeof=36）；
  `MDCodeTableRecord` 是 43 字段、sizeof=555。其 `pre_close_price`、`high_limited`、
  `low_limited`、`price_tick` 均为整数微单位（÷1,000,000），`list_day` 为日期整数。
  `QueryCodeTable` 的 `MDCodeTable` 只有 6 字段，不能作为本高层的底层输出结构。

## 官方静态与 Linux oracle

- 官方 AmazingData 1.1.9 wheel 静态对象：签名为
  `(self, security_type='EXTRA_STOCK_A')`。反汇编显示高层按
  **SZSE(102) → SSE(101) → NEEQ(2)** 顺序构造三个
  `SubCodeTableItem{market, security_code=''}`，逐个调用
  `tgw.QuerySecuritiesInfo(item)`；不调用 `QueryCodeTable`，也没有 ThirdInfo/DQS
  function id。每个返回 DataFrame 经 `get_code` 追加 `.SZ/.SH/.BJ`，按安全类型 regex
  去重筛选，四个价格字段除 1,000,000，再将 `pre_close_price` 改名为 `pre_close`。
  `EXTRA_ETF` 的静态 regex 仅包含 `.SZ` 的 `159xxx` 与规定的 `.SH` ETF 前缀族。
- 一次 Linux x86 官方 AmazingData 最小只读 oracle（受保护配置取密码、一次性非回显
  stdin 覆盖授权 Linux 用户名；官方 stdout/stderr 在调用边界丢弃）实际登录成功，并由
  内存内 `tgw.QuerySecuritiesInfo` 包装器观察到上述三次调用：三次都是空代码、返回错误码
  均为 0。各原始调用仅记录行数为 42,469 / 45,751 / 6,455；未打印或保存代码、简称、价格、
  token、MAC、端点或原始响应。
- 官方最终 `EXTRA_ETF` 容器为 DataFrame：1,631 行、index 名 `code_market`、str 且唯一；
  后缀计数 `.SZ=715`、`.SH=916`、`.BJ=0`，全部匹配 ETF regex。没有空值。列顺序为
  **`symbol, security_status, pre_close, high_limited, low_limited, price_tick, list_day`**；
  文本列为 `str`，四个价格列均为 `float64`，`list_day` 为 `int64`。四个价格列乘
  1,000,000 后均为整数。
- PDF 与官方 wheel 的差异：PDF 仅列六列；官方 Linux 额外返回第七列 **`list_day:int64`**。
  本任务以运行中的官方 wheel 为准，离线转换契约保留该列，绝不为迎合 PDF 而删列。
- Oracle 的首次 raw-vs-normalised 逐列布尔摘要把四个价格显示为 false：该摘要错误地使用
  pandas `Series.equals` 比较 `float64`（高层除法后的结果）与 `int64`（截获原值），该 API
  在数值相等而 dtype 不同时返回 false。没有将该结果认定为业务差异；同次运行的
  `×1e6` 整数不变量、官方静态 `div(1000000)` 路径及后续修正后的纯函数测试共同锁定缩放。
  Oracle 代码已改为逐元素数值比较，但没有为了修正测试工具而重复全市场官方请求。

## 底层通道与 wire 边界

- 已证明的高层映射：官方运行时实际调用 **`QuerySecuritiesInfo` 三次**，不是
  `QueryCodeTable`、ThirdInfo/DQS 或本地缓存组合。该结论来自运行期包装器观察，且与
  官方 wheel 字节码相互印证。
- 既有低层 SSE 单代码证据只证明 `QuerySecuritiesInfo` 的 persistent push 通道、
  `ReqGetCodeTableList`、tag `"109"`、`ReqGetCodelistComplete` 及单帧形状；它**不能**
  外推为本高层的全市场空代码三分支。
- 本任务对官方高层做过一次脱敏 SSL interposer capture：进程在析构阶段被 preload 的
  C++/Boost 兼容性问题中止，capture 仅有空容器（无 SSL 记录）。因此没有保存或声明
  高层的 path/method/`Security` 空代码编码、wire request-id 序列、`code_num` 与 data
  关系、帧数、分页或完成时序。为下一次安全捕获，分析器只会输出
  `empty-code|market`、market enum、id/tag/code_num 与 data shape，绝不输出代码值。

## Arm 实现、测试与清理

- Arm: `experimental/amazingdata_compat/amazingdata_re/base_data.py` 保留官方默认签名
  `security_type='EXTRA_STOCK_A'`，但只识别本任务的 `EXTRA_ETF`；默认、未知值和非字符串
  均显式失败。`EXTRA_ETF` 也明确 `NotImplementedError`，说明 Mac 需要先取证三市场
  空代码 `QuerySecuritiesInfo` 的 wire/paging，避免把部分首帧伪装成完整代码表。
  同文件新增未联网的 `_normalise_extra_etf_frames`，锁定已观测的 7 列、index、筛选、
  去重和四个价格的 float64 微单位转换；它不调用 backend、不随 wheel 发行。
- Tests: 新增 `tests/test_amazingdata_get_code_info.py`，覆盖官方默认签名、`EXTRA_ETF`
  blocker、未知/非字符串拒绝、7 列顺序、index/dtype/唯一性、缩放、空值及缺列/错误结果不
  伪装为空成功。含 pandas 的 bundled runtime 为 4/4 通过；默认 Python 缺 pandas 时
  边界测试 2/2 通过、两个 DataFrame 测试正确 skip。
- Mac live diff: 未执行。Mac 的 `build_secinfo_request` 仍拒绝 market 102/2 与空代码，
  backend 仅观测单帧；在缺少高层 wire/paging capture 时强行运行会猜测
  `Security` 和完成语义，故按工作流停止。
- Cleanup: Linux `galaxy-relay` 开始、oracle 后、capture 后均为 `inactive`；远端 oracle、
  interposer source/.so、空 capture 与分析器均已删除并逐路径复核不存在。未把 capture
  或任何会话材料取回仓库。本地 PDF 渲染临时目录已移至废纸篓（可恢复）。

## Proposed status and open risks

- Proposed status: **`LINUX_OBSERVED(BaseData.get_code_info; EXTRA_ETF only)`**。
  官方高层签名、参数、三次真实低层调用、容器、列/类型/不变量已经观测；Mac 只有离线
  normaliser 和明确 blocker，尚无同参 live 成功，不能到 `WIRE_VERIFIED`、
  `ARM_IMPLEMENTED`（live branch）或 `LIVE_ALIGNED`。
- Open risks:
  1. 空代码的 `Security` wire 表示、102/2 market wire 分支、每市场帧数、`code_num`、
     pagination 和 completion 未捕获；
  2. 低层全市场结果巨大，首帧/超时/流控/资源生命周期尚未验证；
  3. `security_status` 的枚举含义、空/非零错误、重复或退市代码、非 ETF 筛选边界尚未做
     Mac 同参；
  4. PDF 的六列与官方七列差异需要未来成功 Mac 同参后继续保留并向用户说明；
  5. `EXTRA_STOCK_A` 仅为官方默认值，不是本任务的可用范围。
