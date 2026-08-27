# Source Generated with Decompyle++
# File: vectorized_backtest.pyc (Python 3.12)

__doc__ = '\n向量化回测引擎\n\n核心思路: 用矩阵运算替代逐日循环调仓，大幅提升分层回测速度。\n\n方法:\n    1. 构建权重矩阵 W(T×N): 每行一个日期, 每列一只股票, 值为 0 或 1/group_size\n    2. 收益率矩阵 R(T×N): T+1 期股票收益率\n    3. 组日收益率 = (W * R).sum(axis=1)  → 向量化点乘\n    4. 组净值 = (1 + 日收益率).cumprod()\n\n同时支持:\n    - 换手率分析 (个数法 + 权重法)\n    - 买入信号衰减与反转\n    - 板块分析 (市值均值/行业占比)\n    - 分组净值指标 (年化收益/波动率/夏普/回撤等)\n\n复杂度: O(T×N) 替代原逐日循环的 O(T×N×G)\n'
import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Tuple

class VectorizedBacktest:
    '''
    向量化分层回测引擎。

    使用示例:
        vb = VectorizedBacktest(factor_df, close_price_df, group_num=5)
        vb.run()
        # 获取结果
        vb.group_navs       # 各组净值 DataFrame
        vb.group_metrics    # 各组绩效指标
        vb.turnover         # 换手率
    '''
    
    def __init__(self, factor = None, close_price = None, group_num = None, ascending = (5, True, None), benchmark = ('factor', pd.DataFrame, 'close_price', pd.DataFrame, 'group_num', int, 'ascending', bool, 'benchmark', Optional[pd.Series])):
        '''
        :param factor: 因子值, index=日期, columns=股票代码
        :param close_price: 收盘价, index=日期, columns=股票代码
        :param group_num: 分组数量
        :param ascending: True=因子值小→大（组0最小）, False=因子值大→小
        :param benchmark: 基准净值序列（可选）
        '''
        self.factor = factor
        self.close_price = close_price
        self.group_num = group_num
        self.ascending = ascending
        self.benchmark = benchmark
        common_dates = factor.index.intersection(close_price.index)
        common_stocks = factor.columns.intersection(close_price.columns)
        self.factor = factor.loc[(common_dates, common_stocks)]
        self.close_price = close_price.loc[(common_dates, common_stocks)]
    # WARNING: Decompyle incomplete

    
    def run(self = None, market_cap = None):
        '''
        执行向量化回测。

        :param market_cap: 市值数据（可选，用于板块分析）, index=日期, columns=股票代码
        '''
        pass
    # WARNING: Decompyle incomplete

    
    def _calc_turnover(self):
        '''计算各组的个数法换手率和权重法换手率'''
        turnover_data = { }
        for g in self.group_labels:
            gk = self.group_keys[g]
            weights = self.weight_matrices[gk]
            in_group = (weights > 0).astype(int)
            changed = ((in_group.diff().abs() > 0).sum(axis = 1) / in_group.sum(axis = 1).replace(0, np.nan)) * 100
            turnover_data[f'''{gk}_count'''] = changed
            weight_change = (weights.diff().abs().sum(axis = 1) / 2) * 100
            turnover_data[f'''{gk}_weight'''] = weight_change
        self.turnover = pd.DataFrame(turnover_data)

    
    def calc_signal_decay_reversal(self = None, decay_periods = None):
        '''
        计算买入信号衰减和反转。

        - 衰减: 当前 G1 组股票在后续调仓期中仍在 G1 的比例
        - 反转: 当前 G1 组股票在后续调仓期中变为 G5 的比例
        '''
        pass
    # WARNING: Decompyle incomplete

    
    def _calc_sector_analysis(self = None, market_cap = None, group_labels = None):
        '''计算各组市值均值'''
        mc = market_cap.reindex(index = self.factor.index, columns = self.factor.columns)
        cap_means = { }
        for g in self.group_labels:
            gk = self.group_keys[g]
            in_group = group_labels == g
            cap_in_group = mc[in_group]
            cap_means[gk] = cap_in_group.mean(axis = 1)
        self.group_market_cap = pd.DataFrame(cap_means)

    
    def get_long_short_nav(self = None):
        '''多空组合净值: 做多 G0 + 做空 GN'''
        pass
    # WARNING: Decompyle incomplete

    
    def get_long_short_metrics(self = None):
        '''多空组合绩效指标'''
        ls_nav = self.get_long_short_nav()
        if len(ls_nav) < 2:
            return { }
        NetValueAnalyzer = NetValueAnalyzer
        import AmazingData.factor_analysis.regression_analysis
        analyzer = NetValueAnalyzer(ls_nav, self.benchmark)
        return analyzer.analyze()

    
    def summary(self = None):
        '''返回各组关键指标汇总表'''
        if not self.group_metrics:
            return pd.DataFrame()
        rows = None
        for gk in self.group_keys:
            m = self.group_metrics.get(gk, { })
            rows.append({
                'group': gk,
                'annual_return': m.get('annual_return', 0),
                'annual_volatility': m.get('annual_volatility', 0),
                'sharpe_ratio': m.get('sharpe_ratio', 0),
                'max_drawdown': m.get('max_drawdown', 0),
                'calmar_ratio': m.get('calmar_ratio', 0),
                'win_rate': m.get('win_rate', 0) })
        ls = self.get_long_short_metrics()
        rows.append({
            'group': 'long_short',
            'annual_return': ls.get('annual_return', 0),
            'annual_volatility': ls.get('annual_volatility', 0),
            'sharpe_ratio': ls.get('sharpe_ratio', 0),
            'max_drawdown': ls.get('max_drawdown', 0),
            'calmar_ratio': ls.get('calmar_ratio', 0),
            'win_rate': ls.get('win_rate', 0) })
        return pd.DataFrame(rows).set_index('group')


# WARNING: Decompyle incomplete
