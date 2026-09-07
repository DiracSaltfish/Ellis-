from __future__ import annotations

import csv
import json
import time
import urllib.request
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path


BASE = Path('/Users/ellis/工具程序开发/analysis_520600')
PCF_FILE = BASE / 'daily_components.csv'
OUT_DETAIL = BASE / 'pcf_close_nav_20260630_detail.csv'
OUT_SUMMARY = BASE / 'pcf_close_nav_20260630_summary.json'
DAY = '2026-06-30'
PREV_DAY = '2026-06-29'
PCF_DAY = '2026-06-30'
NEXT_PCF_DAY = '2026-07-01'

D = Decimal


def q2(value: Decimal) -> Decimal:
    return value.quantize(D('0.01'), rounding=ROUND_HALF_UP)


def get_close(code: str) -> dict[str, D]:
    url = (
        'https://push2his.eastmoney.com/api/qt/stock/kline/get'
        f'?secid=116.{code}&fields1=f1&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61'
        f'&klt=101&fqt=0&beg={PREV_DAY.replace("-", "")}&end=20260701'
    )
    last = None
    for attempt in range(5):
        try:
            req = urllib.request.Request(
                url,
                headers={
                    'User-Agent': 'Mozilla/5.0',
                    'Referer': 'https://quote.eastmoney.com/',
                    'Accept': '*/*',
                },
            )
            with urllib.request.urlopen(req, timeout=20) as response:
                body = json.load(response)
            if body.get('rc') != 0 or not body.get('data'):
                raise RuntimeError(f'no data for {code}: {body.get("rc")}')
            result = {}
            for line in body['data'].get('klines', []):
                fields = line.split(',')
                result[fields[0]] = D(fields[2])  # date, open, close, high, ...
            if PREV_DAY not in result or DAY not in result:
                raise RuntimeError(f'missing required dates for {code}: {sorted(result)}')
            return {'close_20260629_hkd': result[PREV_DAY], 'close_20260630_hkd': result[DAY]}
        except Exception as exc:  # network source is occasionally transient
            last = exc
            time.sleep(0.4 * (attempt + 1))
    raise RuntimeError(str(last))


def load_pcf(date: str) -> dict[str, dict[str, str]]:
    rows = {}
    with PCF_FILE.open(encoding='utf-8-sig', newline='') as handle:
        for row in csv.DictReader(handle):
            if row['date'] == date:
                rows[row['component_code']] = row
    return rows


