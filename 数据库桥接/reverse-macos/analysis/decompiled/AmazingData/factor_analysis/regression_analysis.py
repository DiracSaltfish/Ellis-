# Source Generated with Decompyle++
# File: regression_analysis.pyc (Python 3.12)

__doc__ = '\n回归法分析模块\n\n以流通市值平方根或流通市值的倒数为权重做 WLS（加权最小二乘法），\n将因子 T 期的暴露度与 T+1 期股票收益回归，估计因子收益率序列。\n\n包含:\n    1. WLS 回归估计因子日收益率\n    2. T 值统计\n    3. 净值分析（年化收益/波动率/夏普/最大回撤/Calmar 等）\n    4. 自相关分析 (ACF/PACF)\n'
import numpy as np
import pandas as pd
from scipy import stats as scipy_stats
from statsmodels.tsa import stattools
from statsmodels.api import api as sm
from typing import Optional, Tuple, Dict
_PD_VERSION = (lambda .0: pass# WARNING: Decompyle incomplete
)(pd.__version__.split('.')[:2]())
MONTH_END = 'ME' if _PD_VERSION >= (2, 2) else 'M'

class NetValueAnalyzer:
    '''
    净值分析器 — 自实现，不依赖外部回测引擎。

    输入一条净值序列，计算全面的绩效指标。
    '''
    
    def __init__(self = None, net_value = None, benchmark = None):
        '''
        :param net_value: 净值序列
        :param benchmark: 基准净值序列（可选）
        '''
        self.nv = net_value.dropna()
    # WARNING: Decompyle incomplete

    
    def analyze(self = None):
        '''执行全部分析，返回指标字典'''
        result = { }
        result['start_date'] = self.nv.index[0]
        result['end_date'] = self.nv.index[-1]
        result['total_days'] = len(self.nv)
        result['total_return'] = self.nv.iloc[-1] / self.nv.iloc[0] - 1
        result['annual_return'] = self._calc_annual_return()
        result['annual_volatility'] = self._returns.std() * np.sqrt(self._annual_factor)
        result['daily_volatility'] = self._returns.std()
        result['monthly_volatility'] = self._returns.resample(MONTH_END).std().mean() * np.sqrt(12)
        rf = 0.02
        result['sharpe_ratio'] = (result['annual_return'] - rf) / result['annual_volatility'] if result['annual_volatility'] > 0 else 0
        (result['max_drawdown'], result['max_drawdown_start'], result['max_drawdown_end']) = self._calc_max_drawdown()
        result['calmar_ratio'] = result['annual_return'] / abs(result['max_drawdown']) if abs(result['max_drawdown']) > 0 else 0
        rf = 0.03
        rf_daily = rf / self._annual_factor
        downside_returns = self._returns[self._returns < rf_daily]
        result['downside_risk'] = downside_returns.std() * np.sqrt(self._annual_factor) if len(downside_returns) > 0 else 0
        result['sortino_ratio'] = (result['annual_return'] - rf) / result['downside_risk'] if result['downside_risk'] > 0 else 0
        result['win_rate'] = (self._returns > 0).mean()
        result['daily_win_rate'] = (self._returns > 0).mean()
        result['monthly_returns'] = self._returns.resample(MONTH_END).apply((lambda x: (1 + x).prod() - 1))
        result['monthly_win_rate'] = (result['monthly_returns'] > 0).mean()
        result['skewness'] = self._returns.skew()
        result['kurtosis'] = self._returns.kurtosis()
        result['positive_days'] = (self._returns > 0).sum()
        result['negative_days'] = (self._returns < 0).sum()
    # WARNING: Decompyle incomplete

    
    def _calc_return_distribution(self = None):
        '''计算日收益率分布（12档），与 analysis_center 保持一致'''
        bins = [
            -(np.inf),
            -0.1,
            -0.05,
            -0.03,
            -0.02,
            -0.01,
            0,
            0.01,
            0.02,
            0.03,
            0.05,
            0.1,
            np.inf]
        labels = [
            '-10%以下',
            '-10%~-5%',
            '-5%~-3%',
            '-3%~-2%',
            '-2%~-1%',
            '-1%~0%',
            '0%~1%',
            '1%~2%',
            '2%~3%',
            '3%~5%',
            '5%~10%',
            '10%以上']
        dist = pd.cut(self._returns, bins = bins, labels = labels)
        counts = dist.value_counts()
    # WARNING: Decompyle incomplete

    
    def _calc_annual_return(self = None):
        n = len(self.nv)
        if n < 2:
            return 0
        total = self.nv.iloc[-1] / self.nv.iloc[0]
        return total ** (self._annual_factor / n) - 1

    
    def _calc_max_drawdown(self = None):
        '''计算最大回撤（%）及起止日期'''
        cummax = self.nv.cummax()
        drawdown = ((self.nv - cummax) / cummax) * 100
        max_dd = drawdown.min()
        end_idx = drawdown.idxmin()
        before_end = self.nv.loc[:end_idx]
        start_idx = before_end.idxmax()
        return (max_dd, str(start_idx), str(end_idx))



