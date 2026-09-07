# 09 machome 真机验证与切换手册

## 1. 目标和硬边界

先验证、再接管；一次只改变一个模块。首次真机测试保持四项旧业务原样运行，Hub 仅做 shadow 读取。任何启停、Webull runner 替换、Wind/QMT mutation 或 owner 交接，都另约维护窗口。

禁止一次性打开四模块控制；禁止用 `kill -9`；禁止让旧 GUI/launchd 与 Hub 同时监管同一进程；禁止让两个 Webull runner 共用 Chrome profile，或让两组 Wind/行情订阅并存。

## 2. 真机前准备

在项目目录执行只读预检：

```bash
cd "/Users/ellis/工具程序开发/machome四合一运行/app"
bash scripts/preflight.sh --qt-prefix /opt/homebrew
```

随后生成正式交付包：

```bash
bash scripts/build-release.sh --qt-prefix /opt/homebrew
```

必须保存：CTest 输出、zip SHA-256、`codesign --verify --deep --strict` 结果、构建时间和配置 hash。若任何测试失败，不进入安装。

## 3. 安装但不接管

先查看计划：

```bash
bash scripts/install.sh
```

确认目标只包含新 Hub app、Hub 配置和 `com.ellis.machome-hub-agent` 后再安装：

```bash
bash scripts/install.sh --apply
```

此步骤不加载 Hub Agent，也不改变四套业务。检查正式配置：

```text
~/Library/Application Support/MachomeHub/config/modules.json
```

首次必须确认四个模块都是：

```json
"ownership": "shadow",
"control_enabled": false
```

不要用安装脚本覆盖已有配置；脚本的设计也会拒绝这样做。

## 4. Shadow 启动与静态检查

从 `~/Applications/Machome 四合一运行中心.app` 打开 GUI。GUI 可以按需启动 bundle 内 Agent；也可在单独确认后仅加载 Hub 自己的 LaunchAgent：

```bash
bash scripts/install.sh --apply --load-agent
```

检查：

1. Agent 行显示 host、version、PID、instance、artifact hash、config hash；
2. 首页恰好四张卡，状态更新互不连带；
3. 四个子页均可进入，断开一个 mock/真实端点不会冻结其余页面；
4. 所有 mutation 和服务启停按钮禁用；
5. 四个内建二级页均可查看；`open_legacy_ui` 在标准配置中不可用，确需比对旧 UI 时只在未接管状态下按独立维护步骤人工打开；
6. 关闭主窗口后托盘仍在、业务不退出；显式退出 GUI 后常驻 Agent/旧业务不受影响；
7. `audit.db`、socket、配置权限仅当前用户可访问；日志和事件没有 token/password/cookie。

## 5. 真实交易日 shadow

至少记录以下窗口：A 股开盘前、集合竞价、上午、午休、下午、14:57 后、收盘；Webull 另覆盖其实际采集时段和自然跨日。

逐屏对照旧 UI 与新 UI：

- Upload：网站、所有 uploader、ACK/freshness、盘外反向请求和日终任务；
- Premium：summary、signal、30 分钟回放、8421/L1 状态、订阅数、历史文件；
- Webull：auth/browser/collector/data、XOP 十档、sequence/session/stale、客户端；
- 实时申赎：7 标的、首帧基线、变更提醒、PCF 方向/篮数、历史、Wind 九态。

每一项按 [完整功能验收清单](06_完整功能验收清单.md) 记录时间、预期、实际和证据。发现业务字段、单位、时序或 stale 判定不一致时，保持 shadow，不进入控制测试。

## 6. Logic 控制试点

只选一个模块，把该模块改为 `ownership=logic` 且 `control_enabled=true`，重启 Hub Agent 使配置生效；其他三个保持 shadow。logic 只开放业务 API 控制，不接管旧进程生命周期。

建议顺序：

1. Webull；
2. 实时申赎；
3. Premium；
4. Upload（Upload 主要是生命周期控制，可直接留到 owner 阶段）。

每条命令检查 accepted → running → succeeded/failed、control revision、最终权威状态和唯一审计记录。验证重复 command ID 不重复动作，旧 revision 被拒绝；超时后先刷新状态，禁止盲目重试。

### Webull 特殊步骤

