# 10 Lifecycle owner 接管与回退演练

## 1. 文件定位与安全边界

[`modules.owner.template.json`](../config/modules.owner.template.json) 是四模块的终态**填写清单**，不是可直接上线的配置。它故意包含非法的 `ownership=OWNER_TEMPLATE_NOT_RUNNABLE`、`generation=0`、非绝对的 Program 和非 64 位 SHA-256；同版本 Agent 必须拒绝它。不得只把四处 `ownership` 全部替换后运行。

[`owner_handoff.py`](../app/scripts/owner_handoff.py) 只能：

- 读本地 plist 与其 Program，计算精确 ProgramArguments 和 SHA-256；
- 生成有效期不超过 15 分钟的一次性 handoff marker 及脱敏回执；
- 在回退后把指定 owner state 原子改为 `active=false`。

工具没有 `launchctl`、进程、网络或远程调用路径，不会启动、停止、重启或判定任何业务。其写入均为绝对路径下的原子 `0600` JSON；默认拒绝覆盖、符号链接、他人所有文件和可被组/其他用户写入的父目录。

## 2. Agent 实际接管门槛

一个模块只有同时满足下列条件才能执行 `start_service / stop_service / restart_service`：

1. `ownership=owner && control_enabled=true`，且服务级动作均在 `allowed_actions` 与二阶段审批列表中。
2. owner 模块只允许非空 `launchd_units`，不允许 `managed_processes`。
3. 每个 unit 的最终 plist `Label`、Program、完整 ProgramArguments 和 `expected_artifacts` 同配置逐字段一致；`expected_artifacts` 必须包含 expected Program，该项 hash 必须等于兼容字段 `artifact_sha256`。已加载 label 身份不一致时拒绝动作。
4. `owner_lease` 具有独立 lock/marker、至少 8 字符 owner ID、单调增加的 generation、一次性 token SHA-256 和旧监管器身份。
5. 首次进入新 generation 时，marker 必须为当前用户 `0600`，module/owner/generation/token 匹配，`previous_owner_stopped=true`，且剩余有效期大于 0、总有效期不超过 15 分钟。
6. Agent 会再独立检查：`previous_owner_processes` 不存在，每个 `previous_owner_labels` 的 `launchctl print` 必须权威返回 service not found。未知、超时或仍加载都按不安全处理。
7. Agent 获取每模块 QLockFile 后，原子写入 `<lock_path>.state.json`，再消费 marker。同 owner/同 generation 的 `active=true` state 用于 Agent 崩溃后恢复，不应重新生成 marker。
8. 启动/重启后仍要等待新服务世代的 `owner_readiness` 条件；只有 `launchctl` 成功不等于业务成功。

## 3. 2026-09-04 已知旧监管器清单

下表只是本次只读审计基线，不是永久事实。每个维护窗口必须重新导出 `ps`、`launchctl print`、监听 PID、plist 和 hash；新发现的监管器必须补入候选配置。

| 模块 | 已知旧 owner/supervisor | 候选配置的排除要求 |
|---|---|---|
| Upload | `com.newnavnav.web`、`com.newnavnav.sina-quote-uploader`、`com.newnavnav.private-xop-family-uploader`、`com.newnavnav.private-nasdaq-valuation-uploader`、`com.newnavnav.private-sp500-valuation-uploader`、`com.newnavnav.private-nikkei225-valuation-uploader`、`com.newnavnav.private-germany-valuation-uploader`、`com.newnavnav.private-161226-silver-uploader`、`com.newnavnav.private-china-internet-valuation-uploader`、`com.newnavnav.private-164824-valuation-uploader`、`com.newnavnav.private-intraday-rebuild-agent`、`com.newnavnav.upload-health-monitor`；另观测到中国互联网历史回填 shell | 全量终态要求所有旧 label not-found。若分批，每个候选只纳入本批 target unit 和它对应的旧 label，其余业务仍由旧 label 唯一托管。手工/定时回填不得在切换窗口中启动。 |
| Premium A | A-console 进程前缀：`/Users/ellis/Applications/ETF溢价率拉升监控/etf-premium-console.app/Contents/MacOS/etf-premium-console`；它子管 core/TGW，当前无固定业务 LaunchAgent | `previous_owner_processes` 只写旧 **supervisor** A-console，不把新 owner 将启动的 core/TGW 程序前缀写入，否则后续每次操作都会自我拒绝。但切换前仍须人工确认 core/TGW 无孤儿 PID、8421/19195/TGW UDS 无旧占用。 |
| Webull | `com.ellis.webull-lv2-gateway`（KeepAlive） | 默认保持 `logic`。若获批 owner，先使旧 label not-found，新 label 必须不同；同时确认 `gateway.lock`、Chrome profile、18765/18766 只有一个持有者，且数据 API/control 凭据分离。 |
| 实时申购赎回 | `com.etfdelivery.mac-home`；还要排除旧 `com.etfdelivery.monitor-server` | 两个旧 label 都必须权威 not-found，并确认只有一个 6787 server、一组 Wind 订阅。 |

