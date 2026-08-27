# Source Generated with Decompyle++
# File: ma10_portfolio_optimization.pyc (Python 3.12)

__doc__ = '\nMA10 因子组合优化脚本\n\n使用 AmazingData TGW API 获取真实 A 股数据，以 MA10（10日均价）作为因子，\n执行完整的因子预处理 + 组合优化流水线，输出最优权重。\n\n流程:\n    1. TGW 登录并获取数据（K线、股本结构、行业成分股）\n    2. 计算 MA10 因子并做因子预处理\n    3. 执行组合优化四步流水线\n    4. 输出最优权重和风险分解报告\n\n运行方式:\n    D:\\ProgramData\x07naconda313\\python.exe ma10_portfolio_optimization.py\n'
import os
import sys
import time
import numpy as np
import pandas as pd
# WARNING: Decompyle incomplete
