# Source Generated with Decompyle++
# File: risk_decomposition.pyc (Python 3.12)

__doc__ = "\n风险分解模块\n\n将组合总风险（波动率）分解为共同因子风险和特质风险两部分。\n\n核心功能:\n    1. 风险分解 — σ²_total = σ²_factor + σ²_specific\n    2. 边际风险贡献 — 各因子对组合风险的边际贡献\n    3. 风险预算分析 — 各因子在总风险中的占比\n\n理论基础:\n    σ² = w'Σw = w'(X'F X + Δ)w = w'X'F X w + w'Δ w\n    其中:\n        - X: 因子载荷矩阵\n        - F: 因子收益率协方差矩阵\n        - Δ: 特质风险对角矩阵\n        - w'X'F X w: 共同因子风险\n        - w'Δ w: 特质风险\n"
import numpy as np
import pandas as pd
from typing import Optional, Dict, Tuple

class RiskDecomposition:
    '''
    组合风险分解器。

    使用示例:
        rd = RiskDecomposition(
            portfolio_weight, factor_exposure,
            factor_cov, specific_risk
        )
        rd.run()
        print(rd.factor_risk)          # 共同因子风险
        print(rd.specific_risk)        # 特质风险
        print(rd.marginal_contribution) # 边际风险贡献
    '''
    
    def __init__(self = None, portfolio_weight = None, factor_exposure = None, factor_cov = (None,), specific_risk = ('portfolio_weight', pd.Series, 'factor_exposure', pd.DataFrame, 'factor_cov', pd.DataFrame, 'specific_risk', Optional[pd.Series])):
        '''
        :param portfolio_weight: 组合持仓权重, index=股票代码
        :param factor_exposure: 因子暴露矩阵, index=股票代码, columns=因子名
        :param factor_cov: 因子收益率协方差矩阵, index=因子名, columns=因子名
        :param specific_risk: 各股票的特质风险（标准差）, index=股票代码
        '''
        self.portfolio_weight = portfolio_weight.dropna()
        self.factor_exposure = factor_exposure
        self.factor_cov = factor_cov
        self.specific_risk = specific_risk
        common_stocks = self.portfolio_weight.index.intersection(factor_exposure.index)
        self.portfolio_weight = self.portfolio_weight[common_stocks]
        self.factor_exposure = factor_exposure.loc[common_stocks]
    # WARNING: Decompyle incomplete

    
    def run(self = None):
        '''
        执行风险分解。

        :return: 风险分解结果字典
        '''
        w = self.portfolio_weight.values
        X = self.factor_exposure.values
        F = self.factor_cov.values
        portfolio_exposure = w @ X
        factor_var = portfolio_exposure @ F @ portfolio_exposure.T
        self.factor_risk = np.sqrt(max(factor_var, 0))
    # WARNING: Decompyle incomplete

    
    def _calc_marginal_contribution(self = None, w = None, X = None, F = ('w', np.ndarray, 'X', np.ndarray, 'F', np.ndarray)):
        """
        计算各因子的边际风险贡献。

        边际贡献: MC_j = ∂σ/∂w_j = (X'F X w)_j / σ_total
        成分贡献: CC_j = w_j × MC_j，满足 Σ CC_j = σ_total
        """
        pass
    # WARNING: Decompyle incomplete

    
    def risk_budget(self = None):
        '''
        风险预算分析：各因子在总风险中的贡献明细。

        :return: 风险预算 DataFrame
        '''
        pass
    # WARNING: Decompyle incomplete

    from_factor_returns = (lambda cls = None, portfolio_weight = None, factor_exposure = classmethod, factor_returns = (None,), specific_risk = ('portfolio_weight', pd.Series, 'factor_exposure', pd.DataFrame, 'factor_returns', pd.DataFrame, 'specific_risk', Optional[pd.Series], 'return', 'RiskDecomposition'): factor_cov = factor_returns.cov()cls(portfolio_weight, factor_exposure, factor_cov, specific_risk))()
    
    def summary(self = None):
        '''
        返回风险分解汇总 DataFrame。

        :return: 汇总 DataFrame
        '''
        pass
    # WARNING: Decompyle incomplete

    
    def save(self = None, path = None):
        '''保存风险分解结果到 HDF5 文件'''
        summary = self.summary()
        summary.to_hdf(path, key = 'risk_decomposition')
        budget = self.risk_budget()
        budget.to_hdf(path, key = 'risk_budget')
        print(f'''风险分解结果已保存至: {path}''')


# WARNING: Decompyle incomplete
