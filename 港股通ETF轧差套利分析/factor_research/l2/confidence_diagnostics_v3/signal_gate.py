"""Experimental veto on the ORIGINAL daily first choice. No ranking or probability claim.

Caller must first apply the existing premium screen, strict 14:45 L2 cutoff,
and fixed classifier. This module neither reads current-day outcomes nor
uses ex-post subscription-cap exhaustion to choose a candidate.
"""
from dataclasses import dataclass
from datetime import date
from math import isfinite

@dataclass(frozen=True)
class Evidence:
    trade_date: str
    pcf_previous_date: str
    history_share_date: str
    score: float
    unit_shares: int
    sell_unit_executed_shares: int
    buy_unit_executed_shares: int
    previous_net_creation_shares: int
    all_candidates_complete: bool
    trade_and_order_valid: bool

def evaluate(e: Evidence):
    reasons=[]
    day,pcf,history=map(date.fromisoformat,[e.trade_date,e.pcf_previous_date,e.history_share_date])
    if not (pcf==history and history<day):reasons.append('前日份额日期未与PCF对应，或使用了同日/未来份额')
    if not isfinite(e.score) or not 0<=e.score<=1:reasons.append('模型分数不可用')
    elif e.score<.95:reasons.append('原每日第一名分数低于0.95')
    if not e.all_candidates_complete:reasons.append('候选不完整，无法确认原每日第一名')
    if not e.trade_and_order_valid:reasons.append('逐笔委托或成交质量未通过')
    quantities=[e.unit_shares,e.sell_unit_executed_shares,e.buy_unit_executed_shares,e.previous_net_creation_shares]
    if any(type(v) is not int for v in quantities):raise TypeError('Share quantities must be exact integer shares')
    if e.unit_shares<=0 or min(e.sell_unit_executed_shares,e.buy_unit_executed_shares)<0:raise ValueError('Invalid unit or executed quantity')
    supply=e.sell_unit_executed_shares-e.buy_unit_executed_shares
    previous_positive=max(0,e.previous_net_creation_shares)
    excess=supply-previous_positive
    if excess<=0:reasons.append('整U净供给未超过上日正净申购量')
    return dict(rule_version='inventory_veto_exploratory_v1',research_only=True,pass_evidence_gate=not reasons,
                model_score=e.score,net_unit_supply_U=supply/e.unit_shares,previous_positive_creation_U=previous_positive/e.unit_shares,
                inventory_hypothesis_excess_U=excess/e.unit_shares,reasons=reasons,
                meaning='供给证据筛选，不是实际库存测量、净申购数量预测或成功概率')
