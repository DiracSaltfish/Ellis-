# Source Generated with Decompyle++
# File: factor_preprocessing.pyc (Python 3.12)

__doc__ = '\n因子数据预处理模块\n\n仅依赖 NumPy/Pandas/statsmodels。\n\n预处理流水线:\n    1. 数据筛选 — 时间区间 + 股票池过滤\n    2. 去极值   — STD / MAD / 分位数 / Boxplot(含偏度调整)\n    3. 中性化   — 行业中性化 + 流通市值中性化 (OLS 取残差)\n    4. 标准化   — Min-Max / Z-score / 排序百分位\n    5. 补空值   — 截面均值 / 截面中位数\n\n加速策略:\n    - 所有截面操作使用 Pandas axis=1 向量化运算\n    - 中性化支持预计算行业哑变量矩阵加速\n    - 避免逐行 Python 循环\n'
import math
from datetime import datetime
from typing import Dict, List, Optional, Union
import numpy as np
import pandas as pd
from statsmodels.api import api as sm
from AmazingData.factor_analysis.factor_constant import ExtremeMethod, ScaleMethod, FillNanMethod, NeutralizeMethod

class FactorPreProcessing:
    """
    因子预处理流水线。

    使用示例:
        factor_data = pd.DataFrame(...)  # index=日期, columns=股票代码
        fpp = FactorPreProcessing(factor_data)
        fpp.extreme_processing({'std': {'sigma_multiple': 3}})
        fpp.neutralize_processing(
            neutralize_method=[NeutralizeMethod.INDUSTRY.value],
            industry_dummies=industry_df,  # 行业哑变量矩阵
            market_value_df=mv_df,         # 流通市值 DataFrame
        )
        fpp.scale_processing(ScaleMethod.Z_SCORE.value)
        fpp.fill_nan_processing(FillNanMethod.MEDIAN.value)
        result = fpp.processed_data
    """
    
    def __init__(self = None, raw_data = None):
        '''
        :param raw_data: 因子原始数据, index=日期(DatetimeIndex), columns=股票代码
        '''
        if not isinstance(raw_data.index, pd.DatetimeIndex):
            
            try:
                raw_data.index = pd.DatetimeIndex(raw_data.index)
                self.raw_data = raw_data.copy()
                self._original_data = raw_data.copy()
                return None
            except Exception:
                continue


    processed_data = (lambda self = None: self.raw_data)()
    
    def data_filter(self = None, start = None, end = None, stock_list = (None, None, None)):
        '''
        按时间区间和/或股票池过滤数据。

        :param start: 起始日期（闭区间）
        :param end: 结束日期（闭区间）
        :param stock_list: 目标股票代码列表
        '''
        pass
    # WARNING: Decompyle incomplete

    
    def extreme_processing(self = None, method = None):
        """
        去极值处理。

        :param method: 方法参数字典，支持以下格式:
            {'std': {'sigma_multiple': 3}}
            {'mad': {'median_multiple': 5}}
            {'quantile': {'quantile_min': 0.025, 'quantile_max': 0.975}}
            {'box_plot': {}}  # 含偏度调整的 Boxplot 法
        """
        pass
    # WARNING: Decompyle incomplete

    
    def neutralize_processing(self = None, neutralize_method = None, industry_dummies = None, market_value_df = (None, None, None)):
        """
        行业 + 流通市值中性化。以因子值为被解释变量，行业哑变量和市值为解释变量做 OLS，取残差。

        :param neutralize_method: 中性化方法列表，如 ['industry', 'market_value']
        :param industry_dummies: 行业哑变量矩阵, index=股票代码, columns=行业代码
        :param market_value_df: 流通市值 DataFrame, index=日期, columns=股票代码
        """
        pass
    # WARNING: Decompyle incomplete

    
    def scale_processing(self = None, method = None):
        '''
        标准化处理。

        :param method: ScaleMethod 枚举值
        '''
        pass
    # WARNING: Decompyle incomplete

    
    def fill_nan_processing(self = None, method = None, industry_map = None):
        '''
        缺失值填充。

        :param method: FillNanMethod 枚举值
        :param industry_map: {股票代码: 行业代码}，行业均值填充时需要
        '''
        pass
    # WARNING: Decompyle incomplete

    
    def run_pipeline(self, extreme_method, neutralize_method = None, scale_method = None, fill_nan_method = None, industry_dummies = (None, None, None, None, None, None), market_value_df = ('extreme_method', dict, 'neutralize_method', List[str], 'scale_method', str, 'fill_nan_method', str, 'industry_dummies', Optional[pd.DataFrame], 'market_value_df', Optional[pd.DataFrame], 'return', pd.DataFrame)):
        '''
        一键执行完整预处理流水线。

        :return: 预处理后的 DataFrame
        '''
        pass
    # WARNING: Decompyle incomplete

    
    def save(self = None, path = None, factor_name = None):
        '''保存预处理后的数据为 HDF5 文件'''
        self.raw_data.to_hdf(path, key = f'''{factor_name}_pre''', mode = 'w')
        print(f'''因子 [{factor_name}] 预处理数据已保存至: {path}''')

    
    def save_csv(self = None, path = None):
        '''保存为 CSV'''
        self.raw_data.to_csv(path)
        print(f'''数据已保存至: {path}''')



