# Source Generated with Decompyle++
# File: covariance_adjuster.pyc (Python 3.12)

'''
因子收益率协方差矩阵调整器

实现因子协方差矩阵的四步调整流水线：
    1. EWMA 基础协方差估计 (F^Raw)
    2. Newey-West 时序自相关修正 (F^NW)
    3. 蒙特卡洛特征值调整 (F^Eigen)
    4. 波动率偏误截面缩放 (F^Final)

使用示例:
    adjuster = CovarianceAdjuster(factor_return)
    adjuster.cal_ewma_cov(half_life=60)
    adjuster.cal_newey_west(max_lags=2)
    adjuster.cal_eigen_adjustment(n_simulations=100)
    adjuster.cal_vol_regime_adjustment(window=60)
    # 或一键执行
    adjuster.run_pipeline()
    final_cov = adjuster.final_cov
'''
import numpy as np
import pandas as pd
from typing import Optional

try:
    from AmazingData.portfolio_optimization.utils import EWMAEstimator, NeweyWestEstimator, EigenAdjuster, VolRegimeAdjuster, MatrixUtils
    
    class CovarianceAdjuster:
        '''
    因子收益率协方差矩阵四步调整流水线

    :param factor_return: 因子收益率 DataFrame, index=日期, columns=因子名
    :param freq_scale: 频率放大系数，日频→月频取21，默认 1（不放大）
    '''
        
        def __init__(self = None, factor_return = None, freq_scale = None):
            self.factor_return = factor_return.copy()
            self.freq_scale = freq_scale
            self.factor_names = list(factor_return.columns)
            self.raw_cov = None
            self.nw_cov = None
            self.eigen_cov = None
            self.final_cov = None
            self.lambda_factor = None

        
        def cal_ewma_cov(self = None, half_life = None):
            '''
        EWMA 指数加权移动平均计算因子协方差矩阵 F^Raw

        :param half_life: EWMA 半衰期，默认 60 天
        :return: self
        '''
            returns = self.factor_return.values
            self.raw_cov = EWMAEstimator.covariance(returns, half_life = half_life)
            return self

        
        def cal_newey_west(self = None, max_lags = None, half_life = None):
            '''
        Newey-West 异方差自相关一致估计

        F^NW = Gamma_0 + sum_{d=1}^{D} (1 - d/(D+1)) * (Gamma_d + Gamma_d^T)

        :param max_lags: 最大滞后期 D，默认 2
        :param half_life: EWMA 半衰期
        :return: self
        '''
            pass
        # WARNING: Decompyle incomplete

        
        def cal_eigen_adjustment(self = None, n_simulations = None, gamma = None, random_seed = (100, 1.4, 42)):
            '''
        蒙特卡洛特征值调整 (Menchero, Wang & Orr 2012)

        通过蒙特卡洛模拟修正采样误差导致的特征值偏误。

        :param n_simulations: 蒙特卡洛模拟次数，默认 100
        :param gamma: 尖峰厚尾调整系数，默认 1.4
        :param random_seed: 随机种子
        :return: self
        '''
            pass
        # WARNING: Decompyle incomplete

        
        def cal_vol_regime_adjustment(self = None, window = None):
            '''
        波动率偏误截面调整

        lambda_t = (1/K) * sum_k (f_kt^2 / sigma_kt^2)
        lambda = EWMA_mean(lambda_t) 开根号
        F^Final = lambda^2 * F^Eigen

        :param window: EWMA 平滑窗口
        :return: self
        '''
            pass
        # WARNING: Decompyle incomplete

        
        def run_pipeline(self, half_life, max_lags = None, n_simulations = None, gamma = None, window = (60, 2, 100, 1.4, 60, 42), random_seed = ('half_life', int, 'max_lags', int, 'n_simulations', int, 'gamma', float, 'window', int, 'random_seed', int, 'return', 'CovarianceAdjuster')):
            '''
        一键执行四步调整流水线

        :param half_life: EWMA 半衰期
        :param max_lags: NW 最大滞后期
        :param n_simulations: 蒙特卡洛模拟次数
        :param gamma: 尖峰厚尾调整系数
        :param window: 波动率偏误平滑窗口
        :param random_seed: 随机种子
        :return: self
        '''
            self._half_life = half_life
            self._max_lags = max_lags
            self._gamma = gamma
            self.cal_ewma_cov(half_life = half_life)
            self.cal_newey_west(max_lags = max_lags, half_life = half_life)
            self.cal_eigen_adjustment(n_simulations = n_simulations, gamma = gamma, random_seed = random_seed)
            self.cal_vol_regime_adjustment(window = window)
            return self

        cov_matrix = (lambda self = None: self.final_cov)()
        cov_df = (lambda self = None: pass# WARNING: Decompyle incomplete
)()
        
        def bias_statistic(self = None, forward_returns = None, forward_window = None):
            '''
        计算偏差统计量 (Bias Test)

        b_t = r_{t→t+q} / sigma_t
        Bias = std(b_t) over test window

        :param forward_returns: 未来 q 日收益率，index=日期, columns=因子名
        :param forward_window: 预测时间长度 q，默认 21 天
        :return: 偏差统计量
        '''
            pass
        # WARNING: Decompyle incomplete

        
        def summary(self = None):
            '''返回协方差调整汇总指标'''
            result = { }
            result['half_life'] = getattr(self, '_half_life', '-')
            result['max_lags'] = getattr(self, '_max_lags', '-')
            result['gamma'] = getattr(self, '_gamma', '-')
        # WARNING: Decompyle incomplete


    if __name__ == '__main__':
        import sys
        sys.path.insert(0, 'D:\\AmazingData\\demo')
        np.random.seed(42)
        print('=== 协方差调整器测试 ===')
        dates = pd.date_range('2020-01-01', periods = 252, freq = 'B')
        factor_names = [
            'size',
            'value',
            'momentum',
            'quality',
            'volatility']
        K = len(factor_names)
        factor_return = pd.DataFrame(index = dates, columns = factor_names, dtype = float)
        for i, name in enumerate(factor_names):
            series = np.zeros(252)
            series[0] = np.random.randn() * 0.01
            for t in range(1, 252):
                series[t] = 0.3 * series[t - 1] + np.random.randn() * 0.01
            factor_return[name] = series
        print(f'''因子收益率形状: {factor_return.shape}''')
        adjuster = CovarianceAdjuster(factor_return, freq_scale = 21)
        adjuster.cal_ewma_cov(half_life = 60)
        print(f'''\n步骤1 - EWMA 协方差:\n{adjuster.raw_cov}''')
        adjuster.cal_newey_west(max_lags = 2)
        print(f'''\n步骤2 - NW 协方差:\n{adjuster.nw_cov}''')
        adjuster.cal_eigen_adjustment(n_simulations = 50)
        print(f'''\n步骤3 - 特征值调整后协方差:\n{adjuster.eigen_cov}''')
        adjuster.cal_vol_regime_adjustment(window = 60)
        print(f'''\n步骤4 - 波动率调整系数: {adjuster.lambda_factor:.4f}''')
        print(f'''最终协方差 (月频放大x21):\n{adjuster.final_cov}''')
        summary = adjuster.summary()
        print('\n汇总指标:')
        for k, v in summary.items():
            print(f'''  {k}: {v}''')
        print('\n=== 测试通过 ===')
        return None
    return None
except ImportError:
    EWMAEstimator = globals().get('EWMAEstimator', None)
    NeweyWestEstimator = globals().get('NeweyWestEstimator', None)
    EigenAdjuster = globals().get('EigenAdjuster', None)
    VolRegimeAdjuster = globals().get('VolRegimeAdjuster', None)
    MatrixUtils = globals().get('MatrixUtils', None)
    continue

