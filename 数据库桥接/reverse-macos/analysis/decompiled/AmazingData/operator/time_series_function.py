# Source Generated with Decompyle++
# File: time_series_function.pyc (Python 3.12)

import warnings
import pandas as pd
import numpy as np
from scipy.signal import lfilter
from base_time_series import _hhv_numba, _llv_numba, _ma_numba, _sum_numba, _ema_numba, _barslast_numba, _barslasts_numba, _barsnext_numba, _barssincen_numba, _barssince_numba, _barslastcount_numba, _hhvbars_numba, _llvbars_numba, _hod_numba, _lod_numba, _sumbars_numba, _sumbarsx_numba, _sma_numba, _tma_numba, _mema_numba, _dma_numba, _ama_numba, _filter_numba, _filterx_numba, _longcross_numba, _upnday_numba, _downnday_numba, _nday_numba, _existr_numba, _last_numba, _hhvllv_numba, _sar_numba
warnings.filterwarnings('ignore')

class TimeSeriesFunction(object):
    '''
    时序函数
    用于时间序列数据的处理，包括引用、移动平均、条件统计等
    '''
    BARSTATUS = (lambda x = None: n = len(x)result = np.zeros(n)if n > 0:
result[0] = 1result[-1] = 2pd.Series(result, index = x.index))()
    CURRBARSCOUNT = (lambda x = None: n = len(x)result = np.arange(n, 0, -1)pd.Series(result, index = x.index))()
    TOTALBARSCOUNT = (lambda x = None: n = len(x)result = np.arange(1, n + 1)pd.Series(result, index = x.index))()
    BARSLAST = (lambda x = None: arr = x.values.astype(np.bool_)result = _barslast_numba(arr)pd.Series(result, index = x.index))()
    BARSLASTS = (lambda x = None, n = None: arr = x.values.astype(np.bool_)result = _barslasts_numba(arr, n)pd.Series(result, index = x.index))()
    BARSNEXT = (lambda x = None: arr = x.values.astype(np.bool_)result = _barsnext_numba(arr)pd.Series(result, index = x.index))()
    BARSSINCEN = (lambda x = None, n = None: arr = x.values.astype(np.bool_)result = _barssincen_numba(arr, n)pd.Series(result, index = x.index))()
    BARSSINCE = (lambda x = None: arr = x.values.astype(np.bool_)result = _barssince_numba(arr)pd.Series(result, index = x.index))()
    COUNT = (lambda x = None, n = None: arr = x.values.astype(np.bool_).astype(np.float64)if n <= 0:
result = np.nancumsum(arr)else:
cumsum = np.nancumsum(arr)result = np.empty(len(arr))for i in range(len(arr)):
start = i - nif start < 0:
result[i] = cumsum[i]continueresult[i] = cumsum[i] - cumsum[start]pd.Series(result, index = x.index))()
    BARSLASTCOUNT = (lambda x = None: arr = x.values.astype(np.bool_)result = _barslastcount_numba(arr)pd.Series(result, index = x.index))()
    HHV = (lambda x = None, n = None: arr = x.values.astype(np.float64)result = _hhv_numba(arr, n)pd.Series(result, index = x.index))()
    HHVBARS = (lambda x = None, n = None: arr = x.values.astype(np.float64)result = _hhvbars_numba(arr, n)pd.Series(result, index = x.index))()
    HOD = (lambda x = None, n = None: arr = x.values.astype(np.float64)result = _hod_numba(arr, n)pd.Series(result, index = x.index))()
    LLV = (lambda x = None, n = None: arr = x.values.astype(np.float64)result = _llv_numba(arr, n)pd.Series(result, index = x.index))()
    LLVBARS = (lambda x = None, n = None: arr = x.values.astype(np.float64)result = _llvbars_numba(arr, n)pd.Series(result, index = x.index))()
    LOD = (lambda x = None, n = None: arr = x.values.astype(np.float64)result = _lod_numba(arr, n)pd.Series(result, index = x.index))()
    HHVLLV = (lambda x = None, t = None, n1 = staticmethod, n2 = ('x', pd.Series, 't', int, 'n1', int, 'n2', int, 'return', pd.Series): arr = x.values.astype(np.float64)result = _hhvllv_numba(arr, t, n1, n2)pd.Series(result, index = x.index))()
    REVERSE = (lambda x = None: pd.Series(-x, index = x.index))()
    REF = (lambda x = None, n = None: arr = x.values.astype(np.float64)result = np.empty(len(arr))result[:] = np.nanif n >= 0 and n < len(arr):
result[n:] = arr[:-n] if n > 0 else arrpd.Series(result, index = x.index))()
    REFX = (lambda x = None, n = None: arr = x.values.astype(np.float64)result = np.empty(len(arr))result[:] = np.nanif n >= 0 and n < len(arr):
result[:-n] = arr[n:] if n > 0 else arrpd.Series(result, index = x.index))()
    REFV = (lambda x = None, n = None: arr = x.values.astype(np.float64)result = np.empty(len(arr))result[:] = np.nanif n >= 0 and n < len(arr):
result[n:] = arr[:-n] if n > 0 else arrlast_valid = np.nanfor i in range(len(result)):
if not np.isnan(result[i]):
last_valid = result[i]continueif np.isnan(last_valid):
continueresult[i] = last_validpd.Series(result, index = x.index))()
    REFXV = (lambda x = None, n = None: arr = x.values.astype(np.float64)result = np.empty(len(arr))result[:] = np.nanif n >= 0 and n < len(arr):
result[:-n] = arr[n:] if n > 0 else arrlast_valid = np.nanfor i in range(len(result) - 1, -1, -1):
if not np.isnan(result[i]):
last_valid = result[i]continueif np.isnan(last_valid):
continueresult[i] = last_validpd.Series(result, index = x.index))()
    SHIFT = (lambda x = None, n = None: arr = x.values.astype(np.float64)result = np.empty(len(arr))result[:] = np.nanif n >= 0 and n < len(arr):
result[n:] = arr[:-n] if n > 0 else arrpd.Series(result, index = x.index))()
    SUM = (lambda x = None, n = None: arr = x.values.astype(np.float64)result = _sum_numba(arr, n)pd.Series(result, index = x.index))()
    MULAR = (lambda x = None, n = None: arr = x.values.astype(np.float64)length = len(arr)if n <= 0:
result = np.cumprod(arr)else:
result = np.empty(length)for i in range(length):
start = max(0, (i - n) + 1)result[i] = np.prod(arr[start:i + 1])pd.Series(result, index = x.index))()
    SUMBARS = (lambda x = None, a = None: arr = x.values.astype(np.float64)result = _sumbars_numba(arr, a)pd.Series(result, index = x.index))()
    SUMBARSX = (lambda x = None, a = None: arr = x.values.astype(np.float64)result = _sumbarsx_numba(arr, a)pd.Series(result, index = x.index))()
    MA = (lambda x = None, n = None: arr = x.values.astype(np.float64)result = _ma_numba(arr, n)pd.Series(result, index = x.index))()
    SMA = (lambda x = None, n = None, m = staticmethod: arr = x.values.astype(np.float64)result = _sma_numba(arr, n, m)pd.Series(result, index = x.index))()
    TMA = (lambda x = None, a = None, b = staticmethod: arr = x.values.astype(np.float64)result = _tma_numba(arr, a, b)pd.Series(result, index = x.index))()
    MEMA = (lambda x = None, n = None: arr = x.values.astype(np.float64)result = _mema_numba(arr, n)pd.Series(result, index = x.index))()
    EMA = (lambda x = None, n = None: arr = x.values.astype(np.float64)result = _ema_numba(arr, n)pd.Series(result, index = x.index))()
    EXPMEMA = (lambda x = None, n = None: arr = x.values.astype(np.float64)result = _ema_numba(arr, n)result[:n - 1] = np.nanpd.Series(result, index = x.index))()
    WMA = (lambda x = None, n = None: arr = x.values.astype(np.float64)length = len(arr)result = np.empty(length)max_weights = np.arange(1, n + 1, dtype = np.float64)for i in range(length):
win_size = min(i + 1, n)start = (i - win_size) + 1window = arr[start:i + 1]weights = max_weights[:win_size]weights_sum = win_size * (win_size + 1) / 2result[i] = np.sum(window * weights) / weights_sumpd.Series(result, index = x.index))()
    DMA = (lambda x = None, a = None: arr_x = x.values.astype(np.float64)arr_a = a.values.astype(np.float64)result = _dma_numba(arr_x, arr_a)pd.Series(result, index = x.index))()
    AMA = (lambda x = None, a = None: arr_x = x.values.astype(np.float64)if isinstance(a, pd.Series):
arr_a = a.values.astype(np.float64)else:
arr_a = np.full(len(arr_x), a, dtype = np.float64)result = _ama_numba(arr_x, arr_a)pd.Series(result, index = x.index))()
    FILTER = (lambda x = None, n = None: arr = x.values.astype(np.bool_)result = _filter_numba(arr, n)pd.Series(result, index = x.index))()
    FILTERX = (lambda x = None, n = None: arr = x.values.astype(np.bool_)result = _filterx_numba(arr, n)pd.Series(result.astype(int), index = x.index))()
    TR = (lambda high = None, low = None, close = staticmethod: prev_close = close.shift(1)tr1 = high - lowtr2 = np.abs(high - prev_close)tr3 = np.abs(low - prev_close)result = np.maximum(np.maximum(tr1, tr2), tr3)result = pd.Series(result, index = high.index)result.iloc[0] = tr1.iloc[0]result)()
    RANGE = (lambda a = None, b = None, c = staticmethod: result = ((a > b) & (a < c)).astype(int)pd.Series(result, index = a.index))()
    CROSS = (lambda a = None, b = None: arr_a = a.values.astype(np.float64)arr_b = b.values.astype(np.float64)length = len(arr_a)result = np.zeros(length)for i in range(1, length):
if not arr_a[i - 1] <= arr_b[i - 1]:
continueif not arr_a[i] > arr_b[i]:
continueresult[i] = 1pd.Series(result, index = a.index))()
    LONGCROSS = (lambda a = None, b = None, n = staticmethod: arr_a = a.values.astype(np.float64)arr_b = b.values.astype(np.float64)result = _longcross_numba(arr_a, arr_b, n)pd.Series(result, index = a.index))()
    UPNDAY = (lambda x = None, n = None: arr = x.values.astype(np.float64)result = _upnday_numba(arr, n)pd.Series(result, index = x.index))()
    DOWNNDAY = (lambda x = None, n = None: arr = x.values.astype(np.float64)result = _downnday_numba(arr, n)pd.Series(result, index = x.index))()
    NDAY = (lambda x = None, y = None, n = staticmethod: arr_x = x.values.astype(np.float64)arr_y = y.values.astype(np.float64)result = _nday_numba(arr_x, arr_y, n)pd.Series(result, index = x.index))()
    EXIST = (lambda x = None, n = None: arr = x.values.astype(np.bool_)length = len(arr)result = np.zeros(length, dtype = np.float64)window_sum = 0for i in range(length):
window_sum += arr[i]if i >= n:
window_sum -= arr[i - n]result[i] = 1 if window_sum > 0 else 0pd.Series(result, index = x.index))()
    EXISTR = (lambda x = None, a = None, b = staticmethod: arr = x.values.astype(np.bool_)result = _existr_numba(arr, a, b)pd.Series(result, index = x.index))()
    EVERY = (lambda x = None, n = None: arr = x.values.astype(np.bool_)length = len(arr)result = np.zeros(length, dtype = np.float64)window_sum = 0for i in range(length):
window_sum += arr[i]if i >= n:
window_sum -= arr[i - n]window_size = min(i + 1, n)result[i] = 1 if window_sum == window_size else 0pd.Series(result, index = x.index))()
    LAST = (lambda x = None, a = None, b = staticmethod: arr = x.values.astype(np.bool_)result = _last_numba(arr, a, b)pd.Series(result, index = x.index))()
    CUMSUM = (lambda x = None: arr = x.values.astype(np.float64)result = np.nancumsum(arr)pd.Series(result, index = x.index))()
    SAR = (lambda high, low = None, close = None, n = staticmethod, step = (4, 0.02, 0.2), max_af = ('high', pd.Series, 'low', pd.Series, 'close', pd.Series, 'n', int, 'step', float, 'max_af', float, 'return', pd.Series): h = high.values.astype(np.float64)l = low.values.astype(np.float64)c = close.values.astype(np.float64)result = _sar_numba(h, l, c, n, step, max_af)pd.Series(result, index = close.index))()

if __name__ == '__main__':
    return None