def main() -> None:
    pcf_630 = load_pcf(PCF_DAY)
    pcf_701 = load_pcf(NEXT_PCF_DAY)
    codes = sorted(set(pcf_630) | set(pcf_701))
    cached_prices = {}
    if OUT_DETAIL.exists():
        with OUT_DETAIL.open(encoding='utf-8-sig', newline='') as handle:
            for row in csv.DictReader(handle):
                if row.get('close_20260629_hkd') and row.get('close_20260630_hkd'):
                    cached_prices[row['code']] = {
                        'close_20260629_hkd': D(row['close_20260629_hkd']),
                        'close_20260630_hkd': D(row['close_20260630_hkd']),
                    }
    if all(code in cached_prices for code in codes):
        prices = {code: cached_prices[code] for code in codes}
    else:
        prices = {}
        for code in codes:
            try:
                prices[code] = get_close(code)
            except RuntimeError:
                if code not in cached_prices:
                    raise
                prices[code] = cached_prices[code]

    # The fixed substitution amount in the PCF is the RMB value of the
    # component at the PCF valuation date, rounded to cents.  Aggregate ratio
    # is more stable than per-stock ratios because of cent rounding.
    fixed_630 = sum(D(pcf_630[c]['fixed_substitution_amount']) for c in pcf_630)
    fixed_701 = sum(D(pcf_701[c]['fixed_substitution_amount']) for c in pcf_701)
    hkd_630_prev = sum(
        D(pcf_630[c]['quantity_shares']) * prices[c]['close_20260629_hkd'] for c in pcf_630
    )
    hkd_701_day = sum(
        D(pcf_701[c]['quantity_shares']) * prices[c]['close_20260630_hkd'] for c in pcf_701
    )
    fx_630 = fixed_630 / hkd_630_prev
    fx_701 = fixed_701 / hkd_701_day
    # Independent cross-check: 2026-06-30 PBOC/CFETS HKD/CNY central parity.
    # The fund's valuation FX can differ from this reference rate.
    fx_reference = D('0.86855')

    rows = []
    for code in codes:
        r630 = pcf_630.get(code)
        r701 = pcf_701.get(code)
        rows.append({
            'code': code,
            'name_630': (r630 or {}).get('component_name', ''),
            'name_701': (r701 or {}).get('component_name', ''),
            'close_20260629_hkd': str(prices[code]['close_20260629_hkd']),
            'close_20260630_hkd': str(prices[code]['close_20260630_hkd']),
            'pcf_630_qty': (r630 or {}).get('quantity_shares', ''),
            'pcf_701_qty': (r701 or {}).get('quantity_shares', ''),
            'pcf_630_fixed_rmb': (r630 or {}).get('fixed_substitution_amount', ''),
            'pcf_701_fixed_rmb': (r701 or {}).get('fixed_substitution_amount', ''),
            'value_630_prev_rmb': str(
                D(r630['quantity_shares']) * prices[code]['close_20260629_hkd'] * fx_630
            ) if r630 else '',
            'value_701_close_rmb': str(
                D(r701['quantity_shares']) * prices[code]['close_20260630_hkd'] * fx_701
            ) if r701 else '',
        })

    headers = list(rows[0])
    with OUT_DETAIL.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)

    pre_cash_630 = D('113.09')
    pre_cash_701 = D('35.69')
    summary = {
        'data_date': DAY,
        'price_source': 'Eastmoney push2his historical daily K-line; close price in HKD',
        'pcf_20260630': {
            'net_value_date': PREV_DAY,
            'disclosed_min_unit_asset_nav_rmb': '493417.39',
            'sum_fixed_component_value_rmb': str(q2(fixed_630)),
            'estimated_cash_part_rmb': str(pre_cash_630),
            'reconstructed_nav_rmb': str(q2(fixed_630 + pre_cash_630)),
            'effective_hkd_cny': str(fx_630),
            'hkd_component_value_at_20260629_close': str(hkd_630_prev),
        },
        'pcf_20260701_for_20260630_nav': {
            'net_value_date': DAY,
            'disclosed_min_unit_asset_nav_rmb': '496892.88',
            'sum_fixed_component_value_rmb': str(q2(fixed_701)),
            'estimated_cash_part_rmb': str(pre_cash_701),
            'reconstructed_nav_rmb': str(q2(fixed_701 + pre_cash_701)),
            'effective_hkd_cny': str(fx_701),
            'hkd_component_value_at_20260630_close': str(hkd_701_day),
        },
        'close_anchored_20260630': {
            'pcf_date_used': NEXT_PCF_DAY,
            'stock_basket_rmb_using_pcf_implied_fx': str(q2(fixed_701)),
            'stock_basket_rmb_using_cfets_reference_fx': str(q2(hkd_701_day * fx_reference)),
            'estimated_cash_part_rmb': str(pre_cash_701),
            'min_unit_asset_nav_rmb_using_pcf_implied_fx': str(q2(fixed_701 + pre_cash_701)),
            'min_unit_asset_nav_rmb_using_cfets_reference_fx': str(q2(hkd_701_day * fx_reference + pre_cash_701)),
            'cfets_reference_fx': str(fx_reference),
            'cfets_vs_pcf_implied_difference_rmb': str(q2(hkd_701_day * fx_reference + pre_cash_701 - (fixed_701 + pre_cash_701))),
            'cfets_vs_pcf_implied_difference_pct': str(q2((hkd_701_day * fx_reference + pre_cash_701) / (fixed_701 + pre_cash_701) * D('100') - D('100'))),
            'nav_per_fund_share_rmb_using_pcf_implied_fx': str(q2((fixed_701 + pre_cash_701) / D('500000'))),
            'nav_per_fund_share_rmb_using_cfets_reference_fx': str(q2((hkd_701_day * fx_reference + pre_cash_701) / D('500000'))),
        },
        'actual_midyear_20260630': {
            'shares': '326368000',
            'equivalent_units': '652.736',
            'stock_fair_value_rmb': '311735467.01',
            'actual_only_02362_fair_value_rmb': '931641.47',
            'common_pcf_component_fair_value_rmb': '310803825.54',
            'net_assets_rmb': '324339872.63',
            'common_pcf_component_value_per_equivalent_unit_rmb': str(q2(D('310803825.54') / D('652.736'))),
            'actual_only_02362_value_per_equivalent_unit_rmb': str(q2(D('931641.47') / D('652.736'))),
            'stock_value_per_equivalent_unit_rmb': str(q2(D('311735467.01') / D('652.736'))),
            'net_assets_per_equivalent_unit_rmb': str(q2(D('324339872.63') / D('652.736'))),
            'cash_and_receivables_less_liabilities_rmb': str(
                D('324339872.63') - D('311735467.01')
            ),
            'cash_and_receivables_less_liabilities_per_equivalent_unit_rmb': str(
                q2((D('324339872.63') - D('311735467.01')) / D('652.736'))
            ),
        },
    }
    OUT_SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
