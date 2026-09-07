# Machome 四合一运行中心：macOS 构建与交付

本目录包含 Qt 6 GUI、独立后台 Agent，以及四个只通过既有接口接入的业务适配器。
交付脚本的边界很窄：它们只写新的 Hub 应用包、Hub 配置、Hub 日志和
`com.ellis.machome-hub-agent` LaunchAgent，不会修改四套原业务源代码，也不会启停
它们的进程。默认配置全部为 `ownership: "shadow"`、`control_enabled: false`。

## 交付路径

默认使用用户级目录，不需要 `sudo`：

| 内容 | 默认路径 |
|---|---|
| Release 构建 | `app/build-release/` |
| 签名后的应用与 zip | `app/dist/` |
| 已安装应用 | `~/Applications/Machome 四合一运行中心.app` |
| Hub 配置 | `~/Library/Application Support/MachomeHub/config/modules.json` |
| Hub Agent plist | `~/Library/LaunchAgents/com.ellis.machome-hub-agent.plist` |
| Hub Agent 日志 | `~/Library/Logs/MachomeHub/` |
| 安装备份 | `~/Library/Application Support/MachomeHub/backups/<UTC时间>-<pid>/` |

所有路径都有命令行选项和对应环境变量，详见各脚本的 `--help`。路径可以包含中文
和空格；LaunchAgent 渲染时会做 XML 转义。

## 前置条件

- macOS，安装 Xcode Command Line Tools；
- CMake 3.24+；
- Qt 6.5+，包含 Core、Network、WebSockets、Widgets、Sql、Test 和
  `macdeployqt`；
- Python 3（CTest 的完整集成冒烟使用）；
- 本机有足够空间同时容纳构建、暂存包和一个旧版本备份。

先运行完全只读的检查：

```bash
cd "/Users/ellis/工具程序开发/machome四合一运行/app"
bash scripts/preflight.sh
```

`preflight.sh` 不创建临时文件、不加载服务、不连业务端口。它检查工具链、默认 JSON、
现有 Hub 配置/plist、目标目录权限、磁盘空间；若已有交付 bundle，还检查双可执行文件、
签名和非便携绝对库引用。`--strict` 可将警告提升为失败。

## Release 构建、测试和打包

标准交付命令：

```bash
bash scripts/build-release.sh --qt-prefix /opt/homebrew
```

脚本依次执行：

1. 以 `Release` 和 `BUILD_TESTING=ON` 配置、构建；
2. 运行完整 CTest（单元测试、UI offscreen 测试与全链路 mock 冒烟）；
3. 把 `machome-hub-agent` 放进 GUI bundle 的 `Contents/MacOS/`；
4. 用 `macdeployqt -executable=<agent>` 同时扫描 GUI 与 Agent，确保 Agent 独有的
   Qt WebSockets/Sql 依赖也被收进包；
5. 剔除构建机 Qt 安装中“插件存在但配套框架缺失”的不可用可选插件，并验证两个
   必需可执行文件没有未打包或构建机绝对依赖；
6. 对整个 bundle 做 ad-hoc 深度签名并严格验证，再执行 bundled Agent 的
   `--version` 装载检查；
7. 生成 `Machome-Operations-Hub-macOS.zip` 和 SHA-256 文件。

应用包还包含 `Contents/Resources/build_manifest.json`，记录 build ID、UTC 构建时间、
构建类型和源码版本状态；它不包含部署密钥。工程未被 Git 实际跟踪时会明确写
`unversioned`，不会借用父目录仓库的提交号。

如果 `app/dist` 已有同名产物，脚本会把它移动到带 UTC 时间戳的 `dist/archive/`，
不会直接删除。先查看完整命令而不写文件：

```bash
bash scripts/build-release.sh --qt-prefix /opt/homebrew --dry-run
```

