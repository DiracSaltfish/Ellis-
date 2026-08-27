# Source Generated with Decompyle++
# File: ic_analysis.pyc (Python 3.12)

__doc__ = '\nIC 分析模块\n\nIC (Information Coefficient) 是指因子在第 T 期的暴露度与 T+N 期股票收益的相关系数。\n\n核心功能:\n    1. IC 衰减计算 — 计算 delay_1 到 delay_N 的 IC 序列\n    2. IC 评价指标 — 12 个统计指标（均值、IR、>0 占比等）\n\n加速优化:\n    - 预分配 DataFrame + iloc 赋值，替代逐行 pd.concat\n    - 收益矩阵预计算所有 delay 周期\n'
import numpy as np
import pandas as pd
from scipy import stats
from typing import Tuple, Dict

class IcAnalysis:
    """
    IC 分析类。

    使用示例:
        ia = IcAnalysis(factor_df, factor_name, close_price_df, ic_decay=20)
        ia.cal_ic_df(method='spearmanr')
        ia.cal_ic_indicator()
        result = ia.ic_result  # 12 个指标的 DataFrame
    """
    
    def __init__(self = None, factor = None, factor_name = None, market_close_data = (20,), ic_decay = ('factor', pd.DataFrame, 'factor_name', str, 'market_close_data', pd.DataFrame, 'ic_decay', int)):
        '''
        :param factor: 因子值 DataFrame, index=日期, columns=股票代码
        :param factor_name: 因子名称
        :param market_close_data: 收盘价 DataFrame, index=日期, columns=股票代码
        :param ic_decay: IC 衰减周期数，默认 20
        '''
        self.factor = factor
        self.factor_name = factor_name
        self.ic_decay = ic_decay
        common_dates = factor.index.intersection(market_close_data.index)
        common_stocks = factor.columns.intersection(market_close_data.columns)
        self.factor = factor.loc[(common_dates, common_stocks)]
        market_data = market_close_data.loc[(common_dates, common_stocks)]
        self.column_prefix = 'delay_'
    # WARNING: Decompyle incomplete

    
    def cal_ic_df(self = None, method = None):
        """
        计算 IC 衰减序列。

        :param method: 'spearmanr' (RankIC) 或 'pearsonr' (普通 IC)
        :return: (ic_df, p_value_df)
        """
        n = len(self.factor)
        for idx in range(n):
            for ic_decay in range(self.ic_decay):
                corr = np.nan
                p_value = np.nan
                if idx + ic_decay + 1 < n:
                    factor_row = self.factor.iloc[idx].dropna()
                    return_row = self.stock_return_dict[ic_decay + 1].iloc[idx + ic_decay + 1].dropna()
                    common = factor_row.index.intersection(return_row.index)
                    if len(common) > 2:
                        x = factor_row[common].sort_index().values
                        y = return_row[common].sort_index().values
                        if method == 'spearmanr':
                            (corr, p_value) = stats.spearmanr(x, y)
                        elif method == 'pearsonr':
                            (corr, p_value) = stats.pearsonr(x, y)
                col = self.column_prefix + str(ic_decay + 1)
                self.ic_df.iloc[(idx, ic_decay)] = corr
                self.p_value_df.iloc[(idx, ic_decay)] = p_value
        return (self.ic_df, self.p_value_df)

    
    def cal_ic_indicator(self = None):
        '''计算 12 个 IC 评价指标'''
        ic = self.ic_df
        pv = self.p_value_df
        count = ic.count()
        greater_zero = ic > 0
        self.ic_result.loc['IC 均值'] = ic.mean()
        self.ic_result.loc['IC 标准差'] = ic.std()
        self.ic_result.loc['IC IR'] = self.ic_result.loc['IC 均值'] / self.ic_result.loc['IC 标准差']
        self.ic_result.loc['IC>0 占比'] = ic[greater_zero].count() / count
        self.ic_result.loc['|IC|>0.02 占比'] = (ic.abs() > 0.02).sum() / count
        self.ic_result.loc['IC 偏度'] = ic.skew()
        self.ic_result.loc['IC 峰度'] = ic.kurtosis()
        pv_significant = pv[pv < 0.05].count()
        self.ic_result.loc['正相关显著比例'] = pv_significant / count
        self.ic_result.loc['负相关显著比例'] = 1 - self.ic_result.loc['正相关显著比例']
        change_df = greater_zero.iloc[:-1].values != greater_zero.iloc[1:].values
        ic_change_num = pd.DataFrame(change_df, columns = ic.columns).sum()
        self.ic_result.loc['方向切换比例'] = ic_change_num / count
        self.ic_result.loc['同向比例'] = 1 - self.ic_result.loc['方向切换比例']
        return self.ic_result

    
    def save(self = None, path = None):
        '''保存 IC 分析结果'''
        self.ic_df.to_hdf(path, key = f'''{self.factor_name}_ic''')
        self.p_value_df.to_hdf(path, key = f'''{self.factor_name}_p_value''')
        self.ic_result.to_hdf(path, key = f'''{self.factor_name}_ic_result''')
        print(f'''IC 分析结果已保存至: {path}''')


# WARNING: Decompyle incomplete