新 Hub target label 和旧 supervisor label 必须不同。否则旧 label 在 Hub 启动它之后就不再是 not-found，下一条命令会触发双 owner 保护并释放 lease。模板因此使用独立的 `com.ellis.machome-hub.<module>.*` 目标 label。

## 4. Shadow → logic → owner 演练门禁

| 阶段 | 许可状态 | 进入条件 | 退出/回退方式 |
|---|---|---|---|
| Shadow | `ownership=shadow`、`control_enabled=false` | 默认安装；旧 owner 原样运行 | 关闭 Hub UI/Agent 即可；不触及业务 |
| Logic | 单模块 `ownership=logic`、仅开批准的业务 API 动作 | 完整交易日 shadow 对比、幂等/审批/审计通过 | 恢复该模块 shadow；旧进程 owner 未变 |
| Owner 预演 | 只在 mock/隔离端口使用 owner 候选 | fake plist/label、身份冲突、过期 marker、旧 owner 存活、readiness 超时、回退均已注入 | 删除测试临时目录；不能当作真机验收 |
| Owner 真机 | 单模块 `ownership=owner && control_enabled=true` | 06 清单、本文准备/回退演练、维护窗口和双人复核全部完成 | 只回退当前模块；其他三个配置/进程不动 |

四个模块不得同时从 logic 进 owner，也不得预先生成四个 marker。推荐顺序是 Webull（通常停在 logic）→实时申购赎回→Premium→Upload 子单元分批。

## 5. 维护窗口前的离线准备

### 5.1 建立候选配置，不覆盖生产

1. 复制 owner 模板到带变更单号的新文件；生产 `modules.json` 保持不动。
2. 删除候选中非本次接管的 target unit；其他三个模块直接从已验收的现行配置复制，保持 shadow/logic。
3. 新建的 target plist 只修改经批准的 `Label`、托管和日志语义，ProgramArguments、WorkingDirectory、EnvironmentVariables、KeepAlive/ThrottleInterval 逐项与基线比较。不运行它。
4. target plist 放在当前用户拥有且组/其他用户不可写的专用目录。不与旧 `~/Library/LaunchAgents/*.plist` 共用同一文件。

### 5.2 冻结每个 target 的 launchd 身份

对**最终新 plist**逐个执行（下例不加载 plist）：

```bash
python3 app/scripts/owner_handoff.py inspect-plist \
  --plist "$HOME/Library/Application Support/MachomeHub/owner-plists/com.ellis.machome-hub.webull.gateway.plist" \
  --unit-id gateway \
  --expected-label com.ellis.machome-hub.webull.gateway \
  --start-delay-ms 2000 \
  --output "$HOME/Library/Application Support/MachomeHub/evidence/webull-gateway.identity.json"
```

Webull、Upload Python uploader、实时申购赎回等 Python/打包 Python unit 必须显式增加：

```bash
  --python-unit \
  --python-entry "/absolute/path/to/the/real_entry.py" \
  --deployment-manifest "/absolute/path/to/deployment-manifest.json"
```

