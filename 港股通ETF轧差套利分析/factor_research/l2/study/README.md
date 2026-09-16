# L2方向与数量研究运行说明

主结果：[L2方向与数量回测报告.md](L2方向与数量回测报告.md)。

可以直接查看 `净申赎预测明细_1445_测试集.csv`：每一行是一个基金日，含净申购/零变化/净赎回概率、预测份额、实际份额、篮子数及名义80%区间。`最近历史日_20260427_1445.csv` 是本次最新历史日，不能视作今天的预测。

代码与全部衍生结果在本目录；大体积行情源码继续在 machome 硬盘。计算均在 machome 的项目 `.venv/bin/python` 运行，使用已有2026数据，无2025回补。本次研究不修改或删除原始行情/归档。

```sh
cd /Users/ellis/工具程序开发/港股通ETF轧差套利分析
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 .venv/bin/python factor_research/l2/study/build_dataset.py
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 .venv/bin/python factor_research/l2/study/analyze.py
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 .venv/bin/python factor_research/l2/study/validate_study.py
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 .venv/bin/python factor_research/l2/study/report.py
```

`build_dataset.py` 的 date_snapshot.json 只在首次运行创建，按日缓存位于 daily/，保持本次可复现性。**修改因子或数据后，不要沿用旧缓存训练**；应在 l2/ 下创建新的同级研究目录，复制研究脚本、生成新的输入快照和缓存。未来测试日期也应为尚未参与调参/筛选的新增日期。本次测试结果不反过来调整本次参数。

模型是 sklearn HistGradientBoosting、温度校准和数量区间校准的可信本地 joblib 文件，模型之间按14:30/14:45和是否含L2区分。只使用本项目生成的可信模型文件。

```sh
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 .venv/bin/python factor_research/l2/study/score.py \
  --model factor_research/l2/study/models/1445_premium_l2.joblib \
  --input factor_research/l2/study/holdout_features_1445.parquet \
  --output factor_research/l2/study/score_cli_verified.csv
```

示例输入来自测试期历史特征，已去掉当日真实标签。新输入需要相同特征定义和截断时点、当日PCF单位、前日份额、质量标志；预测日期必须晚于模型拟合及校准日期。QMT获取、接收延迟、实时FX代理的可知时间验证按用户要求留待后续。

分类目标是净份额变化的正/零/负；回归目标是净份额/前日份额×100后的asinh变换。反变换点估计不等于经过校准的期望净份额。区间也不能解释为套利盈亏区间。

原始文件CRC在冻结manifest内，原始网站份额缓存、PCF篮子、估值序列和源代码SHA256在 source_receipt.json。validation.json 验证时间分割、标签、数量单位、保存模型重放及未来日期/质量阻断；预测准确率另见 metrics.json。
