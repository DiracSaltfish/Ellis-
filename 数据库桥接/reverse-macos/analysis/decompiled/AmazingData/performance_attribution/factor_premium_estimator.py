# Source Generated with Decompyle++
# File: factor_premium_estimator.pyc (Python 3.12)

__doc__ = "\n因子溢价估计模块\n\n核心功能:\n    1. OLS 横截面回归估计因子溢价 — 公式 f = (X'X)^(-1)X'r\n    2. 迭代 WLS 消除异方差性 — 初始权重 → 残差修正 → 重复迭代\n    3. VIF 多重共线性检验 — VIF = 1/(1-R²)，VIF > 5 为强共线性\n    4. t 值显著性统计 — t>2 和 t<-2 占比\n    5. 行业因子作为虚拟变量（0/1敞口），引入时去掉截距项\n\n关键命题（附录证明）:\n    - 命题1: 截距因子近似为等权投资的纯市场组合收益\n    - 命题2: 权重向量 w_j 可构造因子 j 的单位暴露零投资组合\n"
import numpy as np
import pandas as pd
from typing import Optional, Tuple, Dict
from statsmodels.api import api as sm
from AmazingData.performance_attribution.attribution_constant import WlsWeightMethod

class FactorPremiumEstimator:
    '''
    横截面因子溢价估计器。

    逐日做横截面回归: r = X·f + ε，估计因子溢价 f。

    使用示例:
        estimator = FactorPremiumEstimator(
            factor_exposure, stock_return, industry_dummies, market_cap
        )
        estimator.run_ols()
        estimator.calc_vif()
        estimator.calc_t_statistics()
        print(estimator.factor_premium)  # 因子溢价序列
        print(estimator.vif_result)      # VIF 检验结果
        print(estimator.t_statistics)    # t 值统计
    '''
    
    def __init__(self, factor_exposure = None, stock_return = None, industry_dummies = None, market_cap = (None, None, True), include_intercept = ('factor_exposure', pd.DataFrame, 'stock_return', pd.DataFrame, 'industry_dummies', Optional[pd.DataFrame], 'market_cap', Optional[pd.DataFrame], 'include_intercept', bool)):
        '''
        :param factor_exposure: 因子暴露（风格因子）, index=日期, columns=股票代码, values=标准化后的暴露值
        :param stock_return: 股票收益率, index=日期, columns=股票代码, 截面区间收益率
        :param industry_dummies: 行业哑变量, index=股票代码, columns=行业代码, values=0或1
        :param market_cap: 流通市值, index=日期, columns=股票代码, 用于WLS权重
        :param include_intercept: 是否包含截距项，引入行业哑变量时设为 False
        '''
        self.factor_exposure = factor_exposure
        self.stock_return = stock_return
        self.industry_dummies = industry_dummies
        self.market_cap = market_cap
        self.include_intercept = include_intercept
        common_dates = factor_exposure.index.intersection(stock_return.index)
        common_stocks = factor_exposure.columns.intersection(stock_return.columns)
        self.factor_exposure = factor_exposure.loc[(common_dates, common_stocks)]
        self.stock_return = stock_return.loc[(common_dates, common_stocks)]
    # WARNING: Decompyle incomplete

    
    def run_ols(self = None):
        """
        OLS 横截面回归估计因子溢价。

        逐日回归: r_date = X_date · f_date + ε_date
        估计公式: f = (X'X)^(-1)X'r  （文档公式 2.3）

        :return: 因子溢价 DataFrame, index=日期, columns=因子名
        """
        n_dates = len(self.factor_exposure)
        factor_premium_list = []
        t_value_list = []
        residual_list = []
    # WARNING: Decompyle incomplete

    
    def run_wls(self = None, weight_method = None, iterations = None):
        '''
        迭代 WLS 消除异方差性。

        文档第3章技术细节(3):
        初始权重 → 回归 → 用残差修正权重 → 重复迭代 → 使异方差性尽可能小

        :param weight_method: 初始权重方法
        :param iterations: 迭代次数，默认3次
        :return: 因子溢价 DataFrame
        '''
        n_dates = len(self.factor_exposure)
        factor_premium_list = []
        t_value_list = []
    # WARNING: Decompyle incomplete

    
    def _get_initial_weights(self = None, date = None, stocks = None, method = ('method', WlsWeightMethod, 'return', Optional[np.ndarray])):
        '''获取初始权重'''
        if method == WlsWeightMethod.EQUAL:
            return None
    # WARNING: Decompyle incomplete

    
    def calc_vif(self = None):
        '''
        VIF（方差膨胀因子）多重共线性检验。

        文档第3章技术细节(2):
        对因子 i 的暴露构造回归: X_i = Σ β_j·X_j + ε，计算 R²_i
        VIF_i = 1 / (1 - R²_i)
        VIF > 5 表示存在较强共线性

        :return: {因子名: VIF值} 字典
        '''
        pass
    # WARNING: Decompyle incomplete

    
    def calc_t_statistics(self = None):
        """
        t 值显著性统计。

        文档第3章技术细节(4):
        统计 t>2 和 t<-2 的占比。
        若因子对股价的影响完全随机，占比应 < 5%。
        通过 t>2 和 t<-2 分别占比体现因子在同一方向上影响的持续性。

        :return: {因子名: {'t_value_mean': , 't_gt_2_ratio': , 't_lt_neg2_ratio': , 'abs_t_gt_2_ratio': }} 字典
        """
        pass
    # WARNING: Decompyle incomplete

    
    def summary(self = None):
        '''
        返回因子溢价估计的汇总结果。

        :return: 汇总 DataFrame, index=因子名, columns=[溢价均值, 溢价标准差, 溢价IR, t均值, |t|>2占比, VIF]
        '''
        rows = []
    # WARNING: Decompyle incomplete

    
    def save(self = None, path = None):
        '''保存因子溢价估计结果到 HDF5 文件'''
        pass
    # WARNING: Decompyle incomplete


# WARNING: Decompyle incomplete