`--python-entry` 不是任意补充 artifact：其规范化绝对路径必须在 plist 的 `ProgramArguments` 中精确出现一次，并且必须等于参数中首个 `.py/.pyw` 真实入口。隐藏在 shell 文本里、未出现在 plist 参数中的 Python 入口不满足 owner 身份冻结要求，应先把最终 plist 改为显式入口。`/bin/zsh`、`/usr/bin/env` 等通用 launcher 的实际脚本/payload 会被工具自动加入 `expected_artifacts`；相对 payload 必须由 plist 的绝对 `WorkingDirectory` 唯一解析，`shell -c` 会被拒绝。

部署 manifest 使用下列固定 schema（机器可读契约见 [`owner-deployment-manifest.schema.json`](../contracts/owner-deployment-manifest.schema.json)）。`files[].path` 一律相对 manifest 所在目录，不是相对当前 shell；入口脚本本身必须列入 `files`：

```json
{
  "schema_version": 1,
  "kind": "machome_owner_deployment_manifest",
  "path_base": "manifest_directory",
  "metadata": {
    "build": "2026.09.04",
    "source_revision": "approved-commit-or-build-id"
  },
  "files": [
    {
      "path": "service.py",
      "sha256": "64位小写十六进制"
    },
    {
      "path": "package/worker.py",
      "sha256": "64位小写十六进制"
    }
  ]
}
```

工具会解析而不是盲信该文件：拒绝绝对路径、`..`/非规范路径、符号链接逃逸、相对或解析后重复、缺失文件和 hash 不符，并将 manifest 本身及 `files` 中每个文件逐项展开到 `expected_artifacts`。运行期 Agent 因而会逐文件复验，不是只验证一份可被修改后重新解释的 manifest。非 Python 的额外资源仍可重复使用 `--artifact /absolute/path` 加入。

将输出的 `launchd_unit` 对象（包括 `expected_artifacts`）原样替换候选配置对应项。若 plist、Program 或附加 artifact 可被组/其他用户写入、Label 不匹配、路径不绝对或文件不存在，工具会拒绝生成证据。

注意：SHA-256 是 Program 路径指向文件的内容 hash，不是 plist hash。回执另外包含 `plist_sha256`供人工审计；配置中应填 `launchd_unit.artifact_sha256`。

### 5.3 配置和回退预检

在生成 marker 前完成：

- 候选模块使用唯一 owner ID 和比该 lock 历史 state 更大的 generation；
- `previous_owner_labels/processes` 是本窗口重新盘点结果，不是盲信模板；
- 三个服务级动作都要二阶段审批；
- owner 候选不得包含 `open_legacy_ui`：现有旧 UI 会自动拉起旧 supervisor，可能造成双 owner；二级查看使用四合一内建页面；
- Webull 的 `webull_collector_start` 与 `webull_show_login` 和其他 mutation 一样必须二阶段审批；
- `owner_readiness` 能够区分存活、协议身份和时段化业务状态；
- 原 app/plist/config/data 备份位置和恢复命令已实演；
- 同版本 `machome-hub-agent --config /absolute/candidate.json --check-config` 返回 0；
- 候选配置、每个 identity 回执、target plist、Program 的 SHA-256 和变更单关联存档。

## 6. 单模块真机接管 runbook

下列每一步都是门禁。结果不确定时停在当前步，不得通过盲目重试跨过。

