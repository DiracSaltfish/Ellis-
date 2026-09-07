# GetErrorMsg 纯本地错误文案对齐证据

- Scope: 只验证 `GetErrorMsg(error_code)` 的公开枚举值到中文文本的纯本地查表行为。Linux x86
  官方 wheel 与 Mac 均逐个调用所有已登记的 `ErrorCode`，另调用三个未登记码。**不登录、不读
  凭据、不发网络请求、不构造 query/subscribe、无抓包**；本证据不验证任何服务端错误码的触发条件。

- PDF: [中国银河证券格物金融服务平台(TGW)开发手册(C++版).pdf](../../reference/manuals/中国银河证券格物金融服务平台(TGW)开发手册(C++版).pdf)
  PDF 页 62（正文 54）§4.2 `ErrorCode`。已用渲染页人工核对该表的名称、数值与中文说明。
  它列出 `kFailure=-100` 至 `kApiInterfaceUsing=-73` 及 `kSuccess=0`；`GetErrorMsg` 的具体
  fallback 文案由官方 Python wheel 的直接调用确定。

- Header delta: V1.0.8 Linux/Windows `tgw_datatype.h` 的 `ErrorCode` 在 PDF 表末尾另有
  `kTaskIdRepeat=-70` 和 `kDqsError=-69`。官方 Linux wheel 的 `tgw.ErrorCode` 也枚举这两个
  成员，故 Mac 必须保留；不能按 PDF 的简略表删掉。枚举中没有 `-72/-71`。

- Linux oracle: 2026-08-30，在 bj 的官方 x86 Linux wheel 进程中，仅导入 `tgw` 后调用
  `tgw.GetErrorMsg`；没有调用 `Login`、`Close`、任何 Query 或订阅接口。进程由
  `flock -n /tmp/tgw_official_acceptance.lock` 非阻塞串行化，退出时自动释放锁；没有创建远端
  临时文件。随后确认 `galaxy-relay` 仍为 `inactive`。官方 wheel 枚举 31 个成员，31 次逐码
  调用全部返回下表文本；额外三次未登记码均返回 `unknown error code`。

  | code | Linux official `GetErrorMsg` text |
  |---:|---|
  | -100 | 失败 |
  | -99 | 未初始化 |
  | -98 | 空指针 |
  | -97 | 参数非法 |
  | -96 | 网络异常 |
  | -95 | 数据无权限 |
  | -94 | 未登录 |
  | -93 | 分配内存失败 |
  | -92 | 通道错误 |
  | -91 | 查询服务端hqs任务队列溢出 |
  | -90 | 账号已登录 |
  | -89 | 查询服务端HQS系统错误 |
  | -88 | 非查询时间段(非查询时间段不支持查询) |
  | -87 | 数据库和代码表中没有指定的代码 |
  | -86 | api模式非法 |
  | -85 | 超过最大可用线程资源 |
  | -84 | 数据解析出错 |
  | -83 | 获取数据超时 |
  | -82 | 周流量耗尽 |
  | -81 | 代码表缓存不可用 |
  | -80 | 超过最大订阅限制 |
  | -79 | 丢失连接 |
  | -78 | 超过最大查询数（含代码表） |
  | -77 | 三方资讯查询未设置功能号 |
  | -76 | 数据为空 |
  | -75 | 用户不存在 |
  | -74 | 账号/密码错误 |
  | -73 | api接口不能同时多次调用 |
  | -70 | 任务id重复 |
  | -69 | 查询服务端DQS系统错误 |
  | 0 | 成功 |

  | 未登记 code | Linux official fallback |
  |---:|---|
  | -72 | unknown error code |
  | -71 | unknown error code |
  | 1 | unknown error code |

- Wire: 不适用，按本任务边界没有网络会话、request、response、tag、完成消息或 capture。

- Arm: `src/python/tgw_macos/interface.py` 的 `_ERROR_MESSAGES` 已与上表 **31/31 精确一致**，
  fallback 也与三个未登记码一致，故没有修改实现。新增
  `tests/test_get_error_msg.py`：锁定完整的 `ErrorCode` 名称/数值映射、31 个 code-to-text 文案、
  HDR-only `-70/-69` 与三种 fallback；期望值来自本次 Linux 官方逐码调用，而非从 Mac 表反推。

- Tests: `python3 -m unittest tests.test_get_error_msg -v` 为 **4/4 通过**；
  `python3 -m unittest discover -s tests` 为 **177 通过、2 项设计性 skip**；
  `python3 -m compileall -q src/python examples tools` 通过。fixture 只有公开错误码和文案，
  不含帐号、token、行情或 capture。

- Live diff: 此接口是纯本地无状态查询，Linux official wheel 与 Mac 使用同一组 34 个整数输入。
  31 个登记码文案和 3 个未登记 fallback 均完全相同；没有登录或线上服务参与，因此这不是业务
  query 的 Linux/Mac 网络同参验收。

- Cleanup: 没有远端脚本、capture、凭据或网络会话；共享协调锁由进程退出自动释放。
  PDF 渲染中间文件和本轮生成的 `__pycache__` 在收尾测试后移出工作区。

- Proposed status: `LINUX_OBSERVED(pure local GetErrorMsg table)`；不编辑中央状态文档。它仅说明
  官方 Linux wheel 与 Mac 的查表文本完全一致，不提高各错误码的线上触发条件或其它 API 状态。

- Open risks: `GetErrorMsg` 不解释错误码何时由服务端/官方 wrapper 产生。除已单独验收的
  `kDataEmpty=-76` 快照路径外，非零 status 的线上映射、异步交付、超时/权限/流控条件仍须随各
  具体接口分别取证。
