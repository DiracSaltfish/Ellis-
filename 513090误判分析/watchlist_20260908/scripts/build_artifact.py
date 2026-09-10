"""Build the canonical, bounded Data Analytics report artifact."""
from __future__ import annotations

import csv
import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_csv(name: str) -> list[dict[str, str]]:
    with (ROOT / name).open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def number(value: str | None):
    if value in (None, ""):
        return None
    return float(value)


def build_sqlite_evidence(validation: list[dict[str, str]], multi: list[dict[str, str]], intraday: list[dict[str, str]], contributions: list[dict[str, str]], quality: dict) -> None:
    """Materialize the reviewed CSV rows into a local, read-only evidence DB."""
    path = ROOT / "outputs" / "report_data.sqlite"
    if path.exists():
        path.unlink()
    connection = sqlite3.connect(path)
    try:
        def write_table(name: str, rows: list[dict[str, str]]) -> None:
            keys = []
            for row in rows:
                for key in row:
                    if key not in keys:
                        keys.append(key)
            quoted = ", ".join(f'"{key}" TEXT' for key in keys)
            connection.execute(f'DROP TABLE IF EXISTS "{name}"')
            connection.execute(f'CREATE TABLE "{name}" ({quoted})')
            if rows:
                fields = ", ".join(f'"{key}"' for key in keys)
                marks = ", ".join("?" for _ in keys)
                connection.executemany(f'INSERT INTO "{name}" ({fields}) VALUES ({marks})', [[row.get(key, "") for key in keys] for row in rows])

        write_table("validation_watchlist", validation)
        write_table("multi_day_validation", multi)
        write_table("intraday_iopv", intraday)
        write_table("component_contributions", contributions)
        gate_rows = [
            {"metric": "input_count", "value": quality["input_count"]},
            {"metric": "target_nav_covered", "value": quality["target_nav"]["covered"]},
            {"metric": "target_pcf_parsed", "value": quality["target_pcf"]["parsed"]},
            {"metric": "hk_union_count", "value": quality["constituent_market_data"]["hk_union_count"]},
            {"metric": "hk_minute_target_count", "value": quality["constituent_market_data"]["minute_target_count"]},
            {"metric": "status", "value": quality["status"]},
        ]
        write_table("quality_gate", gate_rows)
        connection.commit()
    finally:
        connection.close()


