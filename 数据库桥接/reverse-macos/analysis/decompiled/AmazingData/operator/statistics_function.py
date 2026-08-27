# Source Generated with Decompyle++
# File: statistics_function.pyc (Python 3.12)

import warnings
import pandas as pd
import numpy as np
from base_statistics import _rolling_std_fast, _rolling_var_fast, _rolling_corr_fast, _rolling_apply_numba, _rolling_avedev, _rolling_devsq, _rolling_forcast, _rolling_slope, _rolling_covar, _rolling_relate, _rolling_beta, _rolling_kurtosis, _rolling_skew, _rolling_quantile
warnings.filterwarnings('ignore')

def _sliding_window_view(arr = None, window = None):
    '''创建滑动窗口视图，用于向量化计算（只读视图）'''
    if len(arr) < window:
        return np.empty((0, window), dtype = arr.dtype)
    shape = ((None(arr) - window) + 1, window)
    strides = (arr.strides[0], arr.strides[0])
    return np.lib.stride_tricks.as_strided(arr, shape = shape, strides = strides)


class StatisticsFunction(object):
    '''
    统计函数
    用于计算时序数据的统计指标，如标准差、方差、相关系数等
    '''
    AVEDEV = (lambda x = None, n = None: arr = x.values.astype(np.float64)result = _rolling_avedev(arr, n)pd.Series(result, index = x.index))()
    DEVSQ = (lambda x = None, n = None: arr = x.values.astype(np.float64)result = _rolling_devsq(arr, n)pd.Series(result, index = x.index))()
    FORCAST = (lambda x = None, n = None: arr = x.values.astype(np.float64)result = _rolling_forcast(arr, n)pd.Series(result, index = x.index))()
    SLOPE = (lambda x = None, n = None: arr = x.values.astype(np.float64)result = _rolling_slope(arr, n)pd.Series(result, index = x.index))()
    STD = (lambda x = None, n = None: arr = x.values.astype(np.float64)result = _rolling_apply_numba(arr, n, 1, 1)pd.Series(result, index = x.index))()
    STDP = (lambda x = None, n = None: arr = x.values.astype(np.float64)result = _rolling_std_fast(arr, n, 0)pd.Series(result, index = x.index))()
    STDDEV = (lambda x = None, n = None: arr = x.values.astype(np.float64)result = _rolling_std_fast(arr, n, 0)pd.Series(result, index = x.index))()
    VAR = (lambda x = None, n = None: arr = x.values.astype(np.float64)result = _rolling_var_fast(arr, n, 1)pd.Series(result, index = x.index))()
    VARP = (lambda x = None, n = None: arr = x.values.astype(np.float64)result = _rolling_var_fast(arr, n, 0)pd.Series(result, index = x.index))()
    COVAR = (lambda x = None, y = None, n = staticmethod: arr_x = x.values.astype(np.float64)arr_y = y.values.astype(np.float64)result = _rolling_covar(arr_x, arr_y, n)pd.Series(result, index = x.index))()
    RELATE = (lambda x = None, y = None, n = staticmethod: arr_x = x.values.astype(np.float64)arr_y = y.values.astype(np.float64)result = _rolling_corr_fast(arr_x, arr_y, n)pd.Series(result, index = x.index))()
    BETA = (lambda x = None, benchmark = None, n = staticmethod: arr_x = x.values.astype(np.float64)arr_b = benchmark.values.astype(np.float64)result = _rolling_beta(arr_x, arr_b, n)pd.Series(result, index = x.index))()
    BETAEX = (lambda x = None, y = None, n = staticmethod: arr_x = x.values.astype(np.float64)arr_y = y.values.astype(np.float64)result = _rolling_beta(arr_x, arr_y, n)pd.Series(result, index = x.index))()
    KURTOSIS = (lambda x = None, n = None: arr = x.values.astype(np.float64)result = _rolling_kurtosis(arr, n)pd.Series(result, index = x.index))()
    SKEW = (lambda x = None, n = None: arr = x.values.astype(np.float64)result = _rolling_skew(arr, n)pd.Series(result, index = x.index))()
    MEAN = (lambda x = None, n = None: arr = x.values.astype(np.float64)length = len(arr)result = np.empty(length)cumsum = np.nancumsum(arr)for i in range(length):
start = max(0, (i - n) + 1)if start == 0:
result[i] = cumsum[i] / (i + 1)continueresult[i] = (cumsum[i] - cumsum[start - 1]) / ((i - start) + 1)pd.Series(result, index = x.index))()
    MEDIAN = (lambda x = None, n = None: arr = x.values.astype(np.float64)result = _rolling_apply_numba(arr, n, 1, 4)pd.Series(result, index = x.index))()
    QUANTILE = (lambda x = None, n = None, m = staticmethod: arr = x.values.astype(np.float64)result = _rolling_quantile(arr, n, m)pd.Series(result, index = x.index))()

if __name__ == '__main__':
    return None
