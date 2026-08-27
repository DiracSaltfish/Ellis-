# 10｜盘中 TGW 正式验收手册

## 验收原则

从 09:15 到 15:00 以生产方式运行，同时采 A、adapter、B、TGW raw 和 Sina L1 对照。
按用户决定，**不使用 QMT L1 作行情 baseline**。首日正常鸣笛/弹窗，但工具只出统计/异常证据，
不自动 pass/fail。盘后人工分「确定错误/疑似异常/正常采样差/无法证明」。

## T-1

1. 备份旧 app，确认 `mode=live`、A/QMT 地址、端口、容量和密码权限。
2. `capture_dynamic_market_data=true`（只限容量验收日）。
3. 本机 `ctest`/Python tests/protocol smoke 通过；不要在盘前临时修 key map。
4. 磁盘>20GiB，时钟同步，确认 Sina L1 请求可达；Sina 不可用时标记 baseline 缺失。
5. 建立当日验收目录，复制 `docs/12`为当日报告。

## 当日时间线

| 时间 | 动作/证据 |
| --- | --- |
| 09:05 | 启 A-console/B，确认端口驻留、QMT 页签状态，不交易 |
| 09:15 | TGW 登录 + 202 订阅；保存订阅回应、first-full 时间和 ready 曲线 |
| 09:15–09:30 | 集合竞价只采集；确认 signals_enabled=false |
| 09:30 | 确认清窗口/预热，无跨集合竞价信号 |
| 09:31/09:35 | 分别确认 30s/5min 规则解锁 |
| 上午 | 按202→500→1000，每级至少15min；详情/B/旧 UI 同时观察 |
| 11:30 | 确认仍采集但无信号 |
| 13:00/13:01/13:05 | 确认重清、预热、规则逐步启用 |
| 14:57 | 确认记录但信号锁 |
| 15:00 | 确认 TGW 退订，8421/19195 仍监听 |

## 容量命令

202 是固定基线；500/1000 分别添 298/798 动态标的：

```bash
.venv/bin/python tools/load_test.py --host 127.0.0.1 --target 500 --duration 900 \
  | tee logs/capacity-500.json
.venv/bin/python tools/load_test.py --host 127.0.0.1 --target 1000 --duration 900 \
  | tee logs/capacity-1000.json
```

每级记录请求/接受/拒绝、TGW 回应、ready、SDK/分片/落盘峰值、丢弃、CPU/内存/磁盘、TGW→A/A→B 延迟。任一订阅拒绝、持续积压、丢帧、大量隔离、延迟明显失控或磁盘停写，立即停扩容，保留现场。

## Sina L1 对照

```bash
.venv/bin/python tools/compare_sina_l1.py \
  --a-host 127.0.0.1 --a-port 19195 \
  --watchlist config/watchlist.json --duration 900 --interval 1 \
  --output logs/sina-compare-YYYYMMDD.json \
  --samples-output logs/sina-compare-samples-YYYYMMDD.jsonl
```

对照只证明最新价/五档/数量的近时一致性。Sina 采样时间、档位深度和推送节奏与 TGW
不同，必须按时间容差匹配，不能把每个瞬时差异当作错位。Sina 不提供本项目可用的交易所 IOPV
独立基准，不得在报告中写 IOPV 已被独立验证。

## 202 批量、追加与单标的移除复验

```bash
.venv/bin/python tools/tgw_multi_probe.py \
  --account config/tgw_account.ini --username-file config/tgw_username_override \
  --watchlist config/watchlist.json --batch-size 20 --append-symbol 164824.SZ \
  --duration 30 --summary-output logs/tgw-202-plus-164824-summary-YYYYMMDD.json \
  --events-output logs/tgw-202-plus-164824-events-YYYYMMDD.jsonl

.venv/bin/python tools/tgw_unsubscribe_probe.py \
  --account config/tgw_account.ini --username-file config/tgw_username_override \
  --watchlist config/watchlist.json --batch-size 20 --remove-symbol 159866.SZ \
  --baseline-duration 10 --grace-duration 3 --observe-duration 20 \
  --summary-output logs/tgw-202-remove-159866-summary-YYYYMMDD.json \
  --events-output logs/tgw-202-remove-159866-events-YYYYMMDD.jsonl
```

退订通过条件：返回 0；移除前目标有实际事件；宽限期后目标事件为 0；其它标的仍持续更新；
status 无非 0；`decoder_failures` 为空。宽限期内的少量在途帧单独统计，不直接判失败。

## 盘后

```bash
.venv/bin/python tools/quality_report.py \
  --raw data/raw-YYYYMMDD.jsonl.zst \
  --normalized data/normalized-YYYYMMDD.jsonl.zst \
  --output logs/quality-YYYYMMDD.json
```

先备份 raw/report/log，再改映射。如发现小数点/类型/错位，选最小脱敏 raw full+delta 固化为测试，修代码、全部离线回放，再排复验。

## 交易安全

容量/字段验收不需要交易。若用户在首日依信号人工交易，必须自己核对产品、IOPV、持仓、申赎资格、数量和对手价；测试工具不发委托。