1. **冻结快照**：记录时间、现行 config/build hash、旧 label/PID/父子关系、端口 PID、订阅数、末次成功、进行中批次与数据 age。
2. **安全 idle**：用旧系统本身的正常停订/停批次/退出方式结束业务；Wind、TGW、浏览器和上传器都不使用 `kill -9`。
3. **停止旧 supervisor**：在已保存 plist 备份后，仅对当前模块的批准 label 执行停止/卸载；Premium 使用 A-console 正常退出。不改其他三个模块。
4. **双 owner 排除**：旧 label 必须均是权威 service not found，旧 supervisor 进程前缀不存在，端口/UDS/profile/lock/订阅无孤儿持有者。
5. **最后时刻生成 marker**：先确保 owners/evidence 目录属于当前用户且组/其他用户不可写。示例：

   ```bash
   python3 app/scripts/owner_handoff.py prepare-marker \
     --module-id webull \
     --owner-id change-20260904-webull \
     --generation 1 \
     --lock-path "$HOME/Library/Application Support/MachomeHub/runtime/owners/webull.owner.lock" \
     --marker "$HOME/Library/Application Support/MachomeHub/runtime/owners/webull.handoff.json" \
     --previous-label com.ellis.webull-lv2-gateway \
     --previous-owner-stopped \
     --receipt "$HOME/Library/Application Support/MachomeHub/evidence/webull-generation-1.receipt.json"
   ```

   `--previous-owner-stopped` 只是操作员显式声明，不是工具检查结果。把脱敏回执中的完整 `owner_lease` 复制到候选模块；不要把 marker 内明文 token 输出到终端、日志或工单。
6. **再次校验候选**：确认只有本模块是 owner/control enabled，其他三个原样；同版本 Agent `--check-config` 必须返回 0。
7. **启用新配置**：先正常退出旧 Hub Agent，用已审批的原子配置替换流程切换候选，再启动 Hub Agent。Agent 应消费 marker，建立 `0600 .state.json`，并显示 lease held。如果 marker 过期，恢复旧 Hub 配置重做窗口检查，不手工改时间。
8. **执行精确启动**：在 UI 核对模块、动作、目标 label 后完成二阶段确认。只执行一次 `start_service`；等待 accepted → running → succeeded/failed/timed_out。
9. **权威验证**：确认 target label/program/arguments/hash、唯一 PID、端口/订阅所有者、owner readiness 和模块 golden 全部匹配。超时为未知结果，先对账，不重发命令。
10. **观察与放行**：先过本模块 smoke/golden，再过批准的完整业务窗口。旧 plist/app/config/data 保留但不自动拉起。

## 7. 四模块专项门禁

### 7.1 Upload

- 模板列出的 12 个 target 是**全量终态**，不表示可一次性切换。分批时只把本批 unit 纳入 `launchd_units`，且只卸载一一对应的旧 label。
- 每批都核对独立 health 文件的 PID/source/timestamp、最近 ACK、单实例 lock、重复上传和反向请求。`site.healthy` 只证明 Web，不能单独为 uploader 放行。
- 定时/一次性 rebuild/backfill 不与长驻 uploader 混在同一批次。有任务在运行时不切换。

### 7.2 Premium A

- core + TGW 作为一个依赖组接管和回退，启动顺序与停止逆序须与批准基线一致。
- A-console 退出后要另行核对 core/TGW 都已正常退出；Agent 的 `previous_owner_processes` 检查旧 supervisor，不能替代孤儿子进程/端口检查。
- readiness 至少要有 8421 summary connected、19195 L1 connected 和非空 phase；业务放行还要按时段验证 TGW login/subscribe、watch/hot ready。Hub 不连私有 TGW UDS。

### 7.3 Webull

- 首选终态仍是 launchd 管 sidecar、Hub `logic` 管 collector 动作。只有确实需要 Hub 统一服务启停时才进 owner。
- 旧 KeepAlive label 未权威卸载时不生成 marker。新/旧 runner 不得同时使用 `gateway.lock`、Chrome profile 和 token/data 目录。
- 任何 mutation 候选必须同时配置 `token_file=/Users/ellis/WebullLV2Gateway/runtime/api.token` 和独立 `control_token_file=/Users/ellis/WebullLV2Gateway/runtime/control.token`；路径必须绝对且不同，文件当前用户所有并精确 `0600`，两个 token 分别轮换与审计，不进 argv/URL/日志。
- `status.api_live=true` 是 owner 进程启动的最低 readiness；交易时段放行还要 auth/browser/collector/data fresh、session/sequence/gap 和 XOP 十档 golden。盘外计划停止不得误判为故障。

### 7.4 实时申购赎回

