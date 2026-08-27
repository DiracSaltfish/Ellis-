# Source Generated with Decompyle++
# File: __init__.pyc (Python 3.12)

'''
组合优化算法模块

独立于报告生成，纯算法层。可被 skill 调用。

参考: 《选股秘籍大作初成版》第四章「风险组合优化」

核心算法流程:
    1. 风险因子收益率求解 (OLS/WLS)
    2. 因子收益率协方差矩阵调整 (EWMA → NW → Eigen → VolRegime)
    3. 特异性收益方差矩阵调整 (NW → 结构化 → 贝叶斯压缩 → 偏误调整)
    4. 组合优化求解 (QP/闭式解)

子模块:
    - optimization_constant: 枚举常量
    - utils: 工具类 (EWMAEstimator/NeweyWestEstimator/EigenAdjuster/VolRegimeAdjuster/BayesianShrinkage/MatrixUtils/DataAligner)
    - factor_return_solver: 风险因子收益率求解
    - covariance_adjuster: 因子收益率协方差矩阵调整
    - specific_risk_adjuster: 特异性收益方差矩阵调整
    - portfolio_optimizer: 组合优化求解器
'''
from AmazingData.portfolio_optimization.optimization_constant import OptimizeObjective, FactorReturnMethod, CovAdjustMethod, SpecificRiskAdjustMethod, ConstraintType, RiskModel, SolverMethod
from AmazingData.portfolio_optimization.utils import EWMAEstimator, NeweyWestEstimator, EigenAdjuster, VolRegimeAdjuster, BayesianShrinkage, MatrixUtils, DataAligner
from AmazingData.portfolio_optimization.factor_return_solver import FactorReturnSolver
from AmazingData.portfolio_optimization.covariance_adjuster import CovarianceAdjuster
from AmazingData.portfolio_optimization.specific_risk_adjuster import SpecificRiskAdjuster
from AmazingData.portfolio_optimization.portfolio_optimizer import PortfolioOptimizer
__all__ = [
    'OptimizeObjective',
    'FactorReturnMethod',
    'CovAdjustMethod',
    'SpecificRiskAdjustMethod',
    'ConstraintType',
    'RiskModel',
    'SolverMethod',
    'EWMAEstimator',
    'NeweyWestEstimator',
    'EigenAdjuster',
    'VolRegimeAdjuster',
    'BayesianShrinkage',
    'MatrixUtils',
    'DataAligner',
    'FactorReturnSolver',
    'CovarianceAdjuster',
    'SpecificRiskAdjuster',
    'PortfolioOptimizer']
