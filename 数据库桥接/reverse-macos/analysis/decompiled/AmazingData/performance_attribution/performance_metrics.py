# Source Generated with Decompyle++
# File: performance_metrics.pyc (Python 3.12)

'''
绩效指标计算模块

封装 NetValueAnalyzer，提供一站式的组合绩效指标输出。

核心功能:
    1. 完整绩效指标 — 年化收益/波动率/夏普/最大回撤/信息比率/Alpha/Beta等
    2. 滚动绩效指标 — 滚动夏普、滚动回撤、滚动波动率等
    3. 年度/月度日历指标汇总
'''
import numpy as np
import pandas as pd
from typing import Optional, Dict
from AmazingData.factor_analysis.regression_analysis import NetValueAnalyzer

class PerformanceMetrics:
    """
    组合绩效指标计算器。

    封装 NetValueAnalyzer，提供增强的绩效指标计算能力。

    使用示例:
        pm = PerformanceMetrics(portfolio_nav, benchmark_nav)
        metrics = pm.analyze()
        print(metrics['annual_return'])
        print(metrics['sharpe_ratio'])
        rolling = pm.rolling_metrics(window=252)
        calendar = pm.calendar_metrics()
    """
    
    def __init__(self = None, net_value = None, benchmark = None):
        '''
        :param net_value: 组合净值序列, index=日期
        :param benchmark: 基准净值序列（可选）, index=日期
        '''
        self.net_value = net_value.dropna()
    # WARNING: Decompyle incomplete

    
    def analyze(self = None):
        '''
        计算完整的组合绩效指标。

        :return: 绩效指标字典
        '''
        self._metrics = self._analyzer.analyze()
        self._metrics['cumulative_return'] = self.net_value.iloc[-1] / self.net_value.iloc[0] - 1
        self._metrics['annual_factor'] = 252
        self._metrics['nav_start'] = self.net_value.iloc[0]
        self._metrics['nav_end'] = self.net_value.iloc[-1]
        self._metrics['nav_max'] = self.net_value.max()
        self._metrics['nav_min'] = self.net_value.min()
        if len(self._returns) > 0:
            self._metrics['var_95'] = self._returns.quantile(0.05)
            self._metrics['cvar_95'] = self._returns[self._returns <= self._metrics['var_95']].mean()
        return self._metrics

    
    def rolling_metrics(self = None, window = None):
        '''
        计算滚动绩效指标。

        :param window: 滚动窗口（交易日），默认 252（约1年）
        :return: 滚动指标 DataFrame
        '''
        if len(self._returns) < window:
            return pd.DataFrame()
        roll_ret = None._returns.rolling(window = window)
        roll_nv = self.net_value.rolling(window = window)
        rolling = pd.DataFrame(index = self._returns.index)
        rolling['rolling_annual_return'] = (1 + roll_ret.apply((lambda x: (1 + x).prod() - 1))) ** (252 / window) - 1
        rolling['rolling_volatility'] = roll_ret.std() * np.sqrt(252)
        rf_annual = 0.02
        rf_daily = rf_annual / 252
        rolling['rolling_sharpe'] = (rolling['rolling_annual_return'] - rf_annual) / rolling['rolling_volatility']
        
        def _rolling_max_drawdown(nv_window):
            cummax = nv_window.cummax()
            dd = (nv_window - cummax) / cummax
            return dd.min()

        rolling['rolling_max_drawdown'] = roll_nv.apply(_rolling_max_drawdown)
        rolling['rolling_calmar'] = rolling['rolling_annual_return'] / rolling['rolling_max_drawdown'].abs()
        rolling['rolling_win_rate'] = roll_ret.apply((lambda x: (x > 0).mean()))
    # WARNING: Decompyle incomplete

    
    def calendar_metrics(self = None):
        """
        计算年度和月度绩效指标汇总。

        :return: {'yearly': DataFrame, 'monthly': DataFrame}
        """
        yearly_returns = self._returns.resample('YE').apply((lambda x: (1 + x).prod() - 1))
        yearly_vol = self._returns.resample('YE').std() * np.sqrt(252)
        yearly = pd.DataFrame({
            'annual_return': yearly_returns,
            'annual_volatility': yearly_vol })
        yearly['sharpe_ratio'] = (yearly['annual_return'] - 0.02) / yearly['annual_volatility']
        monthly_returns = self._returns.resample('ME').apply((lambda x: (1 + x).prod() - 1))
        monthly_vol = self._returns.resample('ME').std() * np.sqrt(21)
        monthly = pd.DataFrame({
            'monthly_return': monthly_returns,
            'monthly_volatility': monthly_vol })
        monthly['win'] = monthly['monthly_return'] > 0
        result = {
            'yearly': yearly.dropna(),
            'monthly': monthly.dropna() }
        result['yearly_summary'] = {
            'avg_annual_return': yearly['annual_return'].mean(),
            'positive_years': (yearly['annual_return'] > 0).sum(),
            'total_years': len(yearly),
            'best_year': yearly['annual_return'].max(),
            'worst_year': yearly['annual_return'].min() }
        result['monthly_summary'] = {
            'avg_monthly_return': monthly['monthly_return'].mean(),
            'monthly_win_rate': monthly['win'].mean(),
            'best_month': monthly['monthly_return'].max(),
            'worst_month': monthly['monthly_return'].min() }
        return result

    annual_return = (lambda self = None: if not self._metrics:
self.analyze()self._metrics.get('annual_return', 0))()
    annual_volatility = (lambda self = None: if not self._metrics:
self.analyze()self._metrics.get('annual_volatility', 0))()
    sharpe_ratio = (lambda self = None: if not self._metrics:
self.analyze()self._metrics.get('sharpe_ratio', 0))()
    max_drawdown = (lambda self = None: if not self._metrics:
self.analyze()self._metrics.get('max_drawdown', 0))()
    calmar_ratio = (lambda self = None: if not self._metrics:
self.analyze()self._metrics.get('calmar_ratio', 0))()
    win_rate = (lambda self = None: if not self._metrics:
self.analyze()self._metrics.get('win_rate', 0))()
    information_ratio = (lambda self = None: if not self._metrics:
self.analyze()self._metrics.get('information_ratio', 0))()
    
    def to_dataframe(self = None):
        '''
        将绩效指标转为 DataFrame 便于展示。

        :return: 绩效指标 DataFrame
        '''
        if not self._metrics:
            self.analyze()
        key_metrics = [
            'start_date',
            'end_date',
            'total_days',
            'total_return',
            'annual_return',
            'cumulative_return',
            'annual_volatility',
            'daily_volatility',
            'sharpe_ratio',
            'sortino_ratio',
            'calmar_ratio',
            'max_drawdown',
            'win_rate',
            'daily_win_rate',
            'monthly_win_rate',
            'information_ratio',
            'alpha',
            'beta',
            'treynor_ratio',
            'tracking_error',
            'excess_annual_return',
            'skewness',
            'kurtosis',
            'var_95',
            'cvar_95',
            'factor_stability_coeff',
            'positive_days',
            'negative_days']
    # WARNING: Decompyle incomplete

    
    def save(self = None, path = None):
        '''保存绩效指标结果到 HDF5 文件'''
        if not self._metrics:
            self.analyze()
        metrics_series = pd.Series(self._metrics)
        metrics_series.to_hdf(path, key = 'performance_metrics')
        print(f'''绩效指标已保存至: {path}''')