- 同时排除 `com.etfdelivery.mac-home`、`com.etfdelivery.monitor-server`、未托管 6787 PID 和第二组 Wind 订阅。
- readiness 必须同时满足 `health_probe.ready=true` 与 `health.protocol=1`；交易时段再验证 monitoring/Wind 九态、7 标的首帧、freshness、PCF 方向/篮数。
- Wind 回退始终是停订 → 温和退出 → 确认 → 指定临时文件清理，不使用 SIGKILL。QMT 在 owner 窗口默认不自动下单。

## 8. 单模块回退 runbook

任一双 owner、身份/hash 不一致、readiness/golden 失败、重复订阅/上传/订单、未知命令结果或其他模块受影响，都立即只回退当前模块。

1. 锁定本模块新命令，保存 command ID、审计、target label/PID/端口、配置/hash 和失败证据。
2. 若结果未知，先权威对账，不重发 stop/restart。确定安全后用 Hub 正常 `stop_service`停止 target 组，确认所有 target label not-found、端口/订阅已释放。
3. 正常退出 Hub Agent，确认本模块 QLockFile 不再被持有。不在 Agent 运行时修改 state：内存中的 owner 能力不会因文件改变而立即释放。
4. 用 state 中的精确 module/owner/generation 失效化恢复凭据：

   ```bash
   python3 app/scripts/owner_handoff.py invalidate-state \
     --state "$HOME/Library/Application Support/MachomeHub/runtime/owners/webull.owner.lock.state.json" \
     --module-id webull \
     --owner-id change-20260904-webull \
     --generation 1 \
     --marker-nonce-sha256 '<从 state 复核的 64 位值>' \
     --reason 'rollback: readiness golden mismatch' \
     --confirm-owner-stopped
   ```

   该命令只把 state 原子改为 `active=false`；它不停服务、不删 lock、不恢复 plist，也不释放仍在运行 Agent 的 lease。
5. 恢复该模块原 shadow/logic 的 Hub 配置并启动 Hub Agent；其他三个模块配置必须逐字节不变。
6. 按旧系统 runbook 恢复原 app/plist/config/data，仅启动一个旧 owner；核对旧 label/program/arguments/hash、唯一 PID/端口/订阅、readiness 和 golden。
7. 保留 target plist、Hub 审计、inactive state 和失败证据，不删除现场。下次接管使用更大 generation 和全新 marker/token/nonce，不复用旧回执。

如果 target 无法被权威停止、Wind/TGW/上传批次无法确认安全、或旧 owner 无法恢复，不执行 `invalidate-state`以伪造“已交还”状态；保持新命令锁定并升级人工处置。

## 9. 演练与验收记录

每模块在真机之前保留下列结果：

- [ ] 模板本身被 Agent `--check-config` 拒绝，且原因包含非法 ownership。
- [ ] 最终候选配置只有一个 owner，并被同版本 Agent 接受。
- [ ] 每个 target plist 都有 identity JSON，Label/Program/arguments/artifact hash 与候选一致。
- [ ] 旧 supervisor 存活、旧 label 仍 loaded、旧 label 状态未知时，Agent 全部拒绝 lease/变更。
- [ ] marker 权限过宽、token/module/owner/generation 不匹配、过期、重复消费均失败关闭。
- [ ] 同一 lock 双 Agent 竞争时只有一个持有者。
- [ ] 已加载 label 指向错 Program/arguments/hash 时，stop/restart 也被拒绝，不“错杀”。
- [ ] start/restart 需新 PID 与新世代 readiness；stop 需全部 unit 权威 unloaded。
- [ ] readiness 超时后命令保持未知结果锁，迟到权威状态可对账，不盲目重试。
- [ ] 从安全 stop、Agent 退出、state 失效、恢复旧 owner 到 golden 的完整回退已演练。
- [ ] 当前模块演练期间，其他三个模块 PID、订阅、数据 age 和命令审计无变化。

只有本文与 [06 完整功能验收清单](06_完整功能验收清单.md) 同时有证据通过，才可将单模块 owner 作为完成；“配置能加载”、“命令返回 0”或“进程存活”都不是直接业务平移的充分条件。