class RegressionAnalysis:
    """
    回归法单因子分析。

    使用示例:
        ra = RegressionAnalysis(factor_df, 'my_factor', close_price_df, benchmark_df)
        ra.cal_factor_return(industry_dummies=ind_df, market_value_df=mv_df)
        ra.cal_t_value_statistics()
        ra.cal_net_analysis()
        ra.cal_acf(nlags=10)
    """
    
    def __init__(self = None, factor = None, factor_name = None, market_close_data = (None,), benchmark_df = ('factor', pd.DataFrame, 'factor_name', str, 'market_close_data', pd.DataFrame, 'benchmark_df', Optional[pd.DataFrame])):
        """
        :param factor: 因子值, index=日期, columns=股票代码
        :param factor_name: 因子名称
        :param market_close_data: 收盘价, index=日期, columns=股票代码
        :param benchmark_df: 基准收盘价 DataFrame, columns=['close']
        """
        self.factor = factor
        self.factor_name = factor_name
        self.benchmark_df = benchmark_df
        common_dates = factor.index.intersection(market_close_data.index)
        common_stocks = factor.columns.intersection(market_close_data.columns)
        market_data = market_close_data.loc[(common_dates, common_stocks)]
        self.factor = factor.loc[(common_dates, common_stocks)]
        self.stock_return = market_data.pct_change().shift(-1)
        self.factor_return = pd.DataFrame(index = self.factor.index, columns = [
            'cumsum',
            'cumprod',
            'daily'], dtype = float)
        self.factor_return_daily = None
        self.factor_t_value = None
        self.factor_t_value_statistics = None
        self.net_analysis_result = {
            'cumsum': { },
            'cumprod': { } }
        self.acf_result = {
            'cumsum': { },
            'cumprod': { } }

    
    def cal_factor_return(self = None, industry_dummies = None, market_value_df = None, method = (None, None, 'float_value_inverse')):
        """
        WLS 回归估计因子日收益率。

        :param industry_dummies: 行业哑变量, index=股票代码, columns=行业代码
        :param market_value_df: 流通市值, index=日期, columns=股票代码
        :param method: 'float_value_inverse' 或 'float_value_square_root'
        """
        n = len(self.factor)
        factor_return_dict = { }
        t_value_dict = { }
    # WARNING: Decompyle incomplete

    
    def cal_t_value_statistics(self):
        '''计算 T 值统计'''
        t_abs = self.factor_t_value.abs().dropna()
        if len(t_abs) == 0:
            self.factor_t_value_statistics = pd.Series({
                't_value_mean': np.nan,
                't_value_greater_two': np.nan })
            return None
        self.factor_t_value_statistics = pd.Series({
            't_value_mean': t_abs.mean(),
            't_value_greater_two': (t_abs > 2).mean() })

    
    def cal_net_analysis(self):
        '''对 cumsum 和 cumprod 两条净值曲线做绩效分析'''
        bm_series = None
    # WARNING: Decompyle incomplete

    
    def cal_acf(self = None, nlags = None):
        '''计算自相关和偏自相关系数'''
        for key in ('cumsum', 'cumprod'):
            nv = self.factor_return[key].dropna()
            if len(nv) <= nlags:
                self.acf_result[key] = {
                    'acf': [],
                    'pacf': [] }
                continue
            returns = nv.pct_change().dropna().values
            self.acf_result[key]['acf'] = stattools.acf(returns, fft = False, nlags = nlags)[1:]
            self.acf_result[key]['pacf'] = stattools.pacf(returns, nlags = nlags)[1:]

    
    def save(self = None, path = None):
        '''保存回归分析结果'''
        self.factor_return.to_hdf(path, key = f'''{self.factor_name}_factor_return''')
    # WARNING: Decompyle incomplete


# WARNING: Decompyle incomplete
