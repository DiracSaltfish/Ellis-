# Source Generated with Decompyle++
# File: brinson_attribution.pyc (Python 3.12)

__doc__ = '\nBrinson 归因分析模块\n\n将组合超额收益分解为资产配置效应、个股选择效应和交互效应。\n\n支持三种方法:\n    - BHB (Brinson-Hood-Beebower): 传统法，三项独立分解\n    - BF (Brinson-Fachler): 交互项归并选择效应（行业常用默认方法）\n    - GEOMETRIC (几何法): 使用对数收益分解，避免多期累加偏差\n\n核心公式:\n    - 配置效应: Σ (w_pi - w_bi) × r_bi       行业超配/低配带来的收益\n    - 选择效应: Σ w_bi × (r_pi - r_bi)        行业内选股带来的收益\n    - 交互效应: Σ (w_pi - w_bi) × (r_pi - r_bi)  配置与选择的交叉影响\n'
import numpy as np
import pandas as pd
from typing import Optional, Dict, List
from AmazingData.performance_attribution.attribution_constant import BrinsonMethod

class BrinsonAttribution:
    '''
    Brinson 绩效归因分析器。

    使用示例:
        br = BrinsonAttribution(
            portfolio_weight, benchmark_weight,
            portfolio_return, benchmark_return,
            industry_map, method=BrinsonMethod.BF
        )
        br.run()
        print(br.allocation_effect)   # 配置效应
        print(br.selection_effect)    # 选择效应
        print(br.interaction_effect)  # 交互效应
    '''
    
    def __init__(self, portfolio_weight, benchmark_weight = None, portfolio_return = None, benchmark_return = None, industry_map = (BrinsonMethod.BF,), method = ('portfolio_weight', pd.DataFrame, 'benchmark_weight', pd.DataFrame, 'portfolio_return', pd.DataFrame, 'benchmark_return', pd.DataFrame, 'industry_map', pd.Series, 'method', BrinsonMethod)):
        '''
        :param portfolio_weight: 组合持仓权重, index=日期, columns=股票代码
        :param benchmark_weight: 基准持仓权重, index=日期, columns=股票代码
        :param portfolio_return: 组合个股收益率, index=日期, columns=股票代码
        :param benchmark_return: 基准个股收益率, index=日期, columns=股票代码
        :param industry_map: 行业映射, index=股票代码, values=行业名称
        :param method: Brinson 归因方法, 默认 BF
        '''
        self.portfolio_weight = portfolio_weight
        self.benchmark_weight = benchmark_weight
        self.portfolio_return = portfolio_return
        self.benchmark_return = benchmark_return
        self.industry_map = industry_map
        self.method = method
        common_dates = portfolio_weight.index.intersection(benchmark_weight.index)
        common_dates = common_dates.intersection(portfolio_return.index)
        common_dates = common_dates.intersection(benchmark_return.index)
        common_stocks = portfolio_weight.columns.intersection(benchmark_weight.columns)
        common_stocks = common_stocks.intersection(portfolio_return.columns)
        common_stocks = common_stocks.intersection(benchmark_return.columns)
        self.portfolio_weight = portfolio_weight.loc[(common_dates, common_stocks)].fillna(0)
        self.benchmark_weight = benchmark_weight.loc[(common_dates, common_stocks)].fillna(0)
        self.portfolio_return = portfolio_return.loc[(common_dates, common_stocks)]
        self.benchmark_return = benchmark_return.loc[(common_dates, common_stocks)]
        self.industry_map = industry_map.reindex(common_stocks).fillna('其他')
        self._industries = sorted(self.industry_map.unique())
        self.allocation_effect = None
        self.selection_effect = None
        self.interaction_effect = None
        self.attribution_result = None

    
    def run(self = None):
        '''
        执行 Brinson 归因。

        :return: 归因结果 DataFrame, index=日期
        '''
        n_dates = len(self.portfolio_weight)
        allocation_rows = []
        selection_rows = []
        interaction_rows = []
        for idx in range(n_dates):
            date = self.portfolio_weight.index[idx]
            pw = self.portfolio_weight.iloc[idx]
            bw = self.benchmark_weight.iloc[idx]
            pr = self.portfolio_return.iloc[idx]
            br = self.benchmark_return.iloc[idx]
            (alloc, sel, inter) = self._single_period_attribution(pw, bw, pr, br)
            allocation_rows.append(alloc)
            selection_rows.append(sel)
            interaction_rows.append(inter)
        self.allocation_effect = pd.DataFrame(allocation_rows, index = self.portfolio_weight.index)
        self.selection_effect = pd.DataFrame(selection_rows, index = self.portfolio_weight.index)
        self.interaction_effect = pd.DataFrame(interaction_rows, index = self.portfolio_weight.index)
        if self.method == BrinsonMethod.BHB:
            total_allocation = self.allocation_effect.sum(axis = 1)
            total_selection = self.selection_effect.sum(axis = 1)
            total_interaction = self.interaction_effect.sum(axis = 1)
        elif self.method == BrinsonMethod.BF:
            total_allocation = self.allocation_effect.sum(axis = 1)
            total_selection = self.selection_effect.sum(axis = 1) + self.interaction_effect.sum(axis = 1)
            total_interaction = pd.Series(0, index = self.allocation_effect.index)
        else:
            total_allocation = self.allocation_effect.sum(axis = 1)
            total_selection = self.selection_effect.sum(axis = 1)
            total_interaction = self.interaction_effect.sum(axis = 1)
        port_ret = (self.portfolio_weight * self.portfolio_return).sum(axis = 1)
        bench_ret = (self.benchmark_weight * self.benchmark_return).sum(axis = 1)
        self.attribution_result = pd.DataFrame({
            'portfolio_return': port_ret,
            'benchmark_return': bench_ret,
            'excess_return': port_ret - bench_ret,
            'allocation_effect': total_allocation,
            'selection_effect': total_selection,
            'interaction_effect': total_interaction,
            'total_attribution': total_allocation + total_selection + total_interaction })
        return self.attribution_result

    
    def _single_period_attribution(self, pw = None, bw = None, pr = None, br = ('pw', pd.Series, 'bw', pd.Series, 'pr', pd.Series, 'br', pd.Series)):
        '''
        单期 Brinson 归因计算。

        行业级别聚合:
            w_pi = Σ pw[stock∈ind_i],  w_bi = Σ bw[stock∈ind_i]
            r_pi = Σ (pw[stock]/w_pi) × pr[stock],  r_bi = Σ (bw[stock]/w_bi) × br[stock]

        配置效应: (w_pi - w_bi) × (r_bi - R_b)  或  (w_pi - w_bi) × r_bi
        选择效应: w_bi × (r_pi - r_bi)
        交互效应: (w_pi - w_bi) × (r_pi - r_bi)
        '''
        alloc = { }
        sel = { }
        inter = { }
        for ind in self._industries:
            mask = (self.industry_map == ind).values
            stocks_in_ind = self.industry_map[self.industry_map == ind].index
            common = stocks_in_ind.intersection(pw.index)
            if len(common) == 0:
                alloc[ind] = 0
                sel[ind] = 0
                inter[ind] = 0
                continue
            pw_ind = pw[common].sum()
            bw_ind = bw[common].sum()
            r_b_total = (bw * br).sum()
            if self.method == BrinsonMethod.BHB:
                alloc[ind] = (pw_ind - bw_ind) * rb_ind
                sel[ind] = bw_ind * (rp_ind - rb_ind)
                inter[ind] = (pw_ind - bw_ind) * (rp_ind - rb_ind)
                continue
            if self.method == BrinsonMethod.BF:
                alloc[ind] = (pw_ind - bw_ind) * (rb_ind - r_b_total)
                sel[ind] = bw_ind * (rp_ind - rb_ind)
                inter[ind] = (pw_ind - bw_ind) * (rp_ind - rb_ind)
                continue
            alloc[ind] = (pw_ind - bw_ind) * (np.log(1 + rb_ind) - np.log(1 + r_b_total))
            sel[ind] = bw_ind * (np.log(1 + rp_ind) - np.log(1 + rb_ind))
            inter[ind] = (pw_ind - bw_ind) * (np.log(1 + rp_ind) - np.log(1 + rb_ind))
        return (alloc, sel, inter)

    
    def cumulative_attribution(self = None):
        '''
        多期累计 Brinson 归因。

        使用对数收益法: 每期收益贡献 = ln(1+r)，累加后转回复合收益。

        :return: 累计归因 DataFrame
        '''
        pass
    # WARNING: Decompyle incomplete

    
    def industry_detail(self = None):
        '''
        返回各行业的归因明细。

        :return: 各行业配置效应/选择效应/交互效应汇总 DataFrame
        '''
        pass
    # WARNING: Decompyle incomplete

    
    def save(self = None, path = None):
        '''保存 Brinson 归因结果到 HDF5 文件'''
        pass
    # WARNING: Decompyle incomplete


# WARNING: Decompyle incomplete
