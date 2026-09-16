# 分钟原始行情无损压缩（2026-09-14）

`intraday_minute_store/YYYYMMDD/minute_quotes.csv` 是报价采样原始档案，
不是网站完整估值历史。此次仅更新 MachomeHub 的采集端，不改网站 Go 服务、
历史日期的补传规则或历史估值存储。

## 行为

- 当天继续追加普通 CSV；保留最近 7 个自然日（含当天）。例如 9 月 14 日
  保留 9 月 8 日至 14 日，9 月 7 日及更早日期可归档。
- `minute_archive_compression.py` 默认只列候选；`--apply` 才生成 gzip 并移除
  相应 CSV。只处理日期子目录中的 `minute_quotes.csv`，不处理其他 CSV。
- 超出保留窗口但最近一小时仍被修改的文件跳过。未来日期、当天及保留窗口
  不可压缩，`--keep-days` 最小为 1。
- 写入、读取和压缩使用同一天的 `.minute_quotes.lock`。压缩任务遇到锁占用
  跳过该日期，其他日期继续；压缩不会在采集线程中执行。
- 在同目录临时文件中压缩，解压后计算 SHA-256 和字节数，并重新读取原文件
  比较；检查 inode、大小、修改时间等不变，原子发布 gzip、写入校验回执后
  才移除原 CSV。压缩包默认权限 0600，临时文件自动清理。
- 两种文件同时存在时，读取优先 CSV；压缩仅在双方完整内容一致时移除 CSV。
  冲突、损坏、文件变化及落盘失败不会主动丢弃原始内容。
- gzip 文件为 `minute_quotes.csv.gz`；回执为 `minute_quotes.archive.json`。
  已归档日期不允许悄悄新建只含后续片段的 CSV。
- 共享读取接口 `open_minute_quotes(root, day)` 兼容普通 CSV 和 gzip，已接入
  Sina 分钟补传读取路径。补传仍只处理请求时刻的当日，不能将旧日期传给
  普通当日补传 API 当作历史恢复方案。

## 本地用法

```sh
python3 scripts/minute_archive_compression.py --root /path/to/intraday_minute_store
python3 scripts/minute_archive_compression.py --root /path/to/intraday_minute_store --day 20260709 --apply
```

压缩档案可以用 `gzip -dc minute_quotes.csv.gz > /另一个目录/minute_quotes.csv`
导出临时 CSV 给旧工具使用。导出后按回执核对 SHA-256。不要直接编辑压缩包，
也不要把其文件名改成 `.csv`。恢复到原目录前需停用维护任务、确认相关日期
没有写入，并在内容校验完成后移走 `.gz`；否则写入保护会拒绝追加。

## 本次部署

源码同时保存在：

- `/Users/ellis/newnavnav/scripts/`
- `/Users/ellis/工具程序开发/machome五合一_v1.1.0_本地IOPV与L1保留版/app/components/upload/business/scripts/`

machome 的应用内来源目录与 `business-run/scripts` 同步更新，运行脚本校验表、
应用源码清单及 `minute-archive-patch.json` 已记录新哈希，应用重新签名。
只重启 Sina worker，由现有 Upload supervisor 接管；其他任务没有主动重启。

实际维护任务：`~/Library/LaunchAgents/com.newnavnav.minute-archive-compression.plist`。
每天本机时间 18:10 执行（machome 当前为上海时区），使用应用自带 Python 运行时，
低优先级后台运行。用户需处于已登录状态，机器休眠等情况遵循 launchd 的行为。
日志位于 `~/Library/Application Support/MachomeHub/data/upload/logs/minute-archive-compression.log`。

自动维护根目录固定为：
`~/Library/Application Support/MachomeHub/data/upload/business-run/scripts/intraday_minute_store`。
旧 `~/NAVNAV/scripts/intraday_minute_store` 不纳入定时任务；本次仅对其 20260709
做了归档验收。旧目录 20260907 与正式副本内容不同，未修改或合并。

部署前备份与验收回执：
`~/Library/Application Support/MachomeHub/backups/minute-archive-20260914-140637/`。
`deploy/minute_archive_install.py` 保存了本次定向部署流程，使用精确旧哈希保护；
再次升级时必须重新采集当前基线和测试，不得直接复用旧 expected.json。

## 验证

本地与 machome 自带运行时各通过 27 项测试：原有采集 3 项、重连及捕获时序
10 项、新增压缩 14 项。新增测试覆盖字节级往返、中文与 CSV 换行、偏移续读、
保留窗口、重复运行、锁竞争、源文件变化、压缩包冲突/损坏、发布失败及软链接。

20260709 新旧两份各 79,705,032 字节，分别压缩为 15,778,833 字节，校验 SHA-256
均为 `368c042d3e87fbc2e79e81c4dee68d8ded954452d5da3252884c1d59d773b720`。
每份节省 80.2%，两份合计减少约 121.9 MiB。每日批量定时任务已安装，
本次尚未等待首次 18:10 的自动触发。

部署后 14:09:02 已观察到当天 CSV 增长至 67,551,730 字节；14:09:00 的增量
补传返回 rows=133、accepted=72，行情推送继续。读取压缩档案得到 286,315 行。
全目录预览还有 67 个候选日期，未提前执行全目录压缩；20 个日期目录没有目标
文件，5 个日期处于保留窗口。详见 `minute-archive-deployment-20260914/acceptance.json`。

GitNexus 本地索引存在数据库引擎版本不兼容；已执行 impact，但其结果为 UNKNOWN。
随后使用 codebase-memory 图谱追踪实际写入/补传调用链，并与当前源码核对。
本次没有提交或推送 Git commit，其他未提交修改未包含在部署中。
