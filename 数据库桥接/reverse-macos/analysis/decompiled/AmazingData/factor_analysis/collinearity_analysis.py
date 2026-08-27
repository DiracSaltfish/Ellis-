# Source Generated with Decompyle++
# File: collinearity_analysis.pyc (Python 3.12)

__doc__ = '\n因子共线性分析模块\n\n三种检测方法:\n    1. 相关系数矩阵\n    2. 方差膨胀因子 VIF (>10 认为严重共线)\n    3. 条件数 (>30 认为存在共线)\n'
import numpy as np
import pandas as pd
from statsmodels.api import api as sm
from typing import Dict, List, Optional

class CollinearityAnalysis:
    '''
    因子共线性检测。

    使用示例:
        ca = CollinearityAnalysis(factor_dict)
        ca.cal_collinearity()
        print(ca.vif)       # VIF DataFrame
        print(ca.condition_num)  # 条件数 Series
    '''
    
    def __init__(self = None, factor_data = None):
        '''
        :param factor_data: {factor_name: DataFrame(index=日期, columns=股票代码)}
        '''
        self.factor_data = factor_data
        self.factor_names = list(factor_data.keys())
        all_dates = factor_data[self.factor_names[0]].index
        for name in self.factor_names[1:]:
            all_dates = all_dates.intersection(factor_data[name].index)
        self.dates = all_dates
        self.relation = None
        self.vif = None
        self.condition_num = None

    
    def cal_collinearity(self = None):
        '''计算所有共线性指标'''
        vif_data = { }
        cond_data = { }
        corr_list = []
    # WARNING: Decompyle incomplete

    
    def summary(self = None):
        '''汇总共线性检测结果'''
        result = { }
    # WARNING: Decompyle incomplete


# WARNING: Decompyle incomplete
