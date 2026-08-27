# Source Generated with Decompyle++
# File: factor_return_solver.pyc (Python 3.12)

__doc__ = "\n风险因子收益率求解器\n\n基于结构化风险模型框架，通过 OLS/WLS 回归估计因子收益率 f̂。\n支持国家因子约束，以及特质收益率估计。\n\n使用示例:\n    solver = FactorReturnSolver(stock_return, factor_loadings, market_value, industry_dummies)\n    solver.cal_factor_return(method='wls', weight_method='float_value_inverse')\n    f_hat = solver.factor_return  # 因子收益率 DataFrame\n    solver.cal_specific_return()\n    u = solver.specific_return  # 特质收益率 DataFrame\n"
import numpy as np
import pandas as pd
from typing import Dict, Optional, Union
from datetime import datetime
# WARNING: Decompyle incomplete
