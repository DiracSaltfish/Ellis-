# Source Generated with Decompyle++
# File: integration_test.pyc (Python 3.12)

'''
组合优化模块集成测试

测试完整流水线:
    因子收益求解 → 协方差调整 → 特质风险调整 → 组合优化

运行方式:
    cd D:/AmazingData/AmazingData
    python portfolio_optimization/integration_test.py
'''
import sys
import os
sys.path.insert(0, 'D:\\AmazingData')
sys.path.insert(0, 'D:\\AmazingData\\AmazingData')
import numpy as np
import pandas as pd
from datetime import datetime
exec(open(os.path.join(os.path.dirname(__file__), 'optimization_constant.py'), encoding = 'utf-8').read())
exec(open(os.path.join(os.path.dirname(__file__), 'utils.py'), encoding = 'utf-8').read())
exec(open(os.path.join(os.path.dirname(__file__), 'factor_return_solver.py'), encoding = 'utf-8').read())
exec(open(os.path.join(os.path.dirname(__file__), 'covariance_adjuster.py'), encoding = 'utf-8').read())
exec(open(os.path.join(os.path.dirname(__file__), 'specific_risk_adjuster.py'), encoding = 'utf-8').read())
exec(open(os.path.join(os.path.dirname(__file__), 'portfolio_optimizer.py'), encoding = 'utf-8').read())

def run_integration_test():
    '''完整流水线集成测试'''
    np.random.seed(42)
    print('======================================================================')
    print('  组合优化模块 - 集成测试')
    print('  流水线: 因子收益求解 → 协方差调整 → 特质风险调整 → 组合优化')
    print('======================================================================')
    print('\n======================================================================')
    print('  步骤0: 构造模拟数据')
    print('======================================================================')
    n_dates = 252
    n_stocks = 200
    n_factors = 5
    n_industries = 10
    dates = pd.date_range('2023-01-01', periods = n_dates, freq = 'B')
# WARNING: Decompyle incomplete

if __name__ == '__main__':
    results = run_integration_test()
    return None
