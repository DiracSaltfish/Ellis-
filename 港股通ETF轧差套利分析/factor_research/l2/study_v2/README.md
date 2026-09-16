# 港股通ETF净申赎模型 v2

**验收状态：研究版本未通过替换标准，原模型保留。** 详见[其他日期验收报告](/Users/ellis/工具程序开发/港股通ETF轧差套利分析/factor_research/l2/study_v2/模型升级与其他日期验收报告.md)。

模型预测基金当日最终**净**份额变化，成交证据不能证明一级申赎身份。QMT实时传输及下单仍未接入。

## 数据与时间范围

62个已验证日期：2026-01-05至2026-04-27。训练截至02-27，验证03-02至03-13，测试03-16以后。9月2日整日排除，仅用于冻结选型后的案例复查；15个03-16至04-08的新测试日期与4个旧04-22至04-27测试日期分开报告。1月15日、4月3日、4月7日缺少现成估值序列，未纳入。

输入使用截止14:30/14:45前的L2，以及所有已完成的共同交易分钟；最终当日汇率不进入预测。份额标签为当日增减，不向后错位。历史接收时间不可得，前一日结算汇率可用性存在假设。

## 升级内容

C++按订单累计整U原始量/已执行量、主被动执行、撤单、剩余量、严格整数与替代单位背景。Python生成32个新因子，和原33个因子联合建模；成交数量以昨日份额归一化，买卖两侧分别保留。PCF缺失限额为NaN加未知标记，不以0代替。

比较原冻结模型、在新训练范围重训的旧因子控制组、新因子直接回归、以及新因子两阶段模型。两阶段模型先分类，再分别对正/负非零净增幅拟合Poisson损失回归，按校准分类概率加权得到净量点估计。使用Poisson损失建模正条件均值，不宣称净增幅服从计数分布。

在两个新因子模型间仅按验证集U单位MAE选择，测试不参与选择。阈值仅由验证集选择：至少30条、5天、5基金，净申购精确率至少90%，取覆盖最多的阈值；不满足则无入选阈值。若汇差或换手超出训练范围，候选标志抑制。另独立报告无该范围筛选的固定p≥90%统计。候选标志不是交易建议或盈利保证。

预测区间使用验证集绝对净增幅残差的有限样本80%分位校准，适用性通过测试覆盖率检查，不是单样本80%可信承诺。

## 复跑（machome项目根目录）

```sh
.venv/bin/python factor_research/l2/study_v2/build_dataset.py
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 .venv/bin/python factor_research/l2/study_v2/train.py
.venv/bin/python factor_research/l2/study_v2/case_check.py
.venv/bin/python factor_research/l2/study_v2/tests.py
.venv/bin/python factor_research/l2/study_v2/validate.py
```

`plan.json`冻结日期和源指纹，日数据断点续算。`models/1430_selected.joblib`、`1445_selected.joblib`为验证集选出的升级版本，不覆盖v1。仅加载本项目可信joblib文件。

## 评分入口

先通过同一特征函数构建数据，输入一份与模型截止时点一致、日期晚于校准期的CSV或Parquet。必需字段与模型特征名可从bundle读取；标准输入示例为`case_features_1445.csv`。

```sh
.venv/bin/python factor_research/l2/study_v2/score_cli.py --model factor_research/l2/study_v2/models/1445_selected.joblib --input factor_research/l2/study_v2/case_features_1445.csv --output factor_research/l2/study_v2/example_score.csv
```

输出净申购/净赎回/不变概率、净量点估计与区间（百分比、份、U）、两阶段条件数量（如适用）、范围外标记及研究候选标志。价格和L2缺失、日期泄漏、错误截止时点会拒绝评分。
