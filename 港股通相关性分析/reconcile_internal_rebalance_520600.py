"""Historical, budget-neutral target proxy; NOT actual orders or current holdings.

Run with Python's standard library. Source reports are read independently of
earlier comparison CSVs. PCF quantities are explicitly dated and repriced with
report fair value per share. Preserve non-PCF holdings and net non-stock assets.
"""
import csv
import hashlib
import json
import re
import statistics
from decimal import Decimal as D
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BASE = ROOT.parent / 'analysis_520600'
OUT = ROOT / 'outputs' / 'internal_rebalance_520600_20260630'
SOURCES = {
    'report': BASE / 'periodic_reports/text/AN202608301828743070.txt',
    'pcf': BASE / 'daily_components.csv',
    'prices': BASE / 'pcf_close_nav_20260630_detail.csv',
}


def rows(path):
    with path.open(encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


def number(s):
    return D(s.replace(',', ''))


def run():
    OUT.mkdir(parents=True, exist_ok=True)
    report = SOURCES['report'].read_text()
    assert '广发中证港股通汽车产业主题' in report
    block = report[report.rfind('7.3 期末按公允价值'):]
    block = block[:block.index('7.4 报告期内')]
    pattern = re.compile(r'^\s*\d+\s+(\d{5})\s+(?:(.*?)\s+)?([\d,]+)\s+([\d,]+\.\d{2})\s+([\d.]+)\s*$', re.M)
    holdings = {}
    for m in pattern.finditer(block):
        assert m[1] not in holdings
        holdings[m[1]] = {'name': m[2] or '', 'qty': number(m[3]), 'value': number(m[4])}
    stock = D('311735467.01')
    nav = D('324339872.63')
    shares, unit = D('326368000'), D('500000')
    baskets = shares / unit
    assert len(holdings) == 51
    assert sum(h['value'] for h in holdings.values()) == stock
    pcf = {}
    for r in rows(SOURCES['pcf']):
        if r['date'] not in ('2026-06-30', '2026-07-01'):
            continue
        assert r['fund_code'] == '520600'
        code = r['component_code'].zfill(5)
        date = r['date']
        assert code not in pcf.setdefault(date, {})
        pcf[date][code] = r
    current, prior = pcf['2026-07-01'], pcf['2026-06-30']
    common = set(current) & set(holdings)
    assert len(current) == len(common) == 50
    assert set(holdings) - common == {'02362'}
    assert set(prior) == common
    prices = {c: holdings[c]['value'] / holdings[c]['qty'] for c in common}
    budget = sum(holdings[c]['value'] for c in common)
    basket_cost = sum(D(current[c]['quantity_shares']) * prices[c] for c in common)
    prior_cost = sum(D(prior[c]['quantity_shares']) * prices[c] for c in common)
    scale = budget / basket_cost
    scale_prior = budget / prior_cost
    output = []
    for c in sorted(common):
        h = holdings[c]
        q = D(current[c]['quantity_shares'])
        target = scale * q
        delta = target - h['qty']
        delta_prior = scale_prior * D(prior[c]['quantity_shares']) - h['qty']
        raw_delta = baskets * q - h['qty']
        output.append({
            'report_date': '2026-06-30', 'pcf_date': '2026-07-01',
            'code': c, 'name': h['name'] or current[c]['component_name'],
            'actual_fund_shares': h['qty'], 'actual_per_500000_units': h['qty'] / baskets,
            'pcf_quantity': q, 'report_price_rmb': prices[c],
            'budget_neutral_target_shares': target,
            'budget_neutral_delta_shares': delta,
            'budget_neutral_delta_per_500000_units': delta / baskets,
            'budget_neutral_trade_value_rmb': delta * prices[c],
            'model_direction': '增持候选' if delta > 0 else '减持候选',
            'raw_pcf_delta_shares_not_order': raw_delta,
            'raw_pcf_delta_value_rmb': raw_delta * prices[c],
            'prior_pcf_budget_neutral_delta_shares': delta_prior,
            'direction_same_for_both_pcf_dates': (delta > 0) == (delta_prior > 0),
            'status': '历史目标代理，非真实订单；未扣未成交委托，未做整手约束',
        })
    assert abs(sum(r['budget_neutral_trade_value_rmb'] for r in output)) < D('0.00001')
    buys = sorted((r for r in output if r['budget_neutral_delta_shares'] > 0), key=lambda r: -r['budget_neutral_trade_value_rmb'])
    sells = sorted((r for r in output if r['budget_neutral_delta_shares'] < 0), key=lambda r: r['budget_neutral_trade_value_rmb'])
    for filename, data in [('all_50_components.csv', output), ('buy_candidates.csv', buys), ('sell_candidates.csv', sells)]:
        with (OUT / filename).open('w', encoding='utf-8-sig', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=list(output[0]))
            writer.writeheader()
            writer.writerows(data)
    old = {r['component_code']: D(r['pcf_quantity']) for r in rows(BASE / 'latest_halfyear_single_basket_compare.csv') if r['pcf_quantity']}
    fixed = sum(D(current[c]['fixed_substitution_amount']) for c in common)
    price_rows = {r['code']: r for r in rows(SOURCES['prices'])}
    fx = [prices[c] / D(price_rows[c]['close_20260630_hkd']) for c in common]
    fixed_residuals = {c: D(current[c]['quantity_shares']) * prices[c] - D(current[c]['fixed_substitution_amount']) for c in common}
    summary = {
        'report_date': '2026-06-30', 'pcf_date': '2026-07-01',
        'purpose': '保留实际股票预算和受限股，估计PCF相对权重代理下的历史调仓；非基金真实订单',
        'fund_shares': shares, 'standard_unit': unit, 'equivalent_baskets': baskets,
        'net_assets': nav, 'stock_assets': stock, 'net_non_stock_assets': nav-stock,
        'frozen_02362_shares': holdings['02362']['qty'],
        'frozen_02362_value': holdings['02362']['value'],
        'common_stock_budget': budget, 'basket_at_report_prices': basket_cost,
        'target_scale_units': scale, 'scale_as_fraction_of_full_pcf': scale / baskets,
        'buy_count': len(buys), 'sell_count': len(sells),
        'gross_buy_rmb': sum(r['budget_neutral_trade_value_rmb'] for r in buys),
        'gross_sell_rmb': -sum(r['budget_neutral_trade_value_rmb'] for r in sells),
        'net_trade_rmb': sum(r['budget_neutral_trade_value_rmb'] for r in output),
        'raw_full_pcf_net_buy_rmb': baskets*basket_cost-budget,
        'raw_full_pcf_funding_excess_over_nonstock_net_assets': baskets*basket_cost-budget-(nav-stock),
        'old_comparison_wrong_date_quantity_count': sum(old[c] != D(current[c]['quantity_shares']) for c in common),
        'direction_changed_between_pcf_dates': [r['code'] for r in output if not r['direction_same_for_both_pcf_dates']],
        'report_implied_fx_median': statistics.median(fx),
        'pcf_fixed_total': fixed, 'fixed_value_residual_total': basket_cost-fixed,
        'fixed_value_residual_02333': fixed_residuals['02333'],
        'fixed_value_residual_other_49': sum(v for c,v in fixed_residuals.items() if c!='02333'),
        'sha256': {k: hashlib.sha256(p.read_bytes()).hexdigest() for k,p in SOURCES.items()},
    }
    (OUT / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str)+'\n')
    md = [
        '# 520600 基金内部补券估算：2026-06-30 历史快照', '',
        '更新：2026-09-08。以下是条件化估计，不是当前持仓或基金真实买单。', '',
        '## 已确认与未确认', '',
        '实际持仓来自半年报完整51只股票明细；目标比例以2026-07-01 PCF的50只数量作为代理。PCF为下一交易日操作清单，其数量可能包含预先调整，不能视为管理人内部目标；另用6月30日PCF做方向敏感性检查。', '',
        '保留02362金川国际1,676,000股：半年报6.4.12.2明确列为停牌受限股票。保留净非股票资产12,604,405.62元；非股票净资产并不等于可用现金。', '',
        '## 同一股票预算下的算法', '',
        '```text',
        'p_i = 半年报公允价值_i / 半年报股数_i（人民币/股）',
        'B = 共同50只股票的实际市值 = 310,803,825.54元',
        'T = Σ(PCF数量_i × p_i)',
        'k = B / T',
        '目标股数_i = k × PCF数量_i',
        '调仓股数_i = 目标股数_i − 实际持仓股数_i',
        '正值为增持候选；负值为减持候选；Σ调仓金额=0（费用前）',
        '```', '',
        f'本次k={scale:,.8f}，相当于总份额折算篮子数的{scale/baskets:.6%}。', '',
        f'增持候选{len(buys)}只、减持候选{len(sells)}只；买入和卖出金额各约{summary["gross_buy_rmb"]:,.2f}元，交易费用另计。', '',
        f'若直接补到652.736倍PCF数量，需要净增持{summary["raw_full_pcf_net_buy_rmb"]:,.2f}元；超过非股票净资产{summary["raw_full_pcf_funding_excess_over_nonstock_net_assets"]:,.2f}元，且未留费用/流动性准备。该情景不能直接当执行计划。', '',
        '## 增持候选完整表', '',
        '股数均为数学目标，未按港股整手取整；金额为报告日估值，不是当前成交预算。', '',
        '|代码|名称|实际基金股数|目标基金股数|拟增股数|每50万份拟增股数|金额（元）|',
        '|---|---|---:|---:|---:|---:|---:|',
    ]
    for r in buys:
        md.append(f'|{r["code"]}|{r["name"]}|{r["actual_fund_shares"]:,.0f}|{r["budget_neutral_target_shares"]:,.2f}|{r["budget_neutral_delta_shares"]:,.2f}|{r["budget_neutral_delta_per_500000_units"]:.4f}|{r["budget_neutral_trade_value_rmb"]:,.2f}|')
    md += ['', '## 减持候选完整表', '', '|代码|名称|拟减股数|回收金额（元）|', '|---|---|---:|---:|']
    for r in sells:
        md.append(f'|{r["code"]}|{r["name"]}|{-r["budget_neutral_delta_shares"]:,.2f}|{-r["budget_neutral_trade_value_rmb"]:,.2f}|')
    md += ['', '## 数据审计及更正', '',
        f'- 旧单篮比较CSV有{summary["old_comparison_wrong_date_quantity_count"]}只股票仍使用6月30日数量，而先前文字称7月1日；本表从每日PCF重新读取。',
        '- 先前由份额变化、调仓日期直接推断“基金没跟上调仓”过于确定。公开数据只能确认时点差异，无法识别逐笔成交或因果。',
        f'- 半年报逐股市值与收盘价反推的汇率中位数为{statistics.median(fx):.10f}；PCF替代金额与同价篮子差{basket_cost-fixed:.6f}元，其中长城汽车{fixed_residuals["02333"]:.6f}元、其余49只合计{summary["fixed_value_residual_other_49"]:.6f}元。先前将全额差异归于汇率不成立；长城汽车项需用当日公司行为和PCF原始规则核实，不能靠拟合汇率掩盖。',
        f'- 使用6月30日或7月1日PCF时，增减持方向改变的股票：{summary["direction_changed_between_pcf_dates"]}。',
        '- 单篮数量取整和港股交易整手是两项约束。不能把所有偏差都认定为整手原因；小差额需按整手、费用和误差阈值判断是否交易。',
        '', '## 如何变成真实可执行补券数据', '',
        '1. 取得当日管理人/托管人对账后的全量持仓和份额；持仓必须含已成交未交收交易的经济权益，避免重复加减。',
        '2. 取得指数未取整权重或基金内部目标权重、目标股票预算、受限股处理方案；PCF只在缺少目标时作为有误差的代理。',
        '3. 取得同一时间戳的逐股行情、估值汇率、公司行为、分红税费、现金、应收应付和费用计提。停牌资产使用获批公允估值。',
        '4. 更新买入/卖出成交、申赎引起的资产变动和未完成委托；仅从已含成交的持仓中再扣未成交买单、加未成交卖单。',
        '5. 按可用现金（非净非股票资产）、整手、可买资格、价格冲击和跟踪误差做交易约束；输出计划买入、计划卖出、待交收、已在途、禁买/禁卖清单。',
        '', '实际净值公式：NAV(t)=股票及其他投资公允价值+现金及应收−负债；每最小单位净值=NAV(t)×最小单位份额/F(t)。', '',
        '新订单净需求=目标股数−已含成交的经济持仓−未成交买单剩余股数+未成交卖单剩余股数。替代款专属委托与普通再平衡委托需按交易归属核算，不能重复补券。', '',
        '只有历史半年报、季度前十持仓和每日PCF时，无法唯一推出今天真实持仓或内部应买清单。按期初持仓加成交及公司行为滚动核算，才能逐日准确估值；单一NAV残差也无法唯一反解几十只股票的数量。', '',
        '## 来源与复现', '',
        f'- [半年报文本]({SOURCES["report"]})：股票明细7.3、受限证券6.4.12.2、资产负债表6.1。',
        f'- [逐日PCF]({SOURCES["pcf"]})，本次选择2026-07-01及2026-06-30。',
        '- [广发基金产品页](https://www.gffunds.com.cn/funds/?fundcode=520600)。',
        '- [招募说明书（2026-03更新）](https://www.sse.com.cn/disclosure/fund/announcement/c/new/2026-03-25/520600_20260325_2UZU.pdf)。',
        f'- [复现脚本]({Path(__file__).resolve()})；输出summary.json含来源SHA256与核算结果。', '',
    ]
    (OUT / '基金内部补券估算说明.md').write_text('\n'.join(md))
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    print('BUY', [(r['code'],r['name'],round(r['budget_neutral_delta_shares'],2),round(r['budget_neutral_trade_value_rmb'],2)) for r in buys])
    print('SELL TOP', [(r['code'],r['name'],round(r['budget_neutral_delta_shares'],2),round(r['budget_neutral_trade_value_rmb'],2)) for r in sells[:8]])


if __name__ == '__main__':
    run()
