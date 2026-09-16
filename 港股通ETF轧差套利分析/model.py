"""用户假设下的轧差情景模型。不是基金通用规则，也不是实盘交易信号。

S、R 均为其他参与者最终申购/赎回篮子数；x 为本人双向操作篮子数。
M、B、D 为每篮子人民币价值：轧差、实际买入成本、实际卖出净所得。
实际买卖费用应计入 B/D，fee 是本人额外双边费用，避免重复计费。
"""
from dataclasses import asdict, dataclass
from math import isfinite
import json


@dataclass(frozen=True)
class Result:
    total_create: float
    total_redeem: float
    matched: float
    net_create: float
    create_price: float
    redeem_price: float
    gross_per_basket: float
    gross_total: float
    net_total: float
    dilution_weight: float


def round_trip(S, R, x, M, B, D, fee=0.0, fixed_cost=0.0):
    """输入完整篮子的价值；公共现金部分可一致加入三种价值并相互抵消。"""
    values = (S, R, x, M, B, D, fee, fixed_cost)
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool)
               and isfinite(v) for v in values):
        raise ValueError('所有输入必须是有限数值')
    if min(S, R, fee, fixed_cost) < 0 or min(x, M, B, D) <= 0:
        raise ValueError('S/R/费用须非负；x及篮子价值须为正')
    s, r = S + x, R + x
    matched = min(s, r)
    create = (matched * M + max(s-r, 0) * B) / s
    redeem = (matched * M + max(r-s, 0) * D) / r
    spread = redeem - create
    return Result(s, r, matched, S-R, create, redeem, spread,
                  x * spread, x * spread - x * fee - fixed_cost,
                  abs(S-R) / max(s, r))


def expected_profit(scenarios, **prices):
    """联合情景输入，保留流量、价格与汇率之间的相关性。

    每项含 probability/S/R，其余参数可覆盖 prices。概率须加总为1。
    不把 E[S]/E[R] 代入非线性模型冒充期望收益。
    """
    if not scenarios:
        raise ValueError('情景不能为空')
    probabilities = [s['probability'] for s in scenarios]
    if any(not isfinite(p) or p < 0 for p in probabilities):
        raise ValueError('概率必须有限且非负')
    if abs(sum(probabilities) - 1) > 1e-9:
        raise ValueError('概率之和须为1')
    results = []
    for s in scenarios:
        params = {**prices, **{k:v for k,v in s.items() if k != 'probability'}}
        results.append(round_trip(**params))
    return {
        'expected_net_total': sum(p*r.net_total for p,r in zip(probabilities, results)),
        'probability_of_loss': sum(p for p,r in zip(probabilities, results) if r.net_total < 0),
        'scenarios': [dict(probability=p, **asdict(r)) for p,r in zip(probabilities, results)],
    }


if __name__ == '__main__':
    # 演示价格，不是行情。未计费用。
    out = [dict(S=S, R=R, **asdict(round_trip(S, R, 1, 1010000, 1000000, 1000000)))
           for S,R in [(90,10), (900,820), (10,90), (10,10), (10,0), (0,10), (0,0)]]
    print(json.dumps(out, ensure_ascii=False, indent=2))
