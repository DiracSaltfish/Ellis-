# Source Generated with Decompyle++
# File: math_function.pyc (Python 3.12)

import warnings
import random
import pandas as pd
import numpy as np
warnings.filterwarnings('ignore')

class MathFunction(object):
    '''
    数学函数
    '''
    MAX = (lambda : result = None(np.maximum.reduce)result_s = pd.Series(result, index = args[0].index)result_s)()
    MIN = (lambda : result = None(np.minimum.reduce)result_s = pd.Series(result, index = args[0].index)result_s)()
    ACOS = (lambda x = None: result = np.arccos(x)result_s = pd.Series(result, index = x.index)result_s)()
    ASIN = (lambda x = None: result = np.arcsin(x)result_s = pd.Series(result, index = x.index)result_s)()
    ATAN = (lambda x = None: result = np.arctan(x)result_s = pd.Series(result, index = x.index)result_s)()
    COS = (lambda x = None: result = np.cos(x)result_s = pd.Series(result, index = x.index)result_s)()
    SIN = (lambda x = None: result = np.sin(x)result_s = pd.Series(result, index = x.index)result_s)()
    TAN = (lambda x = None: result = np.tan(x)result_s = pd.Series(result, index = x.index)result_s)()
    EXP = (lambda x = None: result = np.exp(x)result_s = pd.Series(result, index = x.index)result_s)()
    LN = (lambda x = None: result = np.log(x)result_s = pd.Series(result, index = x.index)result_s)()
    LOG = (lambda x = None: result = np.log10(x)result_s = pd.Series(result, index = x.index)result_s)()
    SQRT = (lambda x = None: result = np.sqrt(x)result_s = pd.Series(result, index = x.index)result_s)()
    ABS = (lambda x = None: result = np.abs(x)result_s = pd.Series(result, index = x.index)result_s)()
    POW = (lambda a = None, b = None: result = np.power(a, b)result_s = pd.Series(result, index = a.index)result_s)()
    CEILING = (lambda x = None: result = np.ceil(x)result_s = pd.Series(result, index = x.index)result_s)()
    FLOOR = (lambda x = None: result = np.floor(x)result_s = pd.Series(result, index = x.index)result_s)()
    INTPART = (lambda x = None: result = np.trunc(x)result_s = pd.Series(result, index = x.index)result_s)()
    BETWEEN = (lambda a = None, b = None, c = staticmethod: s_index = a.indexcase1 = np.logical_and(b <= a, a <= c)case2 = np.logical_and(c <= a, a <= b)result = np.logical_or(case1, case2)pd.Series(result.astype(int), index = s_index))()
    FRACPART = (lambda x = None: (result, _) = np.modf(x)result_s = pd.Series(result, index = x.index)result_s)()
    ROUND = (lambda x = None, n = None: result = np.round(x, n)result_s = pd.Series(result, index = x.index)result_s)()
    SIGN = (lambda x = None: result = np.sign(x)result_s = pd.Series(result, index = x.index)result_s)()
    MOD = (lambda x = None, n = None: result = np.mod(x, n)result_s = pd.Series(result, index = x.index)result_s)()
    IF = (lambda cond = None, a = None, b = staticmethod: cond_arr = np.asarray(cond, dtype = np.bool_)a_arr = np.asarray(a, dtype = np.float64)b_arr = np.asarray(b, dtype = np.float64)result = np.where(cond_arr, a_arr, b_arr)pd.Series(result, index = cond.index))()
    RAND = (lambda a = None, b = None: random.randint(a, b))()

if __name__ == '__main__':
    pass