class _Extreme:
    '''去极值处理（内部类）'''
    
    def __init__(self = None, raw_data = None):
        self.raw_data = raw_data

    
    def std_method(self = None, sigma_multiple = None):
        '''标准差法: mean ± n*std'''
        mean = self.raw_data.mean(axis = 1)
        std = self.raw_data.std(axis = 1)
        upper = mean + sigma_multiple * std
        lower = mean - sigma_multiple * std
        return self.raw_data.clip(lower, upper, axis = 0)

    
    def mad_method(self = None, median_multiple = None):
        '''MAD 法: median ± n*MAD'''
        median = self.raw_data.median(axis = 1)
        mad = self.raw_data.sub(median, axis = 0).abs().median(axis = 1)
        upper = median + median_multiple * mad
        lower = median - median_multiple * mad
        return self.raw_data.clip(lower, upper, axis = 0)

    
    def quantile_method(self = None, quantile_min = None, quantile_max = None):
        '''分位数法'''
        lower = self.raw_data.quantile(quantile_min, axis = 1)
        upper = self.raw_data.quantile(quantile_max, axis = 1)
        return self.raw_data.clip(lower, upper, axis = 0)

    
    def box_plot_method(self = None, quantile_min = None, quantile_max = None):
        '''Boxplot 法（含 medcouple 偏度调整）'''
        median = self.raw_data.median(axis = 1)
        q1 = self.raw_data.quantile(quantile_min, axis = 1)
        q3 = self.raw_data.quantile(quantile_max, axis = 1)
        
        def _calc_mc(row, median_series):
            '''计算 medcouple 统计量'''
            vals = row.dropna().values
            if len(vals) < 2:
                return 0
            m = median_series[row.name]
            less = vals[vals <= m]
            greater = vals[vals >= m]
            if len(less) == 0 or len(greater) == 0:
                return 0
            less_tile = np.tile(less, (len(greater), 1))
            greater_tile = np.tile(greater, (len(less), 1)).T
            numerator = less_tile + greater_tile - 2 * m
            denominator = greater_tile - less_tile
            nonzero_mask = denominator != 0
            numerator = numerator[nonzero_mask]
            denominator = denominator[nonzero_mask]
            if len(numerator) == 0:
                return 0
            mc_vals = numerator / denominator
            mc_vals = mc_vals[~np.isnan(mc_vals)]
            if len(mc_vals) > 0:
                return float(np.median(mc_vals))

        mc_series = self.raw_data.apply(_calc_mc, axis = 1, args = (median,))
        min_mult = mc_series.apply((lambda x: if x < 0:
-4))
        max_mult = mc_series.apply((lambda x: if x < 0:
3.5))
        lower = q1 - 1.5 * np.exp(min_mult * mc_series) * (q3 - q1)
        upper = q3 + 1.5 * np.exp(max_mult * mc_series) * (q3 - q1)
        return self.raw_data.clip(lower, upper, axis = 0)



class _Scale:
    '''标准化处理（内部类）'''
    
    def __init__(self = None, raw_data = None):
        self.raw_data = raw_data

    
    def min_max_method(self = None):
        '''Min-Max 标准化 → [0, 1]'''
        dmin = self.raw_data.min(axis = 1)
        dmax = self.raw_data.max(axis = 1)
        denom = dmax - dmin
        denom = denom.replace(0, np.nan)
        return self.raw_data.sub(dmin, axis = 0).div(denom, axis = 0)

    
    def z_score_method(self = None):
        '''Z-score 标准化 → N(0,1)'''
        mean = self.raw_data.mean(axis = 1)
        std = self.raw_data.std(axis = 1)
        std = std.replace(0, np.nan)
        return self.raw_data.sub(mean, axis = 0).div(std, axis = 0)

    
    def rank_method(self = None):
        '''排序百分位 → [0, 1] 均匀分布'''
        ranked = self.raw_data.rank(axis = 1, method = 'average', na_option = 'keep')
        valid_count = self.raw_data.shape[1] - self.raw_data.isna().sum(axis = 1)
        return ranked.div(valid_count, axis = 0)



class _FillNan:
    '''缺失值填充（内部类）'''
    
    def __init__(self = None, raw_data = None, industry_map = None):
        '''
        :param raw_data: 因子数据
        :param industry_map: {股票代码: 行业代码}，用于行业均值填充
        '''
        self.raw_data = raw_data
        if not industry_map:
            industry_map
        self.industry_map = { }

    
    def mean_method(self = None):
        '''截面均值填充'''
        return self.raw_data.T.fillna(self.raw_data.mean(axis = 1)).T

    
    def median_method(self = None):
        '''截面中位数填充'''
        return self.raw_data.T.fillna(self.raw_data.median(axis = 1)).T

    
    def industry_mean_method(self = None):
        '''
        个股所处行业均值填充。
        对每个截面日，用同行业股票的均值填充 NaN。
        '''
        if not self.industry_map:
            return self.mean_method()
        result = None.raw_data.copy()
    # WARNING: Decompyle incomplete



class _Neutralize:
    '''
    行业 + 市值中性化。

    对每个交易日截面做 OLS: factor ~ 行业哑变量 + 流通市值，取残差。
    '''
    
    def __init__(self = None, raw_data = None, industry_dummies = None, market_value_df = (None, None)):
        '''
        :param raw_data: 因子数据, index=日期, columns=股票代码
        :param industry_dummies: 行业哑变量, index=股票代码, columns=行业代码
        :param market_value_df: 流通市值, index=日期, columns=股票代码
        '''
        self.raw_data = raw_data
        self.industry_dummies = industry_dummies
        self.market_value_df = market_value_df

    
    def neutralize(self = None, method = None):
        '''执行中性化'''
        use_industry = NeutralizeMethod.INDUSTRY.value in method
        use_mv = NeutralizeMethod.MARKET_VALUE.value in method
    # WARNING: Decompyle incomplete


# WARNING: Decompyle incomplete
