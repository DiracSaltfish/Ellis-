# Source Generated with Decompyle++
# File: __init__.pyc (Python 3.12)

'''
绩效归因分析算法模块

独立于报告生成，纯算法层。可被 skill 调用。

包含两大归因体系:
    1. 多因子归因 — 依据《多因子归因模型概述》（恒生电子，2017）实现
       - 因子溢价估计（OLS/WLS截面回归 + VIF检验 + t值显著性）
       - 单期/多期超额收益分解（因子收益 + 特殊收益 + 调仓收益）
    2. Barra 因子归因 — 基于结构化风险模型的风格/行业/特质收益分解

其他功能:
    - Brinson 归因: 配置效应/选择效应/交互效应（BHB/BF/几何法）
    - 绩效指标计算: 封装 NetValueAnalyzer，滚动指标和日历指标
    - 风险分解: 共同因子风险/特质风险/边际贡献
    - 绩效报告汇总: 一站式分析接口，链式调用

子模块:
    - attribution_constant: 枚举常量
    - factor_premium_estimator: 因子溢价估计（文档第3章）
    - multi_factor_attribution: 多因子归因分析（文档第4章）
    - barra_attribution: Barra 因子归因分析
    - brinson_attribution: Brinson 归因分析
    - performance_metrics: 绩效指标计算
    - risk_decomposition: 风险分解
    - performance_report: 绩效报告汇总
'''
from AmazingData.performance_attribution.attribution_constant import BrinsonMethod, AttributionPeriod, DecompositionType, WlsWeightMethod, BarraFactorType
from AmazingData.performance_attribution.factor_premium_estimator import FactorPremiumEstimator
from AmazingData.performance_attribution.multi_factor_attribution import MultiFactorAttribution
from AmazingData.performance_attribution.brinson_attribution import BrinsonAttribution
from AmazingData.performance_attribution.performance_metrics import PerformanceMetrics
from AmazingData.performance_attribution.risk_decomposition import RiskDecomposition
from AmazingData.performance_attribution.performance_report import PerformanceReport
__all__ = [
    'BrinsonMethod',
    'AttributionPeriod',
    'DecompositionType',
    'WlsWeightMethod',
    'BarraFactorType',
    'FactorPremiumEstimator',
    'MultiFactorAttribution',
    'BrinsonAttribution',
    'PerformanceMetrics',
    'RiskDecomposition',
    'PerformanceReport']
