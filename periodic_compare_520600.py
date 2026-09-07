import csv
import json
import os
import re
import statistics
from collections import defaultdict


BASE = "/Users/ellis/工具程序开发/analysis_520600"
TEXT_DIR = os.path.join(BASE, "periodic_reports", "text")
OUT_DIR = BASE

REPORTS = [
    ("2025-06-30", "AN202508291736534705.txt", "6.1", "6.2", "7.3", "7.4"),
    ("2025-12-31", "AN202603291820839757.txt", "7.1", "7.2", "8.3", "8.4"),
    ("2026-06-30", "AN202608301828743070.txt", "6.1", "6.2", "7.3", "7.4"),
]

QUARTERLY_REPORTS = [
    ("2025-03-31", "AN202504201659509272.txt"),
    ("2025-06-30", "AN202507171710673737.txt"),
    ("2025-09-30", "AN202510271770092190.txt"),
    ("2025-12-31", "AN202601211818215252.txt"),
    ("2026-03-31", "AN202604211821390060.txt"),
    ("2026-06-30", "AN202607191827113018.txt"),
]

# Match each report date to the next available PCF whose net value date is
# the report date. Holiday gaps are material here (for example, 2025-12-31
# maps to the 2026-01-05 PCF, not the 2025-12-31 file).
PCF_DATE_BY_REPORT_DATE = {
    "2025-03-31": "2025-04-01",
    "2025-06-30": "2025-07-01",
    "2025-09-30": "2025-10-09",
    "2025-12-31": "2026-01-05",
    "2026-03-31": "2026-04-01",
    "2026-06-30": "2026-07-01",
}


def norm_code(value):
    text = (value or "").strip()
    if text.isdigit():
        return str(int(text)).zfill(5)
    return text


