# AmazingData `BaseData.get_calendar` 对齐证据

- Scope: 仅互联网模式、`BaseData.get_calendar` 的默认 `market='SH'`，以及文档列出的
  `data_type='str'`/`'datetime'`。不扩展其它市场、其它高层接口或发行 wheel。
- PDF: `reference/manuals/AmazingData开发手册.pdf` PDF 页 16（正文 12），接口为
  `get_calendar`；`data_type` 默认 `str`、可选 `datetime`/`str`，`market` 默认 `SH`。
  输出表写 `calendar: List[int]`。这与“选择返回数据类型”的文字可能引起元素类型误解，以下以
  官方运行结果裁决。
- C++/header: TGW C++ 手册 PDF 页 37–38（正文 29–30）规定先以
  `SetThirdInfoParam(task_id,key,value)` 设置 `function_id`，再调
  `QueryThirdInfo`；V1.0.8 Linux header 的对应签名为
  `SetThirdInfoParam(int64_t,const std::string&,const std::string&)` 与
  `QueryThirdInfo(const IGMDThirdInfoSpi*,int64_t)`，回调载体是
  `ThirdInfoData{task_id,data_size,json_data}`。C++ 资讯目录 PDF 页 172–174（正文
  164–166）仅列 `A010060001`/`A010061001` 日历，且每段不超过 30 天；本轮官方高层实际使用
  `A010061003`、从历史起点取全量。这是线上 AmazingData/服务版本差异，不得把目录功能号
  替换进当前实现。
- Existing evidence/status: `docs/evidence/` 之前没有日历/ThirdInfo 专项报告；
  `docs/API_STATUS.md` 仅将低层 `A010061003` + SSE 日期范围列为
  `LIVE_ALIGNED(calendar function only)`，`docs/PDF_API_PARITY_MATRIX.md` 明确标明
  高层 wrapper 待验。因此没有把低层成功外推为本高层接口已对齐。

## 官方静态与运行合约

| 项目 | 官方 Linux AmazingData | 修复后实验 wrapper |
|---|---|---|
| 签名 | `(self, data_type='str', market='SH', date=<导入日的 YYYYMMDD int>)` | 相同字段、顺序与默认策略 |
| 默认 / `str` | 升序且唯一的 `list[int]`；元素为 8 位 YYYYMMDD | 相同转换与 `self.calendar` 赋值 |
| `datetime` | 升序且唯一的 `list[datetime.datetime]`（午夜） | `datetime.strptime(..., '%Y%m%d')` 相同转换 |
| 对象状态 | `self.calendar == return` | 相同 |
| 本次边界 | 官方可接受的其它市场与显式非默认日期未请求 | 非 `SH`、未知 `data_type` 与非默认日期在联网前 `NotImplementedError` |

- Linux oracle: 一次官方 x86 AmazingData 高层调用，以受保护配置读取密码、用户名仅从受控 stdin
  一次性注入。登录成功；默认、`str`、`datetime` 三个调用均成功，长度均为 8,714，容器均为
  `list`，默认/`str` 元素仅为 `int`，`datetime` 元素仅为 `datetime`，均升序、唯一、日期形状
  有效；默认结果等于显式 `str`，`str` 不等于 `datetime`。未保存/打印返回日期、凭据、token、
  MAC、端点或原始响应。官方 `logout(username)` 正常完成。
- 实际底层映射: 高层静态与官方运行均指向 `function_id=A010061003`，参数集合为
  `function_id/start_date/end_date/market`（默认 SH 映射为 `SSE`）。既有低层证据证明
  `ReqGetThirdInfo`、tag `11101`、分页 body 与 `ReqGetComplete`；本轮没有额外 SSL capture，
  因而不声称已经捕获官方高层的分页控制序列。Mac 的最小新增私有页读取只将已存在的
  `offset/count` 整数控制传入同一 `ReqGetThirdInfo` envelope，参数本身不作为资讯 key 发出。
- Arm implementation: `experimental/amazingdata_compat/amazingdata_re/base_data.py` 改为通过
  `SetThirdInfoParam` + 私有 `_QueryThirdInfoPage`，不再把裸 task id 直接传进 backend。
  `src/python/tgw_macos/interface.py` 增加未导出的单页 helper；
  `src/python/tgw_macos/_backend.py` 仅为 ThirdInfo 传递已验证 envelope 的私有
  `_offset/_count`。当页少于 1,000 条停止，100 页安全上限防止忽略 offset 时的无限请求。
- Mac live diff: 修复前，Mac 默认/`str` 各仅得到 1,000 个 `int`；`datetime` 也错误地得到
  1,000 个 `int`，均与 Linux 8,714 条全量合约不一致，定位为遗漏高层全量分页及忽略
  `data_type`。修复后的第一次连续复验遇到 `TgwTransportError`，按工作流停止；冷却后，获授权
  只低频重试一次默认分支并成功：返回 `list[int]`、长度 8,714、升序、唯一、8 位日期不变量、
  `self.calendar == return`，与 Linux 默认分支的脱敏 shape/invariants 一致，随后 `Close()`
  正常完成。随后针对剩余的每个显式分支，以独立登录、至少 55 秒间隔、各一次的低频调用复验：
  `str` 与 `datetime` 都登录成功、`Close()` 成功，但查询均以 `TgwTransportError` 停止，未得到
  结果容器。它们和已成功的默认分支共用同一 ThirdInfo 分页/转换路径，故不能从这两次传输失败
  推断返回类型或转换缺陷，也不重复请求以避免流控干扰。此前 Linux 脱敏 oracle 没有持久化首尾
  业务日期，因而本轮不会捏造首尾值比较；可比较且已记录的是长度、元素类型、升序、唯一性和
  `self.calendar` 一致性。
- Tests: 新增 `tests/test_amazingdata_calendar.py` 覆盖默认值/请求参数、显式 `str` 与 `datetime`
  的元素类型、空成功、
  非零错误不伪装为空成功、未知 market/data_type、无效日期与整页后的下一 offset；
  `tests/test_native_protocol.py` 增加 ThirdInfo 分页 envelope 的 offset/count 整数形状。相关
  `unittest` 54 项通过；全量 `unittest discover -s tests -v` 161 项通过（2 项因未安装 pandas
  跳过），`compileall -q src/python examples tools experimental` 通过。
- Cleanup: Linux 开始前与结束后 `galaxy-relay` 均为 `inactive`；一次性远端 oracle 已由
  `galaxyrelay` 身份删除。官方 wrapper 的登录诊断不会写入证据/fixture；保留的 oracle 已将
  官方 stdout/stderr 指向 `/dev/null`，防止其诊断泄露会话材料。
- Proposed status: `LIVE_ALIGNED(default SH; implicit data_type='str' only)`；显式
  `data_type='str'` 与 `data_type='datetime'` 维持 `ARM_IMPLEMENTED(live transport
  blocked)`，因为二者尚无修复后 Linux/Mac 同参结果。它们的离线转换/拒绝测试已通过，但
  不能替代线上同参。
- Open risks: 其它市场、显式非默认日期、空/服务端非零错误的官方高层行为、官方高层分页 wire
  时序、连接限流与资源/重连持续性均未验；不扩展到这些范围。
