# Source Generated with Decompyle++
# File: cross_section_function.pyc (Python 3.12)

import warnings
import pandas as pd
import numpy as np
from AmazingData.operator.base_cross_section import _numpy_rank_axis1, _cs_cor_numba, _cs_corr_numba, _cs_pct_rank_numba
warnings.filterwarnings('ignore')

class CrossSectionFunction(object):
    '''
    截面函数
    用于计算同一交易日内多个标的之间的统计指标
    输入数据为DataFrame,行为日期,列为标的代码
    '''
    CSCOV = (lambda x = None, y = None: arr_x = x.values.astype(np.float64)arr_y = y.values.astype(np.float64)result = _cs_cor_numba(arr_x, arr_y)pd.Series(result, index = x.index))()
    CSCOUNT = (lambda x = None: arr = x.values.astype(np.float64)result = np.sum(~np.isnan(arr), axis = 1)pd.Series(result, index = x.index))()
    CSQUANTILE = (lambda x = None, n = None: arr = x.values.astype(np.float64)result = np.nanpercentile(arr, n * 100, axis = 1)pd.Series(result, index = x.index))()
    CSRANK = (lambda x = None, ascending = None: arr = x.values.astype(np.float64)result = _numpy_rank_axis1(arr, ascending)pd.DataFrame(result, index = x.index, columns = x.columns))()
    CSSTD = (lambda x = None: arr = x.values.astype(np.float64)result = np.nanstd(arr, axis = 1, ddof = 0)pd.Series(result, index = x.index))()
    CSSUM = (lambda x = None: arr = x.values.astype(np.float64)result = np.nansum(arr, axis = 1)pd.Series(result, index = x.index))()
    CSVAR = (lambda x = None: arr = x.values.astype(np.float64)result = np.nanvar(arr, axis = 1, ddof = 0)pd.Series(result, index = x.index))()
    CSPCTRANK = (lambda x = None: arr = x.values.astype(np.float64)result = _cs_pct_rank_numba(arr)pd.DataFrame(result, index = x.index, columns = x.columns))()
    CSMEAN = (lambda x = None: arr = x.values.astype(np.float64)result = np.nanmean(arr, axis = 1)pd.Series(result, index = x.index))()
    CSMAX = (lambda x = None: arr = x.values.astype(np.float64)result = np.nanmax(arr, axis = 1)pd.Series(result, index = x.index))()
    CSCORR = (lambda x = None, y = None: arr_x = x.values.astype(np.float64)arr_y = y.values.astype(np.float64)result = _cs_corr_numba(arr_x, arr_y)pd.Series(result, index = x.index))()
    CSMIN = (lambda x = None: arr = x.values.astype(np.float64)result = np.nanmin(arr, axis = 1)pd.Series(result, index = x.index))()
    CSMEDIAN = (lambda x = None: arr = x.values.astype(np.float64)result = np.nanmedian(arr, axis = 1)pd.Series(result, index = x.index))()
    CSZSCORE = (lambda x = None: arr = x.values.astype(np.float64)mean = np.nanmean(arr, axis = 1, keepdims = True)std = np.nanstd(arr, axis = 1, ddof = 0, keepdims = True)result = (arr - mean) / stdpd.DataFrame(result, index = x.index, columns = x.columns))()
    CSNORMALIZE = (lambda x = None: arr = x.values.astype(np.float64)min_val = np.nanmin(arr, axis = 1, keepdims = True)max_val = np.nanmax(arr, axis = 1, keepdims = True)result = (arr - min_val) / (max_val - min_val)pd.DataFrame(result, index = x.index, columns = x.columns))()
    CSDEMEAN = (lambda x = None: arr = x.values.astype(np.float64)mean = np.nanmean(arr, axis = 1, keepdims = True)result = arr - meanpd.DataFrame(result, index = x.index, columns = x.columns))()

if __name__ == '__main__':
    return None
