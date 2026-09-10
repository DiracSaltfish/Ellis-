"""Render reproducible minute-level proxy IOPV charts from the saved CSV."""
from __future__ import annotations

import csv
import json
import math
from collections import defaultdict
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "intraday"
PLOTS = OUT / "plots"
BLUE = "#2f6b9a"
NEUTRAL = "#565b65"
GRID = "#e6e8eb"


def read_rows(path: Path):
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def dec(value: str):
    return float(value) if value not in (None, "") else math.nan


def esc(value: object) -> str:
    return escape(str(value))


def display_time(value: str) -> str:
    value = str(value)
    return f"{value[:2]}:{value[2:4]}" if len(value) >= 4 and value[:4].isdigit() else value


def make_svg(code: str, rows: list[dict[str, str]], label: dict[str, str], path: Path, compact: bool = False):
    rows = sorted(rows, key=lambda row: row["time"])
    width, height = (960, 400) if not compact else (520, 280)
    left, right, top, bottom = (72, 70, 58, 54) if not compact else (54, 26, 42, 38)
    plot_width = width - left - right
    plot_height = height - top - bottom
    ys = [dec(row["estimated_nav"]) for row in rows]
    published = dec(label.get("published_nav", ""))
    values = ys + ([] if math.isnan(published) else [published])
    lo, hi = min(values), max(values)
    span = hi - lo
    pad = max(span * 0.12, 0.00025)
    lo, hi = lo - pad, hi + pad

    def sx(i: int) -> float:
        return left + (plot_width * i / max(1, len(rows) - 1))

    def sy(value: float) -> float:
        return top + plot_height * (hi - value) / (hi - lo)

    grid = []
    for j in range(5):
        value = lo + (hi - lo) * j / 4
        y = sy(value)
        grid.append(f'<line x1="{left}" y1="{y:.2f}" x2="{width-right}" y2="{y:.2f}" stroke="{GRID}" stroke-width="1"/>')
        grid.append(f'<text x="{left-9}" y="{y+4:.2f}" text-anchor="end" fill="#626975" font-size="{8 if not compact else 7}">{value:.4f}</text>')
    points = " ".join(f"{sx(i):.2f},{sy(value):.2f}" for i, value in enumerate(ys))
    axes = (
        f'<line x1="{left}" y1="{top}" x2="{left}" y2="{height-bottom}" stroke="#b9bec7" stroke-width="1"/>'
        f'<line x1="{left}" y1="{height-bottom}" x2="{width-right}" y2="{height-bottom}" stroke="#b9bec7" stroke-width="1"/>'
    )
    tick_positions = sorted(set([0, len(rows) // 4, len(rows) // 2, (3 * len(rows)) // 4, len(rows) - 1])) if rows else []
    ticks = []
    for i in tick_positions:
        x = sx(i)
        ticks.append(f'<line x1="{x:.2f}" y1="{height-bottom}" x2="{x:.2f}" y2="{height-bottom+4}" stroke="#b9bec7" stroke-width="1"/>')
        ticks.append(f'<text x="{x:.2f}" y="{height-bottom+17}" text-anchor="middle" fill="#626975" font-size="{8 if not compact else 7}">{esc(display_time(rows[i]["time"]))}</text>')
    title = f"{code}  {label.get('fund_name', '')}".strip()
    subtitle = "腾讯分钟价 · 共同时间戳交集 · FX 0.86482" if compact else "腾讯港股分钟价；共同时间戳交集；固定汇率 0.86482"
    if not math.isnan(published):
        subtitle += f"；公布净值 {published:.4f}"
    reference = ""
    if not math.isnan(published):
        py = sy(published)
        reference = f'<line x1="{left}" y1="{py:.2f}" x2="{width-right}" y2="{py:.2f}" stroke="{NEUTRAL}" stroke-width="1.2" stroke-dasharray="5 4"/>'
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">
<title id="title">{esc(title)}</title>
<desc id="desc">{esc(subtitle)}</desc>
<rect width="100%" height="100%" fill="white"/>
<text x="{left}" y="23" fill="#20242a" font-size="{13 if not compact else 10}" font-weight="700">{esc(title)}</text>
<text x="{left}" y="39" fill="#626975" font-size="{8.5 if not compact else 6.8}">{esc(subtitle)}</text>
{''.join(grid)}
{axes}
{''.join(ticks)}
{reference}
<polyline points="{points}" fill="none" stroke="{BLUE}" stroke-width="{1.7 if not compact else 1.15}" stroke-linejoin="round" stroke-linecap="round"/>
<text x="{width/2:.2f}" y="{height-7}" text-anchor="middle" fill="#4e535d" font-size="{9 if not compact else 7.5}">香港时间</text>
<text x="15" y="{(top+height-bottom)/2:.2f}" transform="rotate(-90 15 {(top+height-bottom)/2:.2f})" text-anchor="middle" fill="#4e535d" font-size="{9 if not compact else 7.5}">单位净值 / IOPV</text>
</svg>'''
    path.write_text(svg, encoding="utf-8")


def main() -> None:
    data = read_rows(ROOT / "outputs" / "intraday_iopv_20260908.csv")
    validation = {row["code"]: row for row in read_rows(ROOT / "outputs" / "watchlist_validation.csv")}
    summary = json.loads((ROOT / "outputs" / "validation_summary.json").read_text(encoding="utf-8"))
    fund_source_paths = summary.get("intraday", {}).get("fund_source_paths", {})
    grouped = defaultdict(list)
    for row in data:
        grouped[row["fund_code"]].append(row)
    PLOTS.mkdir(parents=True, exist_ok=True)
    chart_manifest = []
    for code, rows in grouped.items():
        output_path = PLOTS / f"{code}.svg"
        make_svg(code, rows, validation.get(code, {}), output_path)
        chart_manifest.append({
            "fund_code": code,
            "date": rows[0]["date"] if rows else "2026-09-08",
            "row_count": len(rows),
            "svg_path": str(output_path.relative_to(ROOT)),
            "data_path": "outputs/intraday_iopv_20260908.csv",
            "minute_source_paths": fund_source_paths.get(code, []),
            "published_nav": validation.get(code, {}).get("published_nav", ""),
            "single_day_status": validation.get(code, {}).get("single_day_status", ""),
        })
    (OUT / "chart_manifest.json").write_text(json.dumps({"overview": "outputs/intraday/overview.svg", "fund_charts": chart_manifest}, ensure_ascii=False, indent=2), encoding="utf-8")

    # A compact overview keeps the control fund, boundary cases, and large-error cases visible.
    candidates = ["513090", "513060", "159268", "159303", "513950", "526030"]
    candidates = [code for code in candidates if code in grouped]
    overview = OUT / "overview.svg"
    width, height = 1200, 830
    cells = []
    for index, code in enumerate(candidates):
        rows = sorted(grouped[code], key=lambda row: row["time"])
        label = validation.get(code, {})
        # Render each compact panel in a translated group using the same chart contract.
        panel_path = OUT / f".panel_{code}.svg"
        make_svg(code, rows, label, panel_path, compact=True)
        panel = panel_path.read_text(encoding="utf-8")
        panel = panel[panel.find(">") + 1 : panel.rfind("</svg>")]
        panel_path.unlink()
        x = 35 + (index % 2) * 580
        y = 82 + (index // 2) * 220
        cells.append(f'<g transform="translate({x},{y})">{panel}</g>')
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="overview-title overview-desc">
<title id="overview-title">9月8日港股分钟代理 IOPV：控制、边界与偏差案例</title>
<desc id="overview-desc">蓝线为独立分钟价格估值，灰色虚线为公布单位净值。</desc>
<rect width="100%" height="100%" fill="white"/>
<text x="35" y="32" fill="#20242a" font-size="18" font-weight="700">9月8日港股分钟代理 IOPV：控制、边界与偏差案例</text>
<text x="35" y="55" fill="#626975" font-size="10">蓝线=独立分钟价格估值；灰色虚线=公布单位净值。每个小图使用该基金成分的共同时间戳。</text>
{''.join(cells)}
</svg>'''
    overview.write_text(svg, encoding="utf-8")
    print({"fund_charts": len(grouped), "overview": str(overview)})


if __name__ == "__main__":
    main()
