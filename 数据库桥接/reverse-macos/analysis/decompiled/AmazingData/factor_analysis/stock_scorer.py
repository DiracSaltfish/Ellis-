# Source Generated with Decompyle++
# File: stock_scorer.pyc (Python 3.12)

__doc__ = '\n个股打分模块\n\n将多因子值乘以因子权重，得到个股综合得分（预期收益）。\n'
import numpy as np
import pandas as pd
from typing import Dict

class StockScorer:
    """
    个股打分器。

    使用示例:
        ss = StockScorer(factor_dict)
        scores = ss.score(weights={'factor_a': 0.6, 'factor_b': 0.4})
        top_stocks = ss.select_top(scores, top_n=50)
    """
    
    def __init__(self = None, factor_data = None):
        '''
        :param factor_data: {factor_name: DataFrame(index=日期, columns=股票代码)}
        '''
        self.factor_data = factor_data

    
    def score(self = None, weights = None):
        '''
        计算个股综合得分。

        :param weights: {factor_name: weight}
        :return: DataFrame, index=日期, columns=股票代码, values=综合得分
        '''
        result = None
    # WARNING: Decompyle incomplete

    
    def select_top(self = None, scores = None, top_n = None, ascending = (50, False)):
        '''
        选取每期得分最高的 N 只股票。

        :param scores: 综合得分 DataFrame
        :param top_n: 选取数量
        :param ascending: False=选高分, True=选低分
        :return: DataFrame, index=日期, columns=排名, values=股票代码
        '''
        result = pd.DataFrame(index = scores.index, columns = range(top_n), dtype = object)
        for date in scores.index:
            row = scores.loc[date].dropna().sort_values(ascending = ascending)
            selected = row.index[:top_n].tolist()
            for j, stock in enumerate(selected):
                result.loc[(date, j)] = stock
        return result

    
    def get_selected_scores(self = None, scores = None, top_n = None):
        '''获取入选股票及其得分'''
        selected = self.select_top(scores, top_n)
        result = pd.DataFrame(index = scores.index, columns = selected.columns, dtype = float)
        for date in scores.index:
            row = scores.loc[date].dropna().sort_values(ascending = False)
            top = row.index[:top_n].tolist()
            for j, stock in enumerate(top):
                result.loc[(date, j)] = row.get(stock, np.nan)
        return result


# WARNING: Decompyle incomplete
