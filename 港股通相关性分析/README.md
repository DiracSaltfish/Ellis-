# 港股通 ETF 对冲研究

2026-09-06 已完成 520600 首轮真实数据试跑，状态为 **PASS_RISK_ONLY_WITH_CAVEATS**。

- [520600 结果](reports/520600_首轮结果.md)：37 个样本外日，分钟价格、强制现金替代和沪港通结算汇率口径。
- [工作流 v1.0](docs/STANDARD_WORKFLOW.md)：已验证步骤、执行命令和低消耗 Agent 任务卡。
- [研究 Notebook](notebooks/520600_research.ipynb)：5 个代码单元已顺序执行。
- [最初数据盘点](docs/DATA_AUDIT.md)：保留为阶段一历史记录；其中“等待补全”等状态已被本轮工作流和用户后续口径取代。
- `config/pilot.json`：锁定口径、已运行范围及当前状态；不是完整的通用引擎配置接口。

核心结果：30分钟 HHI＋HTI 将篮子波动从36.91bp降至19.43bp，方差下降72.3%；HTI单腿20.03bp。费用未纳入，不能解读为净套利收益。

## 环境与缓存复跑

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-research.txt
.venv/bin/python scripts/run_pilot.py --cached
.venv/bin/python scripts/validate_pilot.py
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/build_deliverables.py
```

修改原始数据后运行不带 `--cached` 的 `run_pilot.py`。

## 重新抽取数据

```bash
ssh -o BatchMode=yes machome 'python3 -' < scripts/extract_pilot_remote.py > data/raw/pilot_520600.jsonl.gz.tmp
python3 -c "import gzip,json; print(sum(1 for line in gzip.open('data/raw/pilot_520600.jsonl.gz.tmp','rt') if json.loads(line)))"
mv data/raw/pilot_520600.jsonl.gz.tmp data/raw/pilot_520600.jsonl.gz
.venv/bin/python scripts/collect_hti.py
.venv/bin/python scripts/fetch_corporate_actions.py
.venv/bin/python scripts/run_pilot.py
```

远端根路径 `/Volumes/EllisFiles/Stocksdata`；原始数据只读。沪港通结算率及HSI/HHI来自 `/Users/ellis/newnavnav/港股通汇率研究`，具体源文件及校验见 `data/raw/local_source_manifest.json`。
HTI只读请求连接本机TWS 127.0.0.1:7496；每块请求落盘可断点续跑，不查询账户或发送订单。

## 后续复用

先让较低消耗Agent复跑520600基准，再为其他基金建立独立目录和配置化适配；当前脚本明确锁定520600和已验证窗口，不能只改配置文件就宣称完成其他标的回测。
源数据、规范化面板不进入版本控制；代码、统计结果和文档留在本工程。
