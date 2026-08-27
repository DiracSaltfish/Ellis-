# Source Generated with Decompyle++
# File: performance_report.pyc (Python 3.12)

__doc__ = '\n绩效报告汇总模块\n\n整合所有绩效归因分析结果，提供一站式分析接口。\n\n支持:\n    1. 链式调用 — report.run_metrics().run_brinson().run_multi_factor().run_barra().run_risk()\n    2. run_all() — 一键执行所有分析\n    3. summary() — 结构化汇总报告\n    4. to_dataframe() — 转为 DataFrame 便于展示和导出\n'
import numpy as np
import pandas as pd
from typing import Optional, Dict
from AmazingData.performance_attribution.performance_metrics import PerformanceMetrics
from AmazingData.performance_attribution.brinson_attribution import BrinsonAttribution
from AmazingData.performance_attribution.multi_factor_attribution import MultiFactorAttribution
from AmazingData.performance_attribution.barra_attribution import BarraAttribution
from AmazingData.performance_attribution.risk_decomposition import RiskDecomposition
from AmazingData.performance_attribution.attribution_constant import BrinsonMethod, AttributionPeriod, DecompositionType

class PerformanceReport:
    '''
    绩效报告汇总类。

    整合绩效指标、Brinson归因、多因子归因、Barra归因、风险分解的结果。

    使用示例:
        report = PerformanceReport(
            nav=portfolio_nav, benchmark_nav=benchmark_nav,
            portfolio_weight=pw, benchmark_weight=bw,
            stock_return=ret, factor_exposure=factor_exp,
            factor_premium=factor_prem,
            industry_map=industry_map,
            style_exposure=style_exp, industry_exposure=ind_exp,
            style_factor_return=style_fr, industry_factor_return=ind_fr,
            factor_cov=factor_cov, specific_risk=specific_risk,
        )
        report.run_all()
        print(report.summary())
    '''
    
    def __init__(self, nav, benchmark_nav, portfolio_weight, benchmark_weight, portfolio_return, benchmark_return, industry_map, brinson_method, stock_return, factor_exposure, factor_premium, style_exposure, industry_exposure, style_factor_return = None, industry_factor_return = None, country_factor_return = None, factor_cov = (None, None, None, None, None, None, None, BrinsonMethod.BF, None, None, None, None, None, None, None, None, None, None), specific_risk = ('nav', Optional[pd.Series], 'benchmark_nav', Optional[pd.Series], 'portfolio_weight', Optional[pd.DataFrame], 'benchmark_weight', Optional[pd.DataFrame], 'portfolio_return', Optional[pd.DataFrame], 'benchmark_return', Optional[pd.DataFrame], 'industry_map', Optional[pd.Series], 'brinson_method', BrinsonMethod, 'stock_return', Optional[pd.DataFrame], 'factor_exposure', Optional[pd.DataFrame], 'factor_premium', Optional[pd.DataFrame], 'style_exposure', Optional[pd.DataFrame], 'industry_exposure', Optional[pd.DataFrame], 'style_factor_return', Optional[pd.DataFrame], 'industry_factor_return', Optional[pd.DataFrame], 'country_factor_return', Optional[pd.Series], 'factor_cov', Optional[pd.DataFrame], 'specific_risk', Optional[pd.Series])):
        '''
        初始化绩效报告，所有参数均为可选，按需传入。
        '''
        self.nav = nav
        self.benchmark_nav = benchmark_nav
        self._metrics = None
        self._metrics_result = { }
        self.portfolio_weight = portfolio_weight
        self.benchmark_weight = benchmark_weight
        self.portfolio_return = portfolio_return
        self.benchmark_return = benchmark_return
        self.industry_map = industry_map
        self.brinson_method = brinson_method
        self._brinson = None
        self._brinson_result = None
        self.stock_return = stock_return
        self.factor_exposure = factor_exposure
        self.factor_premium = factor_premium
        self._multi_factor = None
        self._multi_factor_result = None
        self.style_exposure = style_exposure
        self.industry_exposure = industry_exposure
        self.style_factor_return = style_factor_return
        self.industry_factor_return = industry_factor_return
        self.country_factor_return = country_factor_return
        self._barra = None
        self._barra_result = None
        self.factor_cov = factor_cov
        self.specific_risk = specific_risk
        self._risk = None
        self._risk_result = { }

    
    def run_metrics(self = None):
        '''执行绩效指标计算'''
        pass
    # WARNING: Decompyle incomplete

    
    def run_brinson(self = None):
        '''执行 Brinson 归因'''
        if (lambda .0: pass# WARNING: Decompyle incomplete
)((self.portfolio_weight, self.benchmark_weight, self.portfolio_return, self.benchmark_return, self.industry_map)()):
            self._brinson = BrinsonAttribution(self.portfolio_weight, self.benchmark_weight, self.portfolio_return, self.benchmark_return, self.industry_map, method = self.brinson_method)
            self._brinson_result = self._brinson.run()
        return self

    
    def run_multi_factor(self = None):
        '''执行多因子归因'''
        if (lambda .0: pass# WARNING: Decompyle incomplete
)((self.portfolio_weight, self.benchmark_weight, self.stock_return, self.factor_exposure, self.factor_premium)()):
            self._multi_factor = MultiFactorAttribution(self.portfolio_weight, self.benchmark_weight, self.stock_return, self.factor_exposure, self.factor_premium)
            self._multi_factor_result = self._multi_factor.run_single_period()
        return self

    
    def run_barra(self = None):
        '''执行 Barra 因子归因'''
        if (lambda .0: pass# WARNING: Decompyle incomplete
)((self.portfolio_weight, self.style_exposure, self.industry_exposure, self.style_factor_return)()):
            self._barra = BarraAttribution(portfolio_weight = self.portfolio_weight, style_exposure = self.style_exposure, industry_exposure = self.industry_exposure, style_factor_return = self.style_factor_return, industry_factor_return = self.industry_factor_return, country_factor_return = self.country_factor_return, stock_return = self.stock_return)
            self._barra_result = self._barra.run()
        return self

    
    def run_risk(self = None):
        '''执行风险分解'''
        pass
    # WARNING: Decompyle incomplete

    
    def run_all(self = None):
        '''一键执行所有分析'''
        return self.run_metrics().run_brinson().run_multi_factor().run_barra().run_risk()

    
    def summary(self = None):
        '''
        返回结构化的汇总报告字典。

        :return: 包含各模块分析结果的字典
        '''
        report = { }
        if self._metrics_result:
            report['performance'] = {
                'annual_return': self._metrics_result.get('annual_return'),
                'annual_volatility': self._metrics_result.get('annual_volatility'),
                'sharpe_ratio': self._metrics_result.get('sharpe_ratio'),
                'max_drawdown': self._metrics_result.get('max_drawdown'),
                'calmar_ratio': self._metrics_result.get('calmar_ratio'),
                'sortino_ratio': self._metrics_result.get('sortino_ratio'),
                'win_rate': self._metrics_result.get('win_rate'),
                'information_ratio': self._metrics_result.get('information_ratio'),
                'alpha': self._metrics_result.get('alpha'),
                'beta': self._metrics_result.get('beta'),
                'total_return': self._metrics_result.get('total_return') }
    # WARNING: Decompyle incomplete

    
    def to_dataframe(self = None):
        """
        将报告转为多个 DataFrame 便于展示和导出。

        :return: {'metrics': DataFrame, 'brinson': DataFrame, 'multi_factor': DataFrame, ...}
        """
        dfs = { }
    # WARNING: Decompyle incomplete

    
    def save(self = None, path = None):
        '''保存全部报告结果到 HDF5 文件'''
        dfs = self.to_dataframe()
    # WARNING: Decompyle incomplete


# WARNING: Decompyle incomplete
