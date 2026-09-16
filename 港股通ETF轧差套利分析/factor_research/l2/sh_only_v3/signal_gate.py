"""Experimental Shanghai-only veto of the frozen daily first choice.

No primary-market creation total is inferred from L2. A passed gate is not
a calibrated probability or an instruction to place a trade.
"""
from math import isfinite
def evaluate(symbol,score,unit_shares,sell_unit_executed_shares,buy_unit_executed_shares,*,candidate_complete,order_trade_valid):
    reasons=[]
    if len(symbol)!=9 or not symbol.startswith('5') or not symbol.endswith('.SH') or not symbol[:6].isdigit():reasons.append('不在沪市预测范围')
    if symbol=='513130.SH':reasons.append('沿用513130质量隔离')
    if not isfinite(score) or not 0<=score<=1:reasons.append('模型分数无效')
    elif score<.95:reasons.append('模型分数低于0.95')
    if not candidate_complete:reasons.append('沪市候选数据不完整')
    if not order_trade_valid:reasons.append('L2委托或成交质量未通过')
    if any(type(x) is not int for x in [unit_shares,sell_unit_executed_shares,buy_unit_executed_shares]):raise TypeError('Quantities must be integer shares')
    if unit_shares<=0 or min(sell_unit_executed_shares,buy_unit_executed_shares)<0:raise ValueError('Invalid unit or quantity')
    net=sell_unit_executed_shares-buy_unit_executed_shares
    if net<=0:reasons.append('整U净卖出执行量不为正')
    return dict(research_only=True,rule_version='sh_score95_positive_unit_supply_v1',trigger=not reasons,net_unit_supply_U=net/unit_shares,reasons=reasons)
