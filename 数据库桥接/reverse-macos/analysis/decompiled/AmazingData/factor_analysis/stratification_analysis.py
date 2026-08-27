# Source Generated with Decompyle++
# File: stratification_analysis.pyc (Python 3.12)

__doc__ = '\n分层法分析模块\n\n将股票按因子值排序后等分为 N 组（默认 5 组），每组用向量化回测计算绩效，\n检验因子的区分度和单调性。\n\n依赖: VectorizedBacktest（向量化回测引擎）\n'
import numpy as np
import pandas as pd
from typing import Dict, List, Optional
from AmazingData.factor_analysis.vectorized_backtest import VectorizedBacktest

class StratificationAnalysis:
    '''
    分层法单因子分析。

    使用示例:
        sa = StratificationAnalysis(factor_df, close_price_df, group_num=5)
        sa.run()
        print(sa.summary())   # 各组指标汇总表
        print(sa.group_navs)  # 各组净值
    '''
    
    def __init__(self, factor = None, close_price = None, group_num = None, ascending = (5, True, None), benchmark = ('factor', pd.DataFrame, 'close_price', pd.DataFrame, 'group_num', int, 'ascending', bool, 'benchmark', Optional[pd.Series])):
        '''
        :param factor: 因子值, index=日期, columns=股票代码
        :param close_price: 收盘价, index=日期, columns=股票代码
        :param group_num: 分组数量，默认 5
        :param ascending: True=因子值小→大, False=大→小
        :param benchmark: 基准净值序列（可选）
        '''
        self.factor = factor
        self.close_price = close_price
        self.group_num = group_num
        self.ascending = ascending
        self.benchmark = benchmark
    # WARNING: Decompyle incomplete

    
    def run(self = None, market_cap = None, calc_signal_decay = None, decay_periods = (None, True, 10)):
        '''
        执行分层回测。

        :param market_cap: 市值数据（用于板块分析）
        :param calc_signal_decay: 是否计算信号衰减与反转
        :param decay_periods: 信号衰减计算周期数
        '''
        self._backtest = VectorizedBacktest(factor = self.factor, close_price = self.close_price, group_num = self.group_num, ascending = self.ascending, benchmark = self.benchmark)
        self._backtest.run(market_cap = market_cap)
        if calc_signal_decay:
            self._backtest.calc_signal_decay_reversal(decay_periods)
        return self

    group_navs = (lambda self = None: pass# WARNING: Decompyle incomplete
)()
    group_returns = (lambda self = None: pass# WARNING: Decompyle incomplete
)()
    group_metrics = (lambda self = None: pass# WARNING: Decompyle incomplete
)()
    turnover = (lambda self = None: pass# WARNING: Decompyle incomplete
)()
    signal_decay = (lambda self = None: pass# WARNING: Decompyle incomplete
)()
    signal_reversal = (lambda self = None: pass# WARNING: Decompyle incomplete
)()
    long_short_nav = (lambda self = None: pass# WARNING: Decompyle incomplete
)()
    
    def summary(self = None):
        '''各组指标汇总表'''
        pass
    # WARNING: Decompyle incomplete

    
    def monotonicity_test(self = None):
        """
        检验分组收益的单调性。

        如果因子有效，从 G0 到 GN 的收益应该单调递减（或递增）。

        :return: {'monotonic': bool, 'rank_corr': float, 'group_returns': list}
        """
        pass
    # WARNING: Decompyle incomplete

    
    def save(self = None, path = None, factor_name = None):
        '''保存分层分析结果'''
        pass
    # WARNING: Decompyle incomplete


# WARNING: Decompyle incomplete
