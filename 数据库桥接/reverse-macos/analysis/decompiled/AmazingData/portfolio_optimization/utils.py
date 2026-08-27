# Source Generated with Decompyle++
# File: utils.pyc (Python 3.12)

'''
组合优化工具模块

提供:
    - EWMAEstimator: EWMA 指数加权权重与协方差估计
    - NeweyWestEstimator: Newey-West 自相关协方差估计
    - EigenAdjuster: 蒙特卡洛特征值调整
    - VolRegimeAdjuster: 波动率偏误调整
    - BayesianShrinkage: 贝叶斯压缩
    - MatrixUtils: 矩阵数值稳定性工具
    - DataAligner: DataFrame 对齐工具
'''
import numpy as np
import pandas as pd
from typing import Optional, Tuple

class EWMAEstimator:
    '''
    EWMA 指数加权移动平均估计器

    提供 EWMA 权重计算和协方差矩阵估计。
    '''
    weight = (lambda half_life = None, window = None: raw_weights = np.power(0.5, np.arange(window) / half_life)raw_weights / raw_weights.sum())()
    covariance = (lambda returns = None, half_life = None: (T, K) = returns.shapeweights = EWMAEstimator.weight(half_life, T)weighted_mean = np.average(returns, axis = 0, weights = weights)demeaned = returns - weighted_meancov = np.zeros((K, K))for t in range(T):
cov += weights[t] * np.outer(demeaned[t], demeaned[t])if weights.sum() ** 2 < 1:
cov /= 1 - weights.sum() ** 2covNone /= cov - 1 / Tcov)()


class NeweyWestEstimator:
    '''
    Newey-West 异方差自相关一致协方差估计器

    对因子收益率序列的时序自相关进行修正：
    F_NW = Gamma_0 + sum_{d=1}^{D} (1 - d/(D+1)) * (Gamma_d + Gamma_d^T)
    '''
    covariance = (lambda returns = None, max_lags = None, half_life = staticmethod: (T, K) = returns.shapeweights = EWMAEstimator.weight(half_life, T)weighted_mean = np.average(returns, axis = 0, weights = weights)demeaned = returns - weighted_meangamma_0 = np.zeros((K, K))for t in range(T):
gamma_0 += weights[t] * np.outer(demeaned[t], demeaned[t])gamma_0 /= 1 - (weights ** 2).sum() if (weights ** 2).sum() < 1 else 1 - 1 / Tcov_nw = gamma_0.copy()# WARNING: Decompyle incomplete
)()


class EigenAdjuster:
    '''
    蒙特卡洛特征值调整器

    通过蒙特卡洛模拟估计采样误差带来的特征值偏误，修正协方差矩阵。
    Ref: Menchero, Wang & Orr (2012)

    步骤:
    1. 对 F_NW 做特征值分解: U_0 * D_0 * U_0^T
    2. 蒙特卡洛模拟：以 F_NW 为真实协方差，生成模拟数据，计算模拟协方差，
       比较真实特征值与模拟特征值的比值得到偏误估计
    3. 尖峰厚尾调整：gamma > 1 放大偏误估计
    '''
    adjust = (lambda cov_matrix = None, T = None, n_simulations = staticmethod, gamma = (100, 1.4, 42), random_seed = ('cov_matrix', np.ndarray, 'T', int, 'n_simulations', int, 'gamma', float, 'random_seed', int, 'return', np.ndarray): K = cov_matrix.shape[0]rng = np.random.RandomState(random_seed)(eigenvalues, eigenvectors) = np.linalg.eigh(cov_matrix)eigenvalues = np.maximum(eigenvalues, 1e-12)U0 = eigenvectorsbias_accumulator = np.zeros(K)for m in range(n_simulations):
simulated_returns = rng.multivariate_normal(mean = np.zeros(K), cov = cov_matrix, size = T)projected_cov = U0.T @ simulated_cov @ U0simulated_eigenvalues = np.diag(projected_cov)safe_ratio = np.divide(simulated_eigenvalues, eigenvalues, out = np.ones_like(eigenvalues), where = eigenvalues > 1e-12)bias_accumulator += safe_ratiomean_bias = bias_accumulator / n_simulationsadjusted_bias = 1 + gamma * (mean_bias - 1)adjusted_eigenvalues = eigenvalues * adjusted_biasadjusted_cov = U0 @ np.diag(adjusted_eigenvalues) @ U0.Tadjusted_cov)()


class VolRegimeAdjuster:
    '''
    波动率偏误调整器

    计算波动率偏误调整系数 lambda：
    B_t = (1/K) * sum_k (f_kt^2 / sigma_kt^2)
    lambda = sqrt(EWMA_mean(B_t) / window)
    '''
    adjustment_factor = (lambda returns = None, predicted_vol = None, window = staticmethod: (T, K) = returns.shapeB_t = np.zeros(T)for t in range(T):
safe_denom = np.where(predicted_vol > 1e-12, predicted_vol, np.nan)ratios = np.divide(returns[t] ** 2, safe_denom ** 2)B_t[t] = np.nanmean(ratios)weights = EWMAEstimator.weight(half_life = window, window = T)lambda_sq = np.average(B_t, weights = weights)np.sqrt(max(lambda_sq, 1e-12)))()


