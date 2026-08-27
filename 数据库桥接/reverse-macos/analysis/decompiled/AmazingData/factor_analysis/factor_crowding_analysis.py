# Source Generated with Decompyle++
# File: factor_crowding_analysis.pyc (Python 3.12)

__doc__ = '\n因子拥挤度测算模块\n\n五个拥挤度指标 + 复合拥挤度:\n\n(1) 估值价差 (Valuation Spread)\n    因子多头组合与空头组合的估值差异，估值越悬殊说明拥挤度越高。\n\n(2) 配对相关性 (Pairwise Correlation)\n    因子多头组合内部股票收益率的两两相关系数均值，相关性越高说明持仓越拥挤。\n\n(3) 长期收益反转 (Long-term Return Reversal)\n    因子多头组合过去长期收益的反转概率。若过去收益极高且开始回落，说明可能拥挤。\n\n(4) 因子波动率 (Factor Volatility)\n    因子收益率波动率。波动率异常放大说明因子可能被拥挤交易。\n\n(5) 复合拥挤度 (Composite Crowding)\n    上述四个指标的等权复合（先各自标准化再等权平均）。\n'
import numpy as np
import pandas as pd
from scipy import stats
from typing import Dict, Optional, Tuple

class FactorCrowdingAnalysis:
    '''
    因子拥挤度分析。

    使用示例:
        fca = FactorCrowdingAnalysis(
            factor=factor_df,
            close_price=close_df,
            market_cap=mv_df,
            group_num=5,
        )
        fca.calc_all(window=60)
        print(fca.composite_crowding)  # 复合拥挤度时序
        print(fca.crowding_summary())  # 汇总统计
    '''
    
    def __init__(self, factor = None, close_price = None, market_cap = None, group_num = (None, 5, False), ascending = ('factor', pd.DataFrame, 'close_price', pd.DataFrame, 'market_cap', Optional[pd.DataFrame], 'group_num', int, 'ascending', bool)):
        '''
        :param factor: 因子值, index=日期, columns=股票代码
        :param close_price: 收盘价, index=日期, columns=股票代码
        :param market_cap: 市值（估值价差用）, index=日期, columns=股票代码
        :param group_num: 分组数量
        :param ascending: False=因子值大→小（多头=G0）
        '''
        self.factor = factor
        self.close_price = close_price
        self.market_cap = market_cap
        self.group_num = group_num
        self.ascending = ascending
        common_dates = factor.index.intersection(close_price.index)
        common_stocks = factor.columns.intersection(close_price.columns)
        self.factor = factor.loc[(common_dates, common_stocks)]
        self.close_price = close_price.loc[(common_dates, common_stocks)]
    # WARNING: Decompyle incomplete

    
    def calc_valuation_spread(self = None, window = None):
        '''
        估值价差 = 空头组估值分位数 - 多头组估值分位数。
        估值用 PB 代理（市值/净资产），若无净资产数据则用市值分位数替代。

        拥挤度越高 → 价差越大（多空估值分化严重）。

        :param window: 滚动窗口
        :return: Series, index=日期
        '''
        pass
    # WARNING: Decompyle incomplete

    
    def calc_pairwise_correlation(self = None, window = None):
        '''
        配对相关性 = 多头组合内股票日收益率的两两相关系数均值。
        相关性越高 → 持仓同质化严重 → 拥挤度越高。

        :param window: 滚动窗口
        :return: Series, index=日期
        '''
        returns = self.close_price.pct_change()
        group_labels = self._get_group_labels()
        corr_series = []
        for i in range(window, len(returns)):
            date = returns.index[i]
            if i >= len(group_labels):
                range(window, len(returns))
            else:
                groups = group_labels.iloc[i].dropna()
                long_stocks = groups[groups == self.long_group].index
                if len(long_stocks) < 3:
                    corr_series.append(np.nan)
                    continue
                window_ret = returns.iloc[(i - window) + 1:i + 1][long_stocks].dropna(axis = 1)
                if window_ret.shape[1] < 3:
                    corr_series.append(np.nan)
                    continue
                corr_mat = window_ret.corr()
                upper = corr_mat.values[np.triu_indices_from(corr_mat.values, k = 1)]
                corr_series.append(np.nanmean(upper))
        corr = pd.Series(corr_series, index = returns.index[window:window + len(corr_series)])
        self.pairwise_corr = corr
        return self.pairwise_corr

    
    def calc_return_reversal(self = None, long_window = None, short_window = None):
        '''
        长期收益反转 = 多头组合过去长期收益 - 近期收益。
        正值表示长期收益高但近期回落 → 可能发生反转 → 拥挤度上升。

        :param long_window: 长期窗口（如120日）
        :param short_window: 短期窗口（如20日）
        :return: Series, index=日期
        '''
        returns = self.close_price.pct_change()
        group_labels = self._get_group_labels()
        reversal_series = []
        for i in range(long_window, len(returns)):
            date = returns.index[i]
            if i >= len(group_labels):
                range(long_window, len(returns))
            else:
                groups = group_labels.iloc[i].dropna()
                long_stocks = groups[groups == self.long_group].index
                if len(long_stocks) < 3:
                    reversal_series.append(np.nan)
                    continue
                long_ret = returns.iloc[i - long_window:i][long_stocks].mean().mean()
                short_ret = returns.iloc[i - short_window:i][long_stocks].mean().mean()
                reversal = long_ret - short_ret
                reversal_series.append(reversal)
        rev = pd.Series(reversal_series, index = returns.index[long_window:long_window + len(reversal_series)])
        self.return_reversal = rev
        return self.return_reversal

    
    def calc_factor_volatility(self = None, window = None):
        '''
        因子波动率 = 因子收益率（多头-空头日收益差）的滚动标准差。
        波动率异常放大 → 因子可能被拥挤交易冲击。

        :param window: 滚动窗口
        :return: Series, index=日期
        '''
        returns = self.close_price.pct_change().shift(-1)
        group_labels = self._get_group_labels()
        ls_returns = []
        for i in range(len(returns) - 1):
            if i >= len(group_labels):
                range(len(returns) - 1)
            else:
                groups = group_labels.iloc[i].dropna()
                long_stocks = groups[groups == self.long_group].index
                short_stocks = groups[groups == self.short_group].index
                long_ret = returns.iloc[i][long_stocks].mean() if len(long_stocks) > 0 else np.nan
                short_ret = returns.iloc[i][short_stocks].mean() if len(short_stocks) > 0 else np.nan
                ls_returns.append(long_ret - short_ret)
        ls_ret_series = pd.Series(ls_returns, index = returns.index[:len(ls_returns)])
        self.factor_volatility = ls_ret_series.rolling(window = window, min_periods = window // 2).std()
        return self.factor_volatility

    
    def calc_composite_crowding(self = None):
        '''
        复合拥挤度 = 四个子指标的等权平均（先各自 Z-score 标准化）。

        :return: Series, index=日期
        '''
        indicators = {
            'valuation': self.valuation_spread,
            'pairwise': self.pairwise_corr,
            'reversal': self.return_reversal,
            'volatility': self.factor_volatility }
        standardized = { }
    # WARNING: Decompyle incomplete

    
    def calc_all(self = None, window = None):
        '''一键计算全部拥挤度指标'''
        result = { }
        
        try:
            result['valuation_spread'] = self.calc_valuation_spread(window)
            result['pairwise_corr'] = self.calc_pairwise_correlation(window)
            result['return_reversal'] = self.calc_return_reversal()
            result['factor_volatility'] = self.calc_factor_volatility(window)
            result['composite_crowding'] = self.calc_composite_crowding()
            return result
        except Exception:
            e = None
            print(f'''估值价差计算失败: {e}''')
            e = None
            del e
            continue
            e = None
            del e


    
    def crowding_summary(self = None):
        '''返回各拥挤度指标的统计汇总'''
        indicators = {
            '估值价差': self.valuation_spread,
            '配对相关性': self.pairwise_corr,
            '长期收益反转': self.return_reversal,
            '因子波动率': self.factor_volatility,
            '复合拥挤度': self.composite_crowding }
        rows = []
    # WARNING: Decompyle incomplete

    
    def get_crowding_level(self = None, threshold_high = None, threshold_warn = None):
        """
        判断当前拥挤度水平。

        :return: 'high' / 'warn' / 'normal'
        """
        pass
    # WARNING: Decompyle incomplete

    
    def _get_group_labels(self = None):
        '''获取每期的分组标签'''
        pass
    # WARNING: Decompyle incomplete


# WARNING: Decompyle incomplete