if __name__ == '__main__':
    np.random.seed(42)
    dates = pd.date_range('2024-01-01', '2024-12-31', freq = 'B')
    trend = np.linspace(0, 0.15, len(dates))
    nav = pd.Series(np.exp(np.random.randn(len(dates)).cumsum() * 0.012 + trend), index = dates, name = 'portfolio')
    bm_trend = np.linspace(0, 0.1, len(dates))
    bm_nav = pd.Series(np.exp(np.random.randn(len(dates)).cumsum() * 0.01 + bm_trend), index = dates, name = 'benchmark')
    print('=== 绩效指标测试 ===\n')
    pm = PerformanceMetrics(nav, bm_nav)
    metrics = pm.analyze()
    print('关键绩效指标:')
    print(f'''  年化收益:     {pm.annual_return:.4f}''')
    print(f'''  年化波动率:   {pm.annual_volatility:.4f}''')
    print(f'''  夏普比率:     {pm.sharpe_ratio:.4f}''')
    print(f'''  最大回撤:     {pm.max_drawdown:.4f}''')
    print(f'''  Calmar比率:   {pm.calmar_ratio:.4f}''')
    print(f'''  胜率:         {pm.win_rate:.4f}''')
    print(f'''  信息比率:     {pm.information_ratio:.4f}''')
    rolling = pm.rolling_metrics(window = 60)
    print('\n滚动指标 (最后3行):')
    print(rolling.tail(3).round(4))
    calendar = pm.calendar_metrics()
    print('\n年度汇总:')
    print(calendar['yearly'].round(4))
    print(f'''\n月度胜率: {calendar['monthly_summary']['monthly_win_rate']:.2%}''')
    print('\n完整指标 DataFrame:')
    print(pm.to_dataframe().round(4).head(15))
    print('\n=== 绩效指标测试通过 ===')
    return None