class BayesianShrinkage:
    '''
    贝叶斯压缩器

    按分组向组内加权均值回归：
    sigma_shrink_n = v_n * sigma_n + (1 - v_n) * sigma_bar_sn

    其中 v_n = q * |sigma_n - sigma_bar_sn| / (|sigma_n - sigma_bar_sn| + q * sigma_bar_sn)
    q 为压缩强度系数
    '''
    shrink = (lambda values = None, group_labels = None, group_weights = staticmethod, shrinkage_intensity = (None, 0.3): N = len(values)unique_groups = np.unique(group_labels)# WARNING: Decompyle incomplete
)()


class MatrixUtils:
    '''
    矩阵数值稳定性工具类

    提供矩阵正定性检查和安全的矩阵求逆。
    '''
    ensure_positive_definite = (lambda matrix = None, epsilon = None: try:
np.linalg.cholesky(matrix)matrixexcept np.linalg.LinAlgError:
diag_mean = np.mean(np.diag(matrix))n = matrix.shape[0]reg = epsilon * diag_mean * np.eye(n) if diag_mean > 0 else epsilon * np.eye(n))()
    safe_inverse = (lambda matrix = None, epsilon = None: try:
np.linalg.inv(matrix)except np.linalg.LinAlgError:
n = matrix.shape[0]reg = epsilon * np.eye(n))()


class DataAligner:
    '''
    DataFrame 对齐工具

    将多个 DataFrame 按公共日期和股票代码对齐。
    '''
    align = (lambda : if not dfs:
dfscommon_index = None[0].indexcommon_columns = dfs[0].columnsfor df in dfs[1:]:
common_index = common_index.intersection(df.index)common_columns = common_columns.intersection(df.columns)result = []for df in dfs:
result.append(df.loc[(common_index, common_columns)].copy())tuple(result))()

if __name__ == '__main__':
    import sys
    sys.path.insert(0, 'D:\\AmazingData\\demo')
    np.random.seed(42)
    print('=== EWMA 权重测试 ===')
    w = EWMAEstimator.weight(half_life = 60, window = 252)
    print(f'''权重形状: {w.shape}, 权重和: {w.sum():.6f}, 最近权重: {w[0]:.6f}''')
    print('\n=== EWMA 协方差测试 ===')
    (T, K) = (252, 5)
    fake_returns = np.random.randn(T, K) * 0.01
    cov_ewma = EWMAEstimator.covariance(fake_returns, half_life = 60)
    print(f'''EWMA 协方差形状: {cov_ewma.shape}, 对角元素: {np.diag(cov_ewma)[:3]}''')
    print('\n=== Newey-West 协方差测试 ===')
    cov_nw = NeweyWestEstimator.covariance(fake_returns, max_lags = 2, half_life = 60)
    print(f'''NW 协方差形状: {cov_nw.shape}, 对角元素: {np.diag(cov_nw)[:3]}''')
    print('\n=== 蒙特卡洛特征值调整测试 ===')
    cov_adj = EigenAdjuster.adjust(cov_nw, T = 252, n_simulations = 50, random_seed = 42)
    print(f'''调整后协方差形状: {cov_adj.shape}, 对角元素: {np.diag(cov_adj)[:3]}''')
    print('\n=== 波动率偏误调整测试 ===')
    pred_vol = np.sqrt(np.diag(cov_nw))
    lambda_factor = VolRegimeAdjuster.adjustment_factor(fake_returns, pred_vol, window = 60)
    print(f'''波动率调整系数 lambda: {lambda_factor:.4f}''')
    print('\n=== 贝叶斯压缩测试 ===')
    test_values = np.array([
        0.01,
        0.02,
        0.05,
        0.08,
        0.1,
        0.12,
        0.15,
        0.18,
        0.2,
        0.25])
    test_groups = np.array([
        0,
        0,
        1,
        1,
        1,
        2,
        2,
        2,
        2,
        2])
    test_weights = np.ones(10)
    shrunk = BayesianShrinkage.shrink(test_values, test_groups, test_weights, shrinkage_intensity = 0.3)
    print(f'''原始值: {test_values}''')
    print(f'''压缩值: {shrunk}''')
    print('\n=== 数值稳定性测试 ===')
    mat = np.array([
        [
            1,
            0.999],
        [
            0.999,
            1]])
    inv = MatrixUtils.safe_inverse(mat)
    print(f'''逆矩阵: \n{inv}''')
    print('\n=== 测试通过 ===')
    return None