可通过 `--build-dir`、`--dist-dir`、`--cmake`、`--ctest`、`--macdeployqt`、
`--codesign`、`--ditto`、`--generator`、`--jobs` 调整环境。`--skip-tests` 只用于明确
的诊断场景，标准交付不可跳过。

ad-hoc 签名适合这台受控 Mac 和内网部署，但不等于 Developer ID 签名、公证或
App Store 分发。若未来跨机器公开分发，应在独立发布流水线替换签名步骤并增加 notarize。

## 安装

安装脚本默认是 dry-run，必须显式授权 `--apply`：

```bash
# 1. 预演
bash scripts/install.sh

# 2. 落盘应用、配置（仅首次）、plist；不改变当前进程
bash scripts/install.sh --apply

# 3. 如确认要让 launchd 接管新的 Hub Agent，再显式请求
bash scripts/install.sh --apply --load-agent
```

实际写入前，脚本先验证源 bundle 签名，并使用 bundled Agent 的 `--check-config`
校验将要使用的配置。随后完成暂存、备份再替换：

- 已有 Hub app 和 Hub plist 被移动到本次版本化备份；
- 已有 Hub 配置会复制一份到备份，但目标配置保持原样；
- 只有目标配置不存在时，才复制 `config/modules.example.json`，权限为 `0600`；
- 任一安装步骤失败，脚本尝试自动恢复旧 app/plist，并保留失败的新文件供审计；
- 未传 `--load-agent` 时，安装不会调用 `launchctl bootout/bootstrap`；
- 传入 `--load-agent` 时，也只操作配置的 Hub label，绝不操作四套业务 label。

如果已存在由 GUI detached 启动、但不受该 label 管理的 `machome-hub-agent`，
`--load-agent` 会拒绝继续，避免 LaunchAgent 因 socket lock 冲突而反复重启；先正常关闭
对应 Hub GUI/Agent 后再切换。脚本不会强杀未知来源的进程。

GUI 自身能够在 Hub Agent 不存在时启动 bundle 内的 Agent，因此 LaunchAgent 是可选的
常驻方式。两个入口共用 socket lock，不会正常启动两个 Agent 实例。

生产切换前应基于 `config/modules.example.json` 另存正式配置。初次上线仍建议保持
shadow；只有逐模块验收完成后，才人工变更对应模块的 ownership/control 开关。安装脚本
不会替用户做这一步。

## 回滚

安装结束会打印精确备份路径。先预演，再执行：

```bash
bash scripts/rollback.sh \
  --backup "$HOME/Library/Application Support/MachomeHub/backups/20260904T010203Z-12345"

bash scripts/rollback.sh \
  --backup "$HOME/Library/Application Support/MachomeHub/backups/20260904T010203Z-12345" \
  --apply
```

也可以用 `--latest` 选择名称排序最新的有效备份。回滚时当前 app/plist 不会被删除，
而会移入该备份的 `rollback-current/<时间>-<pid>/`。配置策略刻意保守：

- 如果这次安装首次创建了配置，回滚会把它移入 `rollback-current`，恢复为原先不存在；
- 如果安装前已有配置，因为安装从未覆盖它，回滚默认也不碰它；
- 只有明确传 `--restore-config`，才用安装前备份替换当前配置，同时先保存当前版本。

默认回滚不改变当前 Hub Agent 进程。需要同步重载时显式加 `--load-agent`；仍只操作 Hub
自身 label。四套旧业务的程序、LaunchAgent、数据和日志不属于本安装或回滚事务。

## 人工交付核对

构建机上至少保存以下证据：CTest 全绿输出、`codesign --verify --deep --strict` 成功、
zip 的 `.sha256`、安装备份路径，以及安装后再次运行 `preflight.sh --bundle <已安装app>`
的结果。然后启动 GUI，确认 Agent 握手、首页四卡片、四个二级页和 shadow 状态；此阶段
只允许刷新和只读观测，不应向任何原业务发送启停或交易控制。
