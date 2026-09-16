# 网站历史估值回写与日份额展示

2026-09-12 已部署到 http://192.168.1.113:18680。

## 完成情况

- 新增32,447个基金日、10,707,510个历史分钟点，覆盖191只基金，日期范围2025-07-02至2026-06-30。
- 现有369个基金日压缩档案逐块SHA-256校验，全部保留。
- 导入64,933条日份额记录，来源为前轮保存的1navs历史份额原始响应，最新至2026-09-11。
- 页面在日期工具栏下显示当日净份额变化（万份）及当日总份额；正负数和真实零值正常显示，缺失显示“暂无数据”。
- 重算历史显示“历史重算 · 当日最终汇率”，买/卖方向估值及溢价率均可切换。已有实采历史保留原来的预估结算说明。
- 历史PCF信息取所选日期，不再用当日PCF状态代替。日期接口取消仅返回最近120个日期的限制。

验证样例：159125.SZ在2026-06-30为-100.00万份、2026-09-10为0.00万份；159570.SZ在2025-09-19为+700.00万份。

## 数据口径

输入为 `factor_research/results/series`、`inputs/baskets` 和 `inputs/share_history`。重建/排除规则继承首轮研究，不额外填补不完整PCF或缺失成分。此轮没有扩展2026年7—8月的行情重建。

研究面板将源分钟标签加一作为可用时刻；网站展示时减回这一分钟，恢复原始分钟标签。只保存有完整三种估值的分钟，最后至16:00；午休及15点后不生成ETF价或溢价。网站保留原有截至16:08的坐标轴，但不会虚构16:01—16:08的行情。

存储兼容网站IOPZ1压缩协议：ETF价格3位小数、IOPV4位小数，溢价由网站按存储价格重算，单位为百分数。历史重算不提供五档行情、不进入实时信号，也不声称已核实当时停牌状态。盘后最终汇率固定全天，不代表当时盘中可获得的结算率预测。

1navs份额日期T即T日净变化，不平移；日份额数据独立于分钟估值，所以即使某天没有完整分钟，也可显示其净份额变化。当前导入的是已缓存历史，并未新增未来每日自动抓取任务。

## 程序与位置

本机网站源代码：`/Users/ellis/工具程序开发/machome五合一_v1.1.0_本地IOPV与L1保留版/services/iopv`。

machome网站服务：`/Users/ellis/Applications/Machome 四合一运行中心.app/Contents/Helpers/machome-iopv-server`。

实际数据库：`/Users/ellis/Library/Application Support/MachomeHub/data/premium/local-iopv/data/iopv.sqlite`（machome）。

研究目录：`/Users/ellis/工具程序开发/港股通ETF轧差套利分析/website_backfill`，本机及machome同名。

- `import_history.py`：构造中间数据库；`--apply`先备份，再只插入不存在的基金日；日份额按来源更新时间更新。
- `test_import.py`：验证已有压缩档案、旧格式分钟都不会覆盖，重复运行不会重复写入，零份额变化不会丢失。
- `verify_site.py`：通过正式网站接口逐分钟核对六个沪深基金日期样例的全部价格、三组溢价率、日期与份额。
- `verification.json`：接口核对结果。
- `stage_summary.json`、`import-*.json`：覆盖数量和插入清单、每块摘要。
- `source_patch/`：本轮修改文件的审计快照。`tracked-changes.patch`不含新建Go文件，不能单独当作完整部署补丁；部署维护应以当前网站源目录为准，避免覆盖其他任务更新。
- `modify_site.py`：开发期一次性修改记录，不是重复运行的安装脚本。

### 在machome复核或重复导入

```sh
cd /Users/ellis/工具程序开发/港股通ETF轧差套利分析
.venv/bin/python website_backfill/test_import.py
.venv/bin/python website_backfill/verify_site.py
# 必要时重复导入：已有日期会跳过，且会重新生成备份
.venv/bin/python website_backfill/import_history.py --apply '/Users/ellis/Library/Application Support/MachomeHub/data/premium/local-iopv/data/iopv.sqlite'
```

只有初次生成时运行 `--stage`；已有staged.sqlite时脚本主动退出，避免覆盖审计输入。

## 备份与验证

回写前的在线一致性SQLite备份在machome：`website_backfill/backup-20260912-135103.sqlite`。旧服务程序保留为 `server-before-20260912-134917`；布局微调前另有 `server-before-layout-fix`。不要直接用整库备份覆盖以后新增的采集数据；若撤销本次导入，应按插入清单及摘要逐项撤销。

已通过相关Go服务测试、JS语法检查、导入幂等与不覆盖测试、数据库quick_check、既有档案摘要核对，以及正式API六个基金日共1,980个分钟的逐项比较。浏览器已核对原有2026-09-10图、重算2026-06-30图、份额变化、估值/溢价率及买卖方向切换。
