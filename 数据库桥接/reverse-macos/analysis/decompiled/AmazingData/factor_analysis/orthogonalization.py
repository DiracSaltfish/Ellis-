# Source Generated with Decompyle++
# File: orthogonalization.pyc (Python 3.12)

__doc__ = '\n因子正交化模块\n\n三种正交化方法（逐时间截面处理）:\n    1. 对称正交 (Symmetric) — 推荐，正交后与原因子最相似\n    2. 施密特正交 (Gram-Schmidt) — 结果依赖因子顺序\n    3. 规范正交 (Canonical)\n'
import numpy as np
import pandas as pd
from typing import Dict, List, Optional
from AmazingData.factor_analysis.factor_constant import OrthogonalMethod

class FactorOrthogonalization:
    """
    因子正交化。

    使用示例:
        fo = FactorOrthogonalization({'factor_a': df_a, 'factor_b': df_b})
        fo.cal_orthogonalization(method='symmetric')
        result = fo.orthogonalized_data  # Dict[str, DataFrame]
    """
    
    def __init__(self = None, factor_data = None):
        '''
        :param factor_data: {factor_name: DataFrame(index=日期, columns=股票代码)}
        '''
        self.factor_data = factor_data
        self.factor_names = list(factor_data.keys())
        all_dates = factor_data[self.factor_names[0]].index
        for name in self.factor_names[1:]:
            all_dates = all_dates.intersection(factor_data[name].index)
        self.dates = all_dates
        self.orthogonalized_data = { }

    
    def cal_orthogonalization(self = None, method = None):
        """
        执行正交化。

        :param method: 'symmetric', 'gram_schmidt', 'canonical'
        :return: {factor_name: orthogonalized DataFrame}
        """
        for name in self.factor_names:
            self.orthogonalized_data[name] = pd.DataFrame(index = self.dates, columns = self.factor_data[name].columns, dtype = float)
    # WARNING: Decompyle incomplete

    _symmetric_orth = (lambda X = None: n = X.shape[1]M = (n - 1) * np.cov(X)(eigenvalues, eigenvectors) = np.linalg.eigh(M)inv_sqrt_eig = np.diag(np.maximum(eigenvalues, 1e-10) ** -0.5)transition = eigenvectors @ inv_sqrt_eig @ eigenvectors.Ttransition @ X)()
    _gram_schmidt_orth = (lambda X = None: (m, n) = X.shapeQ = np.zeros((m, n))for i in range(m):
q = X[i].copy().astype(float)for j in range(i):
q -= (np.dot(Q[j], X[i]) / np.dot(Q[j], Q[j])) * Q[j]norm = np.linalg.norm(q)if norm > 1e-10:
Q[i] = (q / norm) * np.std(X[i])continueQ[i] = 0Q)()
    _canonical_orth = (lambda X = None: n = X.shape[1]M = (n - 1) * np.cov(X)(eigenvalues, eigenvectors) = np.linalg.eigh(M)inv_sqrt_eig = np.diag(np.maximum(eigenvalues, 1e-10) ** -0.5)transition = eigenvectors @ inv_sqrt_eigtransition @ X)()

# WARNING: Decompyle incomplete
