# B 组最终报告｜科技互联网汽车

更新时间：2026-09-06（Asia/Shanghai）

## 结论

B 组负责 81 只基金。本轮没有基金达到可采用的 `SUITABLE_PRICE_PROXY` 或 `NONE_IN_TESTED_SET` 门槛；81/81 暂列 `INSUFFICIENT_EVIDENCE`。这不是“低相关”结论，而是新确认窗口、价格覆盖、公司行动和执行证据未闭环后的保守门控结果。

其中 16 只基金有归档的旧窗口技术结果，已导入 `model_metrics`，但全部标为 `REUSED_OR_UNPROVEN`；其数值仅作探索参考，不能作为 2026-08-04 至 2026-09-04 的确认结果。520600 的旧样本复现结果可复核，例如 30 分钟固定 HHI+HTI 的旧样本 VR 为 0.7228、残差标准差 19.43bp，但不改变本轮结论。

## 新窗口证据

520600 的官方产品资料确认其为上交所上市、标的为中证港股通汽车产业主题的基金；21 个官方 PCF 页面已归档并解析，每日 50 行。HSIU6、HHIU6、HTIU6 已通过 IBKR 只读合约详情确认，并抓取了新窗口期货历史数据。

阻断发生在基金与 PCF 成分的新增价格端点：Eastmoney 5 分钟接口出现远端关闭/覆盖不足，Tencent 替代接口无法完整覆盖港股成分。由于没有完整共同 1 分钟端点，不能证明至少 20 个有效新外测日，也不能完成严格重拟合、95% 端点覆盖、逐证券事件核验、取整头寸或成本评估。

## 交付统计

| 项目 | 数量/状态 |
|---|---:|
| 分配基金 | 81 |
| 最终 `INSUFFICIENT_EVIDENCE` | 81 |
| 归档旧技术结果 | 16 |
| 官方 PCF 日期 | 21 |
| 每日 PCF 解析行 | 50 |
| 候选工具记录 | 648（81×8） |
| 新窗口主方案 | 0 |

## 复现入口

旧样本复现命令：

```bash
cd "/Users/ellis/工具程序开发/港股通相关性分析"
.venv/bin/python "outputs/hedge_selection_v2_20260906/agent_B/scripts/run_pilot.py" \
  --config "outputs/hedge_selection_v2_20260906/agent_B/config/repro_520600_old.json"
```

机器表由 `scripts/build_agent_b_tables.py` 生成；结构检查：

```bash
cd "/Users/ellis/工具程序开发/港股通相关性分析/outputs/hedge_selection_v2_20260906"
python3 control/check_delivery.py --owner B
```

`selection_lock.json` 的状态是 `REUSED_OR_UNPROVEN`。后续只有在补齐基金/成分 1 分钟价格、PCF 数量映射、公司行动证据、费用/借券证据后，才可重做 P2–P6 并重新锁定主方案。
