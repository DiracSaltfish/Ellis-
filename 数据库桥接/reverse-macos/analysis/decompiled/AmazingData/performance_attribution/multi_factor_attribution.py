# Source Generated with Decompyle++
# File: multi_factor_attribution.pyc (Python 3.12)

__doc__ = '\n多因子归因分析模块\n\n核心功能:\n    1. 单期模型 — 超额收益 = Σ 因子收益 + 特殊收益 + 日内调仓收益\n    2. 多期模型 — 收益贡献（对数收益）跨期拼接，累计归因\n\n关键公式:\n    - 组合收益 = 持仓不变收益 + 日内调仓收益\n    - 超额收益分解: Σ(Δw_j × f_j) + ε + 调仓收益\n    - 多期: 收益贡献 r_contrib = ln(1 + r), 跨期相加得复合收益\n'
import numpy as np
import pandas as pd
from typing import Optional, Dict
from AmazingData.performance_attribution.factor_premium_estimator import FactorPremiumEstimator

class MultiFactorAttribution:
    '''
    多因子归因分析器。

    使用示例:
        mfa = MultiFactorAttribution(
            portfolio_weight, benchmark_weight,
            stock_return, factor_exposure, factor_premium
        )
        mfa.run_single_period()
        print(mfa.factor_contribution)  # 因子收益贡献
        print(mfa.specific_return)      # 特殊收益
        print(mfa.intraday_return)      # 日内调仓收益
    '''
    
    def __init__(self, portfolio_weight, benchmark_weight = None, stock_return = None, factor_exposure = None, factor_premium = ('portfolio_weight', pd.DataFrame, 'benchmark_weight', pd.DataFrame, 'stock_return', pd.DataFrame, 'factor_exposure', pd.DataFrame, 'factor_premium', pd.DataFrame)):
        '''
        :param portfolio_weight: 组合持仓权重, index=日期, columns=股票代码
        :param benchmark_weight: 基准持仓权重, index=日期, columns=股票代码
        :param stock_return: 股票收益率, index=日期, columns=股票代码
        :param factor_exposure: 因子暴露, index=日期, columns=股票代码
        :param factor_premium: 因子溢价, index=日期, columns=因子名
        '''
        self.portfolio_weight = portfolio_weight
        self.benchmark_weight = benchmark_weight
        self.stock_return = stock_return
        self.factor_exposure = factor_exposure
        self.factor_premium = factor_premium
        common_dates = portfolio_weight.index.intersection(benchmark_weight.index)
        common_dates = common_dates.intersection(stock_return.index)
        common_dates = common_dates.intersection(factor_exposure.index)
        common_stocks = portfolio_weight.columns.intersection(benchmark_weight.columns)
        common_stocks = common_stocks.intersection(stock_return.columns)
        common_stocks = common_stocks.intersection(factor_exposure.columns)
        self.portfolio_weight = portfolio_weight.loc[(common_dates, common_stocks)].fillna(0)
        self.benchmark_weight = benchmark_weight.loc[(common_dates, common_stocks)].fillna(0)
        self.stock_return = stock_return.loc[(common_dates, common_stocks)]
        self.factor_exposure = factor_exposure.loc[(common_dates, common_stocks)]
        self._factor_names = list(factor_premium.columns)
        self.factor_contribution = None
        self.specific_return = None
        self.intraday_return = None
        self.portfolio_return = None
        self.benchmark_return = None
        self.excess_return = None
        self.cumulative_attribution_result = None

    
    def run_single_period(self = None):
        '''
        单期归因模型。

        文档第4章(1):
        - 组合收益 = 持仓不变收益 + 日内调仓收益
        - 超额收益 = Σ(Δw_j × f_j) + 特殊收益 + 日内调仓收益

        其中 Δw_j = w_portfolio_j - w_benchmark_j 为组合与基准的因子敞口差

        :return: 归因结果 DataFrame
        '''
        n_dates = len(self.portfolio_weight)
        hold_return = (self.portfolio_weight.shift(1).fillna(0) * self.stock_return).sum(axis = 1)
        portfolio_ret = (self.portfolio_weight * self.stock_return).sum(axis = 1)
        benchmark_ret = (self.benchmark_weight * self.stock_return).sum(axis = 1)
        intraday = portfolio_ret - hold_return
        excess = portfolio_ret - benchmark_ret
        self.portfolio_return = portfolio_ret
        self.benchmark_return = benchmark_ret
        self.intraday_return = intraday
        self.excess_return = excess
        portfolio_exposure = (self.portfolio_weight.T @ self.factor_exposure.fillna(0).T).T
        benchmark_exposure = (self.benchmark_weight.T @ self.factor_exposure.fillna(0).T).T
        exposure_diff = portfolio_exposure - benchmark_exposure
        factor_contrib = { }
        for fn in self._factor_names:
            if not fn in self.factor_premium.columns:
                continue
            factor_contrib[fn] = exposure_diff[fn].shift(1).fillna(0) * self.factor_premium[fn].fillna(0)
        self.factor_contribution = pd.DataFrame(factor_contrib, index = self.portfolio_weight.index)
        total_factor = self.factor_contribution.sum(axis = 1)
        self.specific_return = excess - total_factor
        result = pd.DataFrame({
            'portfolio_return': portfolio_ret,
            'benchmark_return': benchmark_ret,
            'excess_return': excess,
            'factor_return_total': total_factor,
            'specific_return': self.specific_return,
            'intraday_return': intraday })
        return result

    
    def run_multi_period(self = None):
        '''
        多期累计归因模型。

        文档第4章(2):
        收益贡献 r_contrib = ln(1 + r)，跨期相加得复合收益。
        每个子期分别归因后，通过收益贡献累加实现累计归因。

        :return: 累计归因结果 DataFrame
        '''
        self.run_single_period()
        
        def to_contrib(r):
            '''将收益率转为收益贡献（对数收益）'''
            return np.log(1 + r.fillna(0).clip(lower = -0.999))

        contrib_data = { }
        contrib_data['portfolio_contrib'] = to_contrib(self.portfolio_return)
        contrib_data['benchmark_contrib'] = to_contrib(self.benchmark_return)
        contrib_data['excess_contrib'] = contrib_data['portfolio_contrib'] - contrib_data['benchmark_contrib']
        for fn in self._factor_names:
            if not fn in self.factor_contribution.columns:
                continue
            contrib_data[f'''{fn}_contrib'''] = to_contrib(self.factor_contribution[fn])
        contrib_data['specific_contrib'] = to_contrib(self.specific_return)
        contrib_data['intraday_contrib'] = to_contrib(self.intraday_return)
        contrib_df = pd.DataFrame(contrib_data)
        cum_contrib = contrib_df.cumsum()
    # WARNING: Decompyle incomplete

    
    def summary(self = None):
        '''
        返回归因分析汇总结果。

        :return: 字典，包含各收益分解项的统计指标
        '''
        result = { }
    # WARNING: Decompyle incomplete

    
    def get_exposure_diff(self = None):
        '''
        计算组合与基准的因子敞口差异。

        :return: 敞口差异 DataFrame, index=日期, columns=因子名
        '''
        portfolio_exposure = (self.portfolio_weight.T @ self.factor_exposure.fillna(0).T).T
        benchmark_exposure = (self.benchmark_weight.T @ self.factor_exposure.fillna(0).T).T
        return portfolio_exposure - benchmark_exposure

    
    def save(self = None, path = None):
        '''保存归因结果到 HDF5 文件'''
        pass
    # WARNING: Decompyle incomplete


# WARNING: Decompyle incomplete