def main() -> None:
    validation = read_csv("outputs/watchlist_validation.csv")
    multi = read_csv("outputs/multi_day_validation.csv")
    intraday = read_csv("outputs/intraday_iopv_20260908.csv")
    contributions = read_csv("outputs/component_contributions_20260908.csv")
    summary = json.loads((ROOT / "outputs/validation_summary.json").read_text())
    quality = json.loads((ROOT / "outputs/quality_checks.json").read_text())
    by_code = {row["code"]: row for row in validation}
    build_sqlite_evidence(validation, multi, intraday, contributions, quality)

    status_order = ["PASS_SINGLE_DAY", "FAIL_5BP", "BOUNDARY_ROUNDING", "NOT_APPLICABLE"]
    status_label = {
        "PASS_SINGLE_DAY": "严格通过",
        "FAIL_5BP": "明确超出 5 bp",
        "BOUNDARY_ROUNDING": "四位净值舍入边界",
        "NOT_APPLICABLE": "不适用",
    }
    single_counts = Counter(row["single_day_status"] for row in validation)
    status_distribution = []
    for status in status_order:
        count = single_counts[status]
        status_distribution.append({
            "status": status_label[status],
            "status_code": status,
            "count": count,
            "share": count / len(validation),
            "scope": "197 只全量目标日 PCF",
        })

    multi_labels = {
        "MULTI_DAY_5D_PASS": "深市 5日通过",
        "MULTI_DAY_5D_NOT_STABLE": "深市 5日不稳",
        "MULTI_DAY_PARTIAL": "深市 历史部分",
        "SH_SINGLE_DAY_ONLY": "沪市 9/8单日",
        "NOT_RUN": "单日未通过",
    }
    multi_distribution = []
    for status, label in multi_labels.items():
        count = summary["multi_day_status_counts"].get(status, 0)
        multi_distribution.append({
            "outcome": label,
            "outcome_code": status,
            "count": count,
            "share": count / len(validation),
            "scope": "197 只；只有单日严格通过标的进入复验",
        })

    top_errors = []
    for row in sorted(validation, key=lambda item: number(item.get("abs_error_bp")) or -1, reverse=True)[:10]:
        top_errors.append({
            "code": row["code"],
            "market": row["market"],
            "fund_name": row["fund_name"],
            "single_day_status": status_label.get(row["single_day_status"], row["single_day_status"]),
            "error_bp": number(row.get("error_bp")),
            "abs_error_bp": number(row.get("abs_error_bp")),
            "published_nav": number(row.get("published_nav")),
            "estimated_nav": number(row.get("estimated_nav")),
            "stale_component_count": len(json.loads(row.get("stale_components") or "[]")),
            "fixed_cash_cny": number(row.get("fixed_cash_cny")) or 0,
        })

    control_rows = []
    for row in intraday:
        if row["fund_code"] != "513090":
            continue
        hh, mm = row["time"][:2], row["time"][2:]
        control_rows.append({
            "timestamp": f"2026-09-08T{hh}:{mm}:00+08:00",
            "time": f"{hh}:{mm}",
            "fund_code": "513090",
            "estimated_nav": number(row["estimated_nav"]),
            "published_nav": number(row["published_nav"]),
            "error_bp_vs_published": (number(row["estimated_nav"]) / number(row["published_nav"]) - 1) * 10000,
            "date": row["date"],
            "minute_component_count": int(row["minute_component_count"]),
            "minute_source_ref": row["minute_source_ref"],
            "minute_source": row["minute_quote_source"],
        })
    control_1500 = next((row for row in control_rows if row["time"] == "15:00"), None)
    control_close = control_rows[-1] if control_rows else None

    evidence_db = "outputs/report_data.sqlite"
    source_records = [
        {"id": "validation_csv", "label": "197 只目标日核验明细", "path": evidence_db, "description": "每只基金一行，证据库由 outputs/watchlist_validation.csv 物化，含 PCF/NAV 路径、成分覆盖、估值、bp 误差、舍入边界和复验状态。", "query": {"engine": "sqlite", "language": "sql", "sql": "SELECT code, market, fund_name, single_day_status, error_bp, abs_error_bp, published_nav, estimated_nav FROM validation_watchlist WHERE pcf_date = '2026-09-08'", "description": "Loads one reviewed target-date row per fund.", "tables_used": ["validation_watchlist"], "filters": ["pcf_date = '2026-09-08'"], "metric_definitions": ["error_bp = (estimated_nav / published_nav - 1) × 10000"]}},
        {"id": "validation_summary", "label": "核验汇总与公式", "path": evidence_db, "description": "日期、固定汇率、严格阈值、公式、汇总计数和分钟证据覆盖，原始汇总见 outputs/validation_summary.json。", "query": {"engine": "sqlite", "language": "sql", "sql": "SELECT metric, value FROM quality_gate ORDER BY metric", "description": "Loads the reviewed validation gate values used for the report definitions.", "tables_used": ["quality_gate"], "metric_definitions": ["Fixed FX midpoint is 0.86482 HKD/CNY; strict threshold is abs(error_bp) < 5."]}},
        {"id": "quality_json", "label": "数据质量门禁", "path": evidence_db, "description": "输入唯一性、目标日覆盖、PCF 解析、成分计数、行情覆盖与控制标的门禁，原始门禁见 outputs/quality_checks.json。", "query": {"engine": "sqlite", "language": "sql", "sql": "SELECT metric, value FROM quality_gate ORDER BY metric", "description": "Loads the reviewed quality-gate results.", "tables_used": ["quality_gate"]}},
        {"id": "multi_day_csv", "label": "历史复验明细", "path": evidence_db, "description": "证据库由 outputs/multi_day_validation.csv 物化；深市含目标日及 9/7、9/4、9/3、9/2，沪市仅保留目标日单日结果。", "query": {"engine": "sqlite", "language": "sql", "sql": "SELECT code, date, status, estimated_nav, published_nav, error_bp, abs_error_bp FROM multi_day_validation ORDER BY code, date DESC", "description": "Loads daily revalidation rows without replacing missing PCFs.", "tables_used": ["multi_day_validation"], "filters": ["date IN ('2026-09-08', '2026-09-07', '2026-09-04', '2026-09-03', '2026-09-02')"]}},
        {"id": "intraday_csv", "label": "港股分钟代理 IOPV", "path": evidence_db, "description": "证据库由 outputs/intraday_iopv_20260908.csv 物化；9/8 港股分钟成分估值的共同时间戳序列，公布单位净值只作参考线。每行以 minute_source_ref 回溯到 outputs/intraday/chart_manifest.json 的具体分钟文件列表。", "query": {"engine": "sqlite", "language": "sql", "sql": "SELECT timestamp, time, fund_code, estimated_nav, published_nav, error_bp_vs_published, minute_component_count, minute_source_ref FROM intraday_iopv WHERE fund_code = '513090' ORDER BY timestamp", "description": "Loads the 513090 minute proxy IOPV sequence and its fund-level source reference.", "tables_used": ["intraday_iopv"], "filters": ["fund_code = '513090'", "date = '2026-09-08'"]}},
        {"id": "contribution_csv", "label": "成分贡献明细", "path": evidence_db, "description": "证据库由 outputs/component_contributions_20260908.csv 物化；含每个 PCF 成分的估值方法、数量、价格日期、金额和源文件。", "query": {"engine": "sqlite", "language": "sql", "sql": "SELECT fund_code, date, component_code, quantity, flag, valuation_method, amount_cny, price_hkd, price_date, source_path FROM component_contributions WHERE date = '2026-09-08' ORDER BY fund_code, component_code", "description": "Loads component-level valuation contributions for the target date.", "tables_used": ["component_contributions"], "filters": ["date = '2026-09-08'"]}},
    ]

    blocks = [
        {"id": "title", "type": "markdown", "body": "# 197只 ETF PCF 独立估值核验（2026-09-08）"},
        {"id": "executive_summary", "type": "markdown", "sourceId": "validation_csv", "body": "## Executive Summary\n\n- **单日全量结论：171/197 只严格通过（86.8%）。** 20 只明确超出 5 bp，5 只落在四位小数公布净值的舍入边界，1 只为不适用。\n- **上海按用户指定降级为单日。** 102 只沪市 PCF 均取上交所 2026-09-08 当日接口；其中 89 只单日通过，不把上海结果包装成历史 5 日结论。\n- **深市通过标的再做历史复验。** 31 只在 9/8、9/7、9/4、9/3、9/2 五个共同交易日均严格通过；34 只五日不稳定，17 只因历史 PCF CDN 返回 403 只能部分复验。\n- **513090 控制标的通过。** 独立估值 1.8432886185282、公布单位净值 1.8433，误差 -0.0617 bp；分时估值使用成分分钟价共同时间戳。"},
        {"id": "headline_metrics", "type": "metric-strip", "cardIds": ["coverage_card", "pass_card", "fail_card", "boundary_card"]},
        {"id": "definitions", "type": "markdown", "sourceId": "validation_summary", "body": "## 口径与判定边界\n\n估值口径是：允许现金替代数量 × 港股不复权收盘价 × HKD/CNY 0.86482，加必选现金替代金额和 PCF 当日估计现金，再除以申赎单位。深市 159900 申赎现金占位行从成分估值中排除，避免与当日估计现金重复计算。\n\n公布单位净值按四位小数处理：只有在整个 ±0.00005 舍入区间内仍满足 `abs(error_bp) < 5` 才记为严格通过；区间与阈值相交的标的单列为 `BOUNDARY_ROUNDING`。T+1 最终现金不用于主估值。"},
        {"id": "status_reading", "type": "markdown", "sourceId": "validation_csv", "body": "## 单日结果：大多数标的与独立收盘估值一致\n\n下图按 197 只全量目标日 PCF 展示状态。严格通过是可直接用于首轮筛选的集合；明确超差和舍入边界需进入复核清单。"},
        {"id": "status_chart", "type": "chart", "chartId": "status_chart", "layout": "full"},
        {"id": "worst_cases", "type": "markdown", "sourceId": "validation_csv", "body": "## 偏差集中在少数标的，最大单日偏差为 -21.30 bp\n\n按绝对误差排序的前 10 只如下。表中保留估值、公布净值、方向和停牌沿用数量，便于直接回到逐成分明细复核。"},
        {"id": "worst_table", "type": "table", "tableId": "worst_table", "layout": "full"},
        {"id": "history_reading", "type": "markdown", "sourceId": "validation_csv", "body": "## 历史复验：深市有 5 日证据，沪市只做 9/8 单日\n\n历史结论只对 9/8 单日严格通过的标的展开。深市的 82 只单日通过标的中，31 只五日均在 5 bp 内，34 只跨日不稳定，17 只因历史 PCF 只部分可得；沪市的 89 只通过标的统一标为“仅 9/8 单日”。"},
        {"id": "history_chart", "type": "chart", "chartId": "history_chart", "layout": "full"},
        {"id": "history_caveat", "type": "markdown", "sourceId": "quality_json", "body": "## 如何解读历史缺口\n\n上海接口只提供目标日 PCF，本报告按要求不追补或推断上海历史 PCF。深市历史复验没有把被 CDN 拦截的 88/380 次请求当作成功，也没有用 T+1 最终现金替代 T 日独立估值；因此“部分可得”是证据边界，而不是通过或失败。"},
        {"id": "intraday_reading", "type": "markdown", "sourceId": "intraday_csv", "body": f"## 分钟证据：513090 的独立代理 IOPV 在收盘附近贴合公布净值\n\n下图使用 513090 的 9/8 港股分钟成分价，在共同时间戳上重算代理 IOPV；灰色虚线是公布单位净值，只作外部参考。15:00 代理值为 {control_1500['estimated_nav']:.10f}，16:08 最后分钟为 {control_close['estimated_nav']:.13f}，与公布单位净值 1.8433 的收盘误差为 -0.0617 bp。它用于检查日内路径和收盘时点，不替代目标日收盘估值。" if control_1500 and control_close else "## 分钟证据：513090 的独立代理 IOPV\n\n分钟证据缺少必要时间点。"},
        {"id": "intraday_chart", "type": "chart", "chartId": "intraday_chart", "layout": "full"},
        {"id": "next_steps", "type": "markdown", "sourceId": "quality_json", "body": "## 风险与下一步\n\n- **优先复核 20 只明确超差标的。** 先看 159699（-21.30 bp）、159303（+13.68 bp）和 513950（-11.55 bp）等偏差较大的行，再下钻 `component_contributions_20260908.csv`。\n- **边界标的不要直接二元化。** 513060、159268、159245、159152、159285 受公布净值四舍五入影响，报告单列为边界；需要更高精度净值或基金侧 IOPV 才能消除不确定性。\n- **继续监控数据缺口。** 3 只港股（00853、02172、02252）9/8 无日线成交，估值明确沿用 8/31 最后收盘价；分钟数据对 646 只成分只取到 455 只目标日序列，170 只基金具备完整共同时间戳证据。\n- **全量审计入口**是随报告交付的 `watchlist_validation.csv`、`multi_day_validation.csv`、`component_contributions_20260908.csv` 与 `quality_checks.json`。"},
    ]

    cards = [
        {"id": "coverage_card", "description": "目标日 NAV、PCF 和代码日期门禁均覆盖的基金数。", "dataset": "headline", "sourceId": "quality_json", "metrics": [{"label": "目标日全量覆盖", "field": "coverage", "format": "number"}]},
        {"id": "pass_card", "description": "在公布净值舍入区间内仍满足严格 abs(error_bp) < 5 的基金数。", "dataset": "headline", "sourceId": "validation_csv", "metrics": [{"label": "单日严格通过", "field": "single_pass", "format": "number"}, {"label": "通过率", "field": "pass_rate", "format": "percent"}]},
        {"id": "fail_card", "description": "整个公布净值舍入区间都无法满足严格 5 bp 阈值的基金数。", "dataset": "headline", "sourceId": "validation_csv", "metrics": [{"label": "明确超出 5 bp", "field": "definite_fail", "format": "number"}]},
        {"id": "boundary_card", "description": "公布净值四位舍入区间与 5 bp 阈值相交，或独立定价规则不适用的基金数。", "dataset": "headline", "sourceId": "validation_csv", "metrics": [{"label": "边界 + 不适用", "field": "boundary_or_na", "format": "number"}]},
    ]

    charts = [
        {"id": "status_chart", "title": "单日核验状态分布", "subtitle": "197 只目标日 PCF；严格通过占 86.8%", "intent": "status", "type": "bar", "dataset": "status_distribution", "sourceId": "validation_csv", "valueFormat": "number", "encodings": {"x": {"field": "status", "type": "nominal", "label": "状态"}, "y": {"field": "count", "type": "quantitative", "label": "基金数"}, "tooltip": [{"field": "share", "type": "quantitative", "label": "占比", "format": "percent"}, {"field": "scope", "type": "text", "label": "范围"}]}, "palette": {"kind": "semantic", "name": "status"}},
        {"id": "history_chart", "title": "通过标的的复验结果", "subtitle": "深市复验五个共同交易日；沪市按要求仅保留 9/8 单日", "intent": "comparison", "type": "bar", "dataset": "multi_distribution", "sourceId": "validation_csv", "valueFormat": "number", "encodings": {"x": {"field": "outcome", "type": "nominal", "label": "复验结果"}, "y": {"field": "count", "type": "quantitative", "label": "基金数"}, "tooltip": [{"field": "share", "type": "quantitative", "label": "占全量比例", "format": "percent"}, {"field": "scope", "type": "text", "label": "范围"}]}, "palette": {"kind": "categorical", "name": "comparison"}},
        {"id": "intraday_chart", "title": "513090 港股分钟代理 IOPV", "subtitle": "9月8日香港时间；蓝线为独立估值，灰色虚线为公布单位净值 1.8433", "intent": "trend", "type": "line", "dataset": "control_intraday", "sourceId": "intraday_csv", "valueFormat": "number", "encodings": {"x": {"field": "timestamp", "type": "temporal", "label": "香港时间"}, "y": {"field": "estimated_nav", "type": "quantitative", "label": "代理 IOPV"}, "tooltip": [{"field": "published_nav", "type": "quantitative", "label": "公布单位净值"}, {"field": "error_bp_vs_published", "type": "quantitative", "label": "误差（bp）", "format": "number"}]}, "referenceLines": [{"value": 1.8433, "axis": "y", "color": "neutral", "lineStyle": "dashed", "label": "公布单位净值"}], "palette": {"kind": "semantic", "name": "actual-vs-baseline"}},
    ]

    tables = [
        {"id": "worst_table", "title": "单日绝对偏差最大的 10 只基金", "subtitle": "按 9 月 8 日独立估值与公布单位净值的绝对误差降序；误差单位为 bp", "dataset": "top_errors", "sourceId": "validation_csv", "defaultSort": {"field": "abs_error_bp", "direction": "desc"}, "columns": [
            {"field": "code", "label": "代码", "type": "text"}, {"field": "market", "label": "市场", "type": "text"}, {"field": "fund_name", "label": "基金", "type": "text"}, {"field": "single_day_status", "label": "状态", "type": "text"}, {"field": "error_bp", "label": "误差（bp）", "format": "number"}, {"field": "abs_error_bp", "label": "绝对误差（bp）", "format": "number"}, {"field": "published_nav", "label": "公布净值", "format": "number"}, {"field": "estimated_nav", "label": "独立估值", "format": "number"}, {"field": "stale_component_count", "label": "沿用旧收盘成分数", "format": "number"}, {"field": "fixed_cash_cny", "label": "必选现金替代（CNY）", "format": "number"}
        ]},
    ]

    artifact = {
        "surface": "report",
        "manifest": {
            "version": 1,
            "surface": "report",
            "title": "197只 ETF PCF 独立估值核验（2026-09-08）",
            "description": "基于目标日 PCF、独立港股收盘价/分钟价和固定 HKD/CNY 中间价的 197 只 ETF 估值核验。",
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "cards": cards,
            "charts": charts,
            "tables": tables,
            "sources": source_records,
            "blocks": blocks,
        },
        "snapshot": {
            "version": 1,
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "status": "ready",
            "datasets": {
                "headline": [{
                    "coverage": quality["target_nav"]["covered"],
                    "single_pass": single_counts["PASS_SINGLE_DAY"],
                    "pass_rate": single_counts["PASS_SINGLE_DAY"] / len(validation),
                    "definite_fail": single_counts["FAIL_5BP"],
                    "boundary_or_na": single_counts["BOUNDARY_ROUNDING"] + single_counts["NOT_APPLICABLE"],
                }],
                "status_distribution": status_distribution,
                "multi_distribution": multi_distribution,
                "top_errors": top_errors,
                "control_intraday": control_rows,
            },
        },
        "sources": source_records,
    }
    (ROOT / "outputs/artifact.json").write_text(json.dumps(artifact, ensure_ascii=False, indent=2))
    print(json.dumps({"path": "outputs/artifact.json", "datasets": {k: len(v) for k, v in artifact["snapshot"]["datasets"].items()}, "blocks": len(blocks), "charts": len(charts), "tables": len(tables)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