def clean_num(value):
    text = (value or "").replace(",", "").strip()
    if not text or text in {"-", "--"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_two_numbers(line):
    values = re.findall(r"[\d,]+(?:\.\d+)?", line)
    return [clean_num(value) for value in values[-2:]] if len(values) >= 2 else []


def section(text, start_heading, next_heading):
    matches = list(re.finditer(r"(?m)^" + re.escape(start_heading) + r".*$", text))
    if not matches:
        raise ValueError("heading not found: " + start_heading)
    start = matches[-1].start()
    tail = text[start:]
    next_match = re.search(r"(?m)^" + re.escape(next_heading) + r".*$", tail)
    return tail[: next_match.start() if next_match else len(tail)]


def parse_balance_sheet(text, balance_heading, balance_next_heading):
    balance = section(text, balance_heading + " 资产负债表", balance_next_heading + " 利润表")
    stock_line = next(line for line in balance.splitlines() if line.strip().startswith("其中：股票投资"))
    net_line = next(line for line in balance.splitlines() if line.strip().startswith("净资产合计"))
    stock_values = parse_two_numbers(stock_line)
    net_values = parse_two_numbers(net_line)
    return {
        "stock_value": stock_values[0],
        "stock_value_prior": stock_values[1],
        "net_assets": net_values[0],
        "net_assets_prior": net_values[1],
    }


def parse_holdings(text, heading, next_heading):
    body = section(text, heading, next_heading)
    rows = []
    pattern = re.compile(
        r"^\s*\d+\s+(\d{5})\s+(?:(.*?)\s+)?([\d,]+)\s+([\d,]+\.\d{2})\s+([\d.]+)\s*$"
    )
    for line in body.splitlines():
        match = pattern.match(line)
        if not match:
            continue
        rows.append(
            {
                "code": norm_code(match.group(1)),
            "name": (match.group(2) or "").strip(),
                "actual_quantity": clean_num(match.group(3)),
                "actual_value": clean_num(match.group(4)),
                "actual_pct": clean_num(match.group(5)),
            }
        )
    return rows


def main():
    pcf = defaultdict(dict)
    with open(os.path.join(BASE, "daily_components.csv"), encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            pcf[row["date"]][norm_code(row["component_code"])] = {
                "name": row["component_name"],
                "pcf_quantity": clean_num(row["quantity_shares"]),
            }

    comparison_rows = []
    holding_rows = []
    for report_date, filename, balance_heading, balance_next_heading, holding_heading, next_heading in REPORTS:
        text = open(os.path.join(TEXT_DIR, filename), encoding="utf-8").read()
        balance = parse_balance_sheet(text, balance_heading, balance_next_heading)
        holdings = parse_holdings(text, holding_heading, next_heading)
        actual_map = {row["code"]: row for row in holdings}
        pcf_map = pcf[PCF_DATE_BY_REPORT_DATE.get(report_date, report_date)]
        for row in holdings:
            if not row["name"]:
                row["name"] = pcf_map.get(row["code"], {}).get("name", "")
        actual_codes = set(actual_map)
        pcf_codes = set(pcf_map)
        overlap = actual_codes & pcf_codes
        actual_only = actual_codes - pcf_codes
        pcf_only = pcf_codes - actual_codes

        ratios = [
            actual_map[code]["actual_quantity"] / pcf_map[code]["pcf_quantity"]
            for code in overlap
            if actual_map[code]["actual_quantity"] and pcf_map[code]["pcf_quantity"]
        ]
        actual_total_value = sum(row["actual_value"] for row in holdings)
        proxy_values = {
            code: pcf_map[code]["pcf_quantity"] * actual_map[code]["actual_value"] / actual_map[code]["actual_quantity"]
            for code in overlap
            if actual_map[code]["actual_quantity"] and pcf_map[code]["pcf_quantity"]
        }
        proxy_total_value = sum(proxy_values.values())
        actual_overlap_value = sum(actual_map[code]["actual_value"] for code in overlap)
        abs_weight_diffs = []
        for code in sorted(overlap):
            actual_weight = actual_map[code]["actual_value"] / actual_total_value
            proxy_weight = proxy_values[code] / proxy_total_value
            abs_weight_diffs.append(abs(proxy_weight - actual_weight))
            holding_rows.append(
                [
                    report_date,
                    code,
                    actual_map[code]["name"],
                    pcf_map[code]["pcf_quantity"],
                    actual_map[code]["actual_quantity"],
                    actual_map[code]["actual_quantity"] / pcf_map[code]["pcf_quantity"],
                    actual_map[code]["actual_value"],
                    actual_map[code]["actual_pct"],
                    actual_weight,
                    proxy_weight,
                    proxy_weight - actual_weight,
                ]
            )

        comparison_rows.append(
            [
                report_date,
                filename.replace(".txt", ".pdf"),
                len(holdings),
                len(pcf_codes),
                len(overlap),
                len(actual_only),
                ";".join(sorted(actual_only)),
                len(pcf_only),
                ";".join(sorted(pcf_only)),
                balance["stock_value"],
                balance["net_assets"],
                actual_total_value,
                actual_overlap_value / actual_total_value,
                statistics.median(ratios),
                min(ratios),
                max(ratios),
                statistics.median(abs_weight_diffs),
                max(abs_weight_diffs),
            ]
        )

    with open(os.path.join(OUT_DIR, "periodic_comparison.csv"), "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "report_date",
                "report_pdf",
                "actual_holdings_count",
                "pcf_component_count",
                "overlap_count",
                "actual_only_count",
                "actual_only_codes",
                "pcf_only_count",
                "pcf_only_codes",
                "balance_sheet_stock_investment_value",
                "balance_sheet_net_assets",
                "parsed_holdings_total_fair_value",
                "actual_value_coverage_by_overlap",
                "actual_qty_to_pcf_qty_median",
                "actual_qty_to_pcf_qty_min",
                "actual_qty_to_pcf_qty_max",
                "median_abs_weight_difference",
                "max_abs_weight_difference",
            ]
        )
        writer.writerows(comparison_rows)

    with open(os.path.join(OUT_DIR, "periodic_holding_comparison.csv"), "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "report_date",
                "component_code",
                "component_name",
                "pcf_quantity_per_creation_unit",
                "actual_report_quantity",
                "actual_qty_to_pcf_qty",
                "actual_fair_value",
                "actual_report_pct_of_net_assets",
                "actual_weight_of_reported_stocks",
                "pcf_weight_proxy_using_report_prices",
                "pcf_minus_actual_weight",
            ]
        )
        writer.writerows(holding_rows)

    quarterly_rows = []
    quarterly_summary = []
    for report_date, filename in QUARTERLY_REPORTS:
        text = open(os.path.join(TEXT_DIR, filename), encoding="utf-8").read()
        holdings = parse_holdings(
            text,
            "5.3 报告期末按公允价值占基金资产净值比例大小排序的前十名股票投资明细",
            "5.4",
        )
        pcf_map = pcf[PCF_DATE_BY_REPORT_DATE.get(report_date, report_date)]
        for row in holdings:
            if not row["name"]:
                row["name"] = pcf_map.get(row["code"], {}).get("name", "")
        overlap = [row for row in holdings if row["code"] in pcf_map]
        ratios = [
            row["actual_quantity"] / pcf_map[row["code"]]["pcf_quantity"]
            for row in overlap
            if row["actual_quantity"] and pcf_map[row["code"]]["pcf_quantity"]
        ]
        quarterly_summary.append(
            [
                report_date,
                filename.replace(".txt", ".pdf"),
                len(holdings),
                len(overlap),
                ";".join(row["code"] for row in holdings if row["code"] not in pcf_map),
                statistics.median(ratios) if ratios else "",
                min(ratios) if ratios else "",
                max(ratios) if ratios else "",
                (max(ratios) - min(ratios)) / statistics.median(ratios) if ratios else "",
            ]
        )
        for row in holdings:
            pcf_row = pcf_map.get(row["code"])
            quarterly_rows.append(
                [
                    report_date,
                    row["code"],
                    row["name"],
                    row["actual_quantity"],
                    row["actual_value"],
                    row["actual_pct"],
                    pcf_row["pcf_quantity"] if pcf_row else "",
                    row["actual_quantity"] / pcf_row["pcf_quantity"]
                    if pcf_row and pcf_row["pcf_quantity"]
                    else "",
                ]
            )

    with open(os.path.join(OUT_DIR, "quarterly_top10_summary.csv"), "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "report_date",
                "report_pdf",
                "reported_top10_count",
                "pcf_overlap_count",
                "top10_actual_only_codes",
                "actual_qty_to_pcf_qty_median",
                "actual_qty_to_pcf_qty_min",
                "actual_qty_to_pcf_qty_max",
                "ratio_range_as_pct_of_median",
            ]
        )
        writer.writerows(quarterly_summary)

    with open(os.path.join(OUT_DIR, "quarterly_top10_quantity_compare.csv"), "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "report_date",
                "component_code",
                "component_name",
                "actual_report_quantity",
                "actual_fair_value",
                "actual_report_pct_of_net_assets",
                "pcf_quantity_per_creation_unit",
                "actual_qty_to_pcf_qty",
            ]
        )
        writer.writerows(quarterly_rows)

    print(
        json.dumps(
            {
                "comparisons": comparison_rows,
                "holding_rows": len(holding_rows),
                "quarterly_summary": quarterly_summary,
                "quarterly_rows": len(quarterly_rows),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
