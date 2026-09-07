import csv
import hashlib
import json
import math
import os
import re
import shutil
import statistics
from collections import Counter, defaultdict


ROOT = "/Volumes/EllisFiles/Stocksdata/PCF导出CSV"
OUT = "/tmp/pcf_520600_analysis"
FUND_CODE = "520600"


def norm_code(value):
    text = (value or "").strip()
    if re.fullmatch(r"[0-9]+\.0+", text):
        text = text.split(".")[0]
    if text.isdigit():
        text = str(int(text)).zfill(5)
    return text


def number(value):
    text = (value or "").strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def fmt_num(value):
    if value is None:
        return ""
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return format(value, ".10g")


def write_csv(path, header, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


def main():
    shutil.rmtree(OUT, ignore_errors=True)
    os.makedirs(OUT, exist_ok=True)

    main_rows = []
    for filename in os.listdir(ROOT):
        if not filename.endswith("_主表.csv"):
            continue
        path = os.path.join(ROOT, filename)
        with open(path, "r", encoding="utf-8-sig", newline="", errors="replace") as handle:
            for row in csv.DictReader(handle):
                if norm_code(row.get("基金代码")) == FUND_CODE:
                    main_rows.append(
                        {
                            "date": (row.get("公告日期") or "").strip() or filename[:8],
                            "file": filename,
                            "name": (row.get("基金名称") or "").strip(),
                            "main_count": number(row.get("成分股数量只")),
                        }
                    )
    main_rows.sort(key=lambda item: item["date"])
    main_by_date = {item["date"]: item for item in main_rows}

    by_date = defaultdict(list)
    missing_detail_dates = []
    for item in main_rows:
        detail_filename = item["date"].replace("-", "") + "_明细.csv"
        detail_path = os.path.join(ROOT, detail_filename)
        if not os.path.exists(detail_path):
            missing_detail_dates.append(item["date"])
            continue
        with open(detail_path, "r", encoding="utf-8-sig", newline="", errors="replace") as handle:
            for row in csv.DictReader(handle):
                if norm_code(row.get("基金代码")) != FUND_CODE:
                    continue
                by_date[item["date"]].append(
                    {
                        "code": norm_code(row.get("成分股代码")),
                        "name": (row.get("成分股名称") or "").strip(),
                        "qty": number(row.get("数量股")),
                        "flag": (row.get("现金替代标志") or "").strip(),
                        "replace_pct": (row.get("现金替代比例百分比") or "").strip(),
                        "fixed_replace": (row.get("固定替代金额元") or "").strip(),
                    }
                )

    dates = sorted(by_date)
    write_csv(
        os.path.join(OUT, "daily_components.csv"),
        [
            "date",
            "fund_code",
            "fund_name",
            "component_code",
            "component_name",
            "quantity_shares",
            "cash_substitution_flag",
            "cash_substitution_pct",
            "fixed_substitution_amount",
        ],
        [
            [
                date,
                FUND_CODE,
                main_by_date.get(date, {}).get("name", ""),
                item["code"],
                item["name"],
                fmt_num(item["qty"]),
                item["flag"],
                item["replace_pct"],
                item["fixed_replace"],
            ]
            for date in dates
            for item in by_date[date]
        ],
    )

    previous = None
    daily_rows = []
    membership_changes = []
    quantity_changes = []
    all_codes = set()
    code_days = defaultdict(list)
    code_names = defaultdict(set)

    for date in dates:
        records = by_date[date]
        codes = {item["code"] for item in records}
        quantity_map = defaultdict(float)
        names = {}
        flags = Counter()
        null_quantity_rows = 0
        for item in records:
            code = item["code"]
            all_codes.add(code)
            code_days[code].append(date)
            code_names[code].add(item["name"])
            if item["qty"] is None:
                null_quantity_rows += 1
            else:
                quantity_map[code] += item["qty"]
            names[code] = item["name"]
            flags[item["flag"]] += 1
        quantity_map = dict(quantity_map)

        added = []
        removed = []
        quantity_changed = 0
        quantity_changed_nonnull = 0
        absolute_delta = 0.0
        relative_changes = []
        if previous is not None:
            previous_date, previous_codes, previous_quantities, previous_names = previous
            added = sorted(codes - previous_codes)
            removed = sorted(previous_codes - codes)
            for code in codes & previous_codes:
                old_quantity = previous_quantities.get(code)
                new_quantity = quantity_map.get(code)
                if old_quantity != new_quantity:
                    quantity_changed += 1
                    if old_quantity is not None and new_quantity is not None:
                        quantity_changed_nonnull += 1
                        absolute_delta += abs(new_quantity - old_quantity)
                        if old_quantity != 0:
                            relative_changes.append(abs((new_quantity - old_quantity) / old_quantity))
                    quantity_changes.append(
                        [
                            date,
                            previous_date,
                            code,
                            previous_names.get(code, names.get(code, "")),
                            fmt_num(old_quantity),
                            fmt_num(new_quantity),
                            fmt_num(new_quantity - old_quantity if old_quantity is not None and new_quantity is not None else None),
                            format((new_quantity - old_quantity) / old_quantity, ".8%")
                            if old_quantity not in (None, 0) and new_quantity is not None
                            else "",
                        ]
                    )
            if added or removed:
                membership_changes.append(
                    [
                        date,
                        previous_date,
                        len(previous_codes),
                        len(codes),
                        len(added),
                        len(removed),
                        ";".join(added),
                        ";".join(removed),
                        quantity_changed,
                    ]
                )

        daily_rows.append(
            [
                date,
                main_by_date.get(date, {}).get("name", ""),
                fmt_num(main_by_date.get(date, {}).get("main_count")),
                len(records),
                len(codes),
                len(records) - len(codes),
                null_quantity_rows,
                fmt_num(sum(quantity_map.values()) if quantity_map else None),
                len(added),
                len(removed),
                quantity_changed,
                quantity_changed_nonnull,
                fmt_num(absolute_delta),
                format(statistics.median(relative_changes), ".4%") if relative_changes else "",
                format(max(relative_changes), ".4%") if relative_changes else "",
                json.dumps(dict(flags), ensure_ascii=False, sort_keys=True),
            ]
        )
        previous = (date, codes, quantity_map, names)

    write_csv(
        os.path.join(OUT, "daily_summary.csv"),
        [
            "date",
            "fund_name",
            "main_component_count",
            "detail_rows",
            "unique_component_codes",
            "duplicate_rows",
            "null_quantity_rows",
            "total_quantity_shares",
            "added_count_vs_prev",
            "removed_count_vs_prev",
            "quantity_changed_common_count",
            "quantity_changed_nonnull_count",
            "absolute_quantity_delta_common",
            "median_abs_quantity_change_pct",
            "max_abs_quantity_change_pct",
            "cash_substitution_flag_counts",
        ],
        daily_rows,
    )
    write_csv(
        os.path.join(OUT, "membership_changes.csv"),
        [
            "change_date",
            "previous_date",
            "previous_unique_count",
            "current_unique_count",
            "added_count",
            "removed_count",
            "added_codes",
            "removed_codes",
            "quantity_changed_common_count",
        ],
        membership_changes,
    )
    write_csv(
        os.path.join(OUT, "quantity_changes.csv"),
        [
            "date",
            "previous_date",
            "component_code",
            "component_name",
            "previous_quantity",
            "current_quantity",
            "delta_quantity",
            "pct_change_vs_previous",
        ],
        quantity_changes,
    )

    annual_rows = []
    for year in sorted({date[:4] for date in dates}):
        year_dates = [date for date in dates if date.startswith(year)]
        signatures = [tuple(sorted(item["code"] for item in by_date[date])) for date in year_dates]
        stable_quantity_days = sum(
            1 for row in daily_rows if row[0] in year_dates and int(row[10] or 0) == 0
        )
        membership_change_days = sum(1 for row in membership_changes if row[0].startswith(year))
        counts = [len({item["code"] for item in by_date[date]}) for date in year_dates]
        annual_rows.append(
            [
                year,
                len(year_dates),
                year_dates[0],
                year_dates[-1],
                len(set(signatures)),
                membership_change_days,
                stable_quantity_days,
                max(0, len(year_dates) - 1),
                min(counts),
                max(counts),
                counts[0],
                counts[-1],
            ]
        )
    write_csv(
        os.path.join(OUT, "annual_summary.csv"),
        [
            "year",
            "observed_days",
            "first_date",
            "last_date",
            "unique_membership_signatures",
            "membership_change_days_in_year",
            "quantity_stable_days_vs_prev",
            "comparisons_vs_prev",
            "min_unique_component_count",
            "max_unique_component_count",
            "first_day_unique_count",
            "last_day_unique_count",
        ],
        annual_rows,
    )

    lifecycle_rows = []
    quantity_change_by_code = Counter(row[2] for row in quantity_changes)
    for code in sorted(all_codes):
        code_dates = sorted(set(code_days[code]))
        lifecycle_rows.append(
            [
                code,
                ";".join(sorted(code_names[code])),
                code_dates[0],
                code_dates[-1],
                len(code_dates),
                quantity_change_by_code[code],
            ]
        )
    write_csv(
        os.path.join(OUT, "component_lifecycle.csv"),
        [
            "component_code",
            "component_names_seen",
            "first_seen",
            "last_seen",
            "observed_days",
            "quantity_change_records",
        ],
        lifecycle_rows,
    )

    quantity_counts = [int(row[10]) for row in daily_rows[1:]]
    nonnull_quantity_counts = [int(row[11]) for row in daily_rows[1:]]
    summary = {
        "fund_code": FUND_CODE,
        "data_root": ROOT,
        "main_dates": len(main_rows),
        "detail_dates": len(dates),
        "missing_detail_dates": missing_detail_dates,
        "detail_rows": sum(len(by_date[date]) for date in dates),
        "date_min": dates[0] if dates else None,
        "date_max": dates[-1] if dates else None,
        "fund_names_seen": sorted({item["name"] for item in main_rows}),
        "unique_component_codes_overall": len(all_codes),
        "membership_change_days": len(membership_changes),
        "membership_change_dates": [row[0] for row in membership_changes],
        "quantity_change_records": len(quantity_changes),
        "quantity_changed_common_count_min": min(quantity_counts) if quantity_counts else None,
        "quantity_changed_common_count_median": statistics.median(quantity_counts) if quantity_counts else None,
        "quantity_changed_common_count_max": max(quantity_counts) if quantity_counts else None,
        "quantity_changed_common_count_mean": statistics.mean(quantity_counts) if quantity_counts else None,
        "quantity_changed_nonnull_days_zero": sum(value == 0 for value in nonnull_quantity_counts),
        "quantity_comparisons": len(nonnull_quantity_counts),
        "duplicate_rows_total": sum(int(row[5]) for row in daily_rows),
        "null_quantity_rows_total": sum(int(row[6]) for row in daily_rows),
        "all_days_detail_count_matches_main": all(
            str(row[4]) == str(row[2]) for row in daily_rows if row[2]
        ),
        "files": [
            "daily_components.csv",
            "daily_summary.csv",
            "membership_changes.csv",
            "quantity_changes.csv",
            "annual_summary.csv",
            "component_lifecycle.csv",
            "summary.json",
        ],
    }
    with open(os.path.join(OUT, "summary.json"), "w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2)
    with open(os.path.join(OUT, "README.txt"), "w", encoding="utf-8") as handle:
        handle.write("520600 历史PCF分析结果\n")
        handle.write("数据源：" + ROOT + "\n")
        handle.write(
            "分析对象：基金代码 520600，主表与同日明细表；覆盖 "
            + str(summary["date_min"])
            + " 至 "
            + str(summary["date_max"])
            + "，共 "
            + str(summary["detail_dates"])
            + " 个交易日。\n"
        )
        handle.write("成分集合稳定性：以成分股代码集合判断，membership_changes.csv列出相邻交易日集合发生增删的日期。\n")
        handle.write("数量稳定性：以同一成分股的数量股字段逐日比较，quantity_changes.csv列出变化记录；数量变化不等于成分股调仓。\n")
        handle.write("数据质量：daily_summary.csv含重复代码行、空数量、主表/明细行数对照及现金替代标志统计。\n")

    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