现有入口没有控制 API。必须在维护窗口按 [Webull runner 手册](../app/modules/webull/README.md) 用模板替换该模块的 LaunchAgent 入口；旧入口与 runner 共用 `gateway.lock`，不能并行。启用 mutation 前确认 18766 的 LISTEN PID 就是批准的 LaunchAgent，并同时核对数据 API `token_file=runtime/api.token` 与控制 `control_token_file=runtime/control.token`：两者必须是不同的绝对路径、当前用户所有且文件精确 `0600`。主机存在不可信本地进程时不开放控制面。回退时恢复旧 plist/入口。

### 实时申赎特殊步骤

logic 阶段仍只有一个 6787 server 和一组 Wind。先测 watchlist/name/PCF，再测 monitor；Wind 启停放在单独窗口。QMT 先只连接和同步，核对 welcome/orders/positions 后，才允许用测试标的执行固定一篮双击下单验收。

### Premium 特殊步骤

Hub 只连 8421/19195，绝不能连私有 `runtime/tgw.sock`。先测 sync/raw snapshot，再测 watchlist/L1 hotlist ACK；core/TGW 生命周期留到 owner 交接。

## 7. Lifecycle owner 交接

只有本模块 06 清单通过并完成回滚演练后，才改为 `ownership=owner`。不要直接使用 `config/modules.owner.template.json`：它的 ownership、generation、Program 和 hash 是故意无效的防误操作占位值。完整的旧 supervisor 清单、plist 身份冻结、marker/state 命令、逐模块门禁和回退步骤见 [10 owner 接管与回退演练](10_owner接管与回退演练.md)。通用顺序：

每个 owner launchd unit 还必须有 `expected_artifacts=[{path,sha256},…]`，其中必须包含 `expected_program`，且该项 hash 精确等于 `artifact_sha256`。对 Python 解释器、shell wrapper 或打包 Python app，不得只 hash Python/shell 解释器：必须用 `owner_handoff.py inspect-plist --python-unit --python-entry <真实入口> --deployment-manifest <部署 manifest>` 把入口脚本/打包 payload 和包版本、源码哈希清单一并冻结。工具要求真实入口显式出现在 plist `ProgramArguments`，会解析固定 schema 的 manifest、逐文件验 hash 并把所有文件展开进 `expected_artifacts`；完整 schema 见 [10 owner 接管与回退演练](10_owner接管与回退演练.md)。隐藏在 shell 命令文本中的入口不具备可验证身份，应先改成显式参数。

owner 模式禁止保留 `open_legacy_ui`：已确认旧 UI 会自动拉起旧 supervisor，可能制造双 owner。业务二级查看使用四合一内建页面；如需比对旧 UI，只能在未接管或已完成回退的独立维护步骤中人工启动。

1. 保存旧 app/plist/config、PID/端口/订阅和数据新鲜度快照；
2. 等待当前批次安全结束；
3. 禁用旧 owner 的自动拉起职责，但保留文件；
4. 确认没有同模块第二 owner、第二订阅或端口占用；
5. 开启 Hub owner，并按 UI 的精确 target 二次确认启动；
6. 等待 Agent 的最终状态验证，不以 launchctl 返回 0 代替 readiness；
7. 跑该模块 golden/smoke，观察至少一个完整业务窗口；
8. 失败立即只回滚当前模块，其他三项不动。

Premium 的 core+TGW 是一个依赖组；Upload 各 uploader 可分别核对但仍按已批准 label；Webull 通常保留 launchd 为唯一进程 owner、Hub 只做 logic；实时申赎必须保证旧 standalone server 与内嵌 6787 不双占。

## 8. Hub 自身回滚

安装会打印备份目录。先 dry-run：

```bash
bash scripts/rollback.sh --backup "/完整/备份/路径"
```

确认后：

```bash
bash scripts/rollback.sh --backup "/完整/备份/路径" --apply
```

如明确需要同步重载 Hub Agent，再加 `--load-agent`。回滚脚本只处理 Hub app/plist/config；当前版本会移入 `rollback-current`，不会直接删除，也不会操作四套业务。

## 9. 放行标准

只有以下条件同时成立才称为“业务直接平移完成”：

- 06 清单无未解释失败，所有 N/A 有负责人说明；
- 版本/配置/artifact hash 可追溯；
- 四模块分别通过完整时段 shadow 和控制试点；
- 每个 owner 都演练过独立回滚；
- 不存在双 owner、重复订阅、重复上传或重复订单；
- 8 小时 soak 无 UI 卡顿、无界队列/内存增长和跨模块连锁故障；
- 原页面保留一个发布周期，二级入口和业务结果与批准基线一致。
