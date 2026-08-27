# Source Generated with Decompyle++
# File: specific_risk_adjuster.pyc (Python 3.12)

__doc__ = '\n特质风险方差矩阵调整器\n\n实现特质风险方差矩阵的四步调整流水线：\n    1. Newey-West 时序相关性修正\n    2. 结构化调整（同因子暴露同风险假设）\n    3. 贝叶斯压缩调整（按市值分组回归均值）\n    4. 波动率偏误调整（截面整体缩放）\n\n注意：特质方差矩阵为对角阵，非对角元素为 0。\n\n使用示例:\n    adjuster = SpecificRiskAdjuster(specific_return, factor_loadings, market_value)\n    adjuster.cal_newey_west(max_lags=5)\n    adjuster.cal_structural_adjustment()\n    adjuster.cal_bayesian_shrinkage(n_groups=10)\n    adjuster.cal_vol_regime_adjustment()\n    # 或一键执行\n    adjuster.run_pipeline()\n    delta = adjuster.final_delta  # 特质方差对角向量\n'
import numpy as np
import pandas as pd
from typing import Dict, Optional
# WARNING: Decompyle incomplete
