#!/usr/bin/env python3
"""Plot representative ETF intraday pull-up events using Pillow only."""

from __future__ import annotations

import csv
import io
import json
import math
import sys
import zipfile
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


FONT_PATH = "/System/Library/Fonts/Supplemental/Arial.ttf"


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    path = "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else FONT_PATH
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


def parse_number(value: str | None) -> float:
    value = (value or "").strip()
    return float(value) if value else 0.0


def parse_time(value: str) -> datetime:
    return datetime.strptime(value.strip().replace("/", "-"), "%Y-%m-%d %H:%M")


def minute_value(moment: datetime) -> int:
    return moment.hour * 60 + moment.minute


def read_member_day(bundle: zipfile.ZipFile, symbol: str, date: str) -> list[dict[str, object]]:
    with bundle.open(f"csv/{symbol}.csv") as raw:
        text = io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")
        reader = csv.DictReader(text)
        rows: list[dict[str, object]] = []
        for row in reader:
            stamp = row.get("时间", "").strip().replace("/", "-")
            if not stamp.startswith(date):
                continue
            rows.append({
                "timestamp": parse_time(stamp),
                "open": parse_number(row.get("开盘价")),
                "close": parse_number(row.get("收盘价")),
                "high": parse_number(row.get("最高价")),
                "low": parse_number(row.get("最低价")),
                "volume": parse_number(row.get("成交量")),
            })
    return sorted(rows, key=lambda row: row["timestamp"])


def plot_event(rows: list[dict[str, object]], event: dict[str, str], output_path: Path, model_name: str) -> None:
    width, height = 1800, 1000
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    title_font = font(25, bold=True)
    body_font = font(17)
    small_font = font(14)
    axis_font = font(15)
    left, right = 115, 1735
    price_top, price_bottom = 130, 660
    volume_top, volume_bottom = 745, 890
    plot_width = right - left
    min_minute, max_minute = 570, 900

    times = [row["timestamp"] for row in rows]
    closes = [float(row["close"]) for row in rows]
    highs = [float(row["high"]) for row in rows]
    lows = [float(row["low"]) for row in rows]
    opens = [float(row["open"]) for row in rows]
    volumes = [float(row["volume"]) for row in rows]

    def x_for(moment: datetime) -> float:
        return left + (minute_value(moment) - min_minute) / (max_minute - min_minute) * plot_width

    price_min = min(lows)
    price_max = max(highs)
    pad = max((price_max - price_min) * 0.08, price_max * 0.001)
    price_min -= pad
    price_max += pad

    def y_for(value: float) -> float:
        return price_bottom - (value - price_min) / (price_max - price_min) * (price_bottom - price_top)

    trigger_value = event.get(f"trigger_{model_name}", "").strip()
    trigger_time = parse_time(trigger_value) if trigger_value else None
    offset_value = event.get(f"trigger_offset_{model_name}_bars", "").strip()
    offset_text = f"{float(offset_value):+.0f} bars to event start" if offset_value else "offset n/a"
    rise_value = event.get("peak_return_from_start", event.get("rise_pct", "0"))
    drop_value = event.get("drop_pct", "").strip()
    trough_value = event.get("trough_time", "").strip()
    trough_time = parse_time(trough_value) if trough_value else None
    shape_text = f"rise +{float(rise_value) * 100:.2f}%"
    if drop_value:
        shape_text += f" -> dump -{float(drop_value) * 100:.2f}%"
    title = (
        f"{event['symbol']}  {event['date']}  |  {event['session']} session  |  "
        f"{model_name} trigger {trigger_value[11:] if trigger_value else 'n/a'}  |  {offset_text}  |  {shape_text}"
    )
    draw.text((left, 28), title, fill="#1d2733", font=title_font)
    draw.text(
        (left, 72),
        f"rise {event['start_time'][11:]} -> peak {event['peak_time'][11:]} -> dump {trough_value[11:] if trough_value else 'n/a'}   |   blue = close   |   red/green = up/down minute",
        fill="#68727e",
        font=body_font,
    )
    draw.rectangle((left, price_top, right, price_bottom), outline="#cfd6df", width=1)
    draw.rectangle((left, volume_top, right, volume_bottom), outline="#cfd6df", width=1)

    # Background lunch gap.
    lunch_x1 = x_for(datetime.strptime("12:00", "%H:%M"))
    lunch_x2 = x_for(datetime.strptime("13:00", "%H:%M"))
    draw.rectangle((lunch_x1, price_top, lunch_x2, volume_bottom), fill="#f7f8fa")
    draw.text(((lunch_x1 + lunch_x2) / 2 - 24, price_top + 12), "lunch", fill="#9aa3ad", font=small_font)
    for minute in range(570, 901, 30):
        x = left + (minute - min_minute) / (max_minute - min_minute) * plot_width
        draw.line((x, price_top, x, volume_bottom), fill="#e9edf2", width=1)
        draw.text((x - 25, volume_bottom + 16), f"{minute // 60:02d}:{minute % 60:02d}", fill="#5e6875", font=axis_font)

    for step in range(6):
        value = price_min + (price_max - price_min) * step / 5
        y = y_for(value)
        draw.line((left, y, right, y), fill="#e9edf2", width=1)
        draw.text((12, y - 10), f"{value:.4f}", fill="#5e6875", font=axis_font)
    draw.text((left - 82, price_top - 26), "price", fill="#5e6875", font=axis_font)
    draw.text((left - 92, volume_top - 26), "volume", fill="#5e6875", font=axis_font)

    high_points = [(x_for(t), y_for(v)) for t, v in zip(times, highs)]
    low_points = [(x_for(t), y_for(v)) for t, v in zip(times, lows)]
    close_points = [(x_for(t), y_for(v)) for t, v in zip(times, closes)]
    draw.line(high_points, fill="#8b99a9", width=1)
    draw.line(low_points, fill="#8b99a9", width=1)
    draw.line(close_points, fill="#2367b1", width=3)

    volume_max = max(volumes) if volumes else 1.0
    for t, open_, close, volume in zip(times, opens, closes, volumes):
        x = x_for(t)
        bar_width = max(2, plot_width / 335 * 0.72)
        bar_height = math.sqrt(max(volume, 0) / volume_max) * (volume_bottom - volume_top - 12)
        color = "#d84a4a" if close >= open_ else "#2b9b63"
        draw.rectangle((x - bar_width / 2, volume_bottom - bar_height, x + bar_width / 2, volume_bottom), fill=color)

    start_time = parse_time(event["start_time"])
    peak_time = parse_time(event["peak_time"])
    start_index = next((i for i, value in enumerate(times) if value == start_time), None)
    peak_index = next((i for i, value in enumerate(times) if value == peak_time), None)
    if start_index is not None:
        x = x_for(start_time)
        y = y_for(closes[start_index])
        draw.line((x, price_top, x, price_bottom), fill="#e38d24", width=2)
        draw.ellipse((x - 6, y - 6, x + 6, y + 6), fill="#e38d24")
        draw.text((x + 10, max(price_top + 10, y - 40)), "event start", fill="#b56c14", font=small_font)
    if peak_index is not None:
        x = x_for(peak_time)
        y = y_for(highs[peak_index])
        if start_index is not None:
            draw.rectangle((x_for(start_time), price_top, x, price_bottom), fill="#f4c56a")
            draw.line(high_points, fill="#8b99a9", width=1)
            draw.line(low_points, fill="#8b99a9", width=1)
            draw.line(close_points, fill="#2367b1", width=3)
            draw.line((x_for(start_time), price_top, x_for(start_time), price_bottom), fill="#e38d24", width=2)
        draw.line((x, price_top, x, price_bottom), fill="#a53a8a", width=2)
        draw.ellipse((x - 6, y - 6, x + 6, y + 6), fill="#a53a8a")
        draw.text((x + 10, min(price_bottom - 26, y + 14)), "peak", fill="#8a2d73", font=small_font)
    # Redraw the start marker after the event band so it remains visible.
    if start_index is not None:
        x = x_for(start_time)
        y = y_for(closes[start_index])
        draw.ellipse((x - 6, y - 6, x + 6, y + 6), fill="#e38d24")
        draw.text((x + 10, max(price_top + 10, y - 40)), "event start", fill="#b56c14", font=small_font)
    if trigger_time is not None:
        trigger_index = next((i for i, value in enumerate(times) if value == trigger_time), None)
        if trigger_index is not None:
            x = x_for(trigger_time)
            y = y_for(closes[trigger_index])
            draw.line((x, price_top, x, price_bottom), fill="#0b7a75", width=3)
            draw.ellipse((x - 7, y - 7, x + 7, y + 7), fill="#0b7a75", outline="white", width=2)
            label_y = price_top + 12 if y > price_top + 80 else y + 14
            draw.text((x + 10, label_y), f"MODEL TRIGGER {trigger_time:%H:%M}", fill="#075e5a", font=small_font)
    if trough_time is not None:
        trough_index = next((i for i, value in enumerate(times) if value == trough_time), None)
        if trough_index is not None:
            x = x_for(trough_time)
            y = y_for(lows[trough_index])
            draw.line((x, price_top, x, price_bottom), fill="#b74343", width=2)
            draw.ellipse((x - 7, y - 7, x + 7, y + 7), fill="#b74343", outline="white", width=2)
            label_y = min(price_bottom - 26, y + 14)
            draw.text((x + 10, label_y), f"DUMP LOW {trough_time:%H:%M}", fill="#923131", font=small_font)
    draw.text((left, 930), "Source: filtered ETF 1-minute OHLCV bundle; event labels are offline forward-looking benchmarks.", fill="#7a838e", font=small_font)
    image.save(output_path, format="PNG", optimize=True)


def make_contact_sheet(plotted: list[dict[str, object]], output_path: Path) -> None:
    thumb_width, thumb_height = 450, 250
    columns = 4
    rows = math.ceil(len(plotted) / columns)
    sheet = Image.new("RGB", (columns * thumb_width, rows * thumb_height), "#f4f6f8")
    draw = ImageDraw.Draw(sheet)
    label_font = font(14, bold=True)
    for index, row in enumerate(plotted):
        thumbnail = Image.open(str(row["image"])).convert("RGB")
        thumbnail.thumbnail((thumb_width - 20, thumb_height - 42))
        x = (index % columns) * thumb_width + (thumb_width - thumbnail.width) // 2
        y = (index // columns) * thumb_height + 4
        sheet.paste(thumbnail, (x, y))
        rise_value = row.get("peak_return_from_start", row.get("rise_pct", "0"))
        drop_value = row.get("drop_pct", "")
        suffix = f"+{float(rise_value) * 100:.2f}%"
        if drop_value not in (None, ""):
            suffix += f" / -{float(drop_value) * 100:.2f}%"
        label = f"{int(row['rank']):02d}  {row['symbol']}  {row['date']}  {suffix}"
        draw.text(((index % columns) * thumb_width + 8, (index // columns + 1) * thumb_height - 29), label, fill="#26313d", font=label_font)
    sheet.save(output_path, format="PNG", optimize=True)


def main() -> int:
    if len(sys.argv) not in (5, 6, 7):
        print("usage: plot_intraday_signal_events.py BUNDLE EVENT_TRIGGERS_CSV OUTPUT_DIR MODEL_NAME [TOP_N] [per_symbol|per_day]", file=sys.stderr)
        return 2
    bundle_path = Path(sys.argv[1])
    event_path = Path(sys.argv[2])
    output_dir = Path(sys.argv[3])
    model_name = sys.argv[4]
    top_n = int(sys.argv[5]) if len(sys.argv) >= 6 else 40
    selection_mode = sys.argv[6] if len(sys.argv) == 7 else "per_day"
    if selection_mode not in {"per_symbol", "per_day"}:
        print("selection mode must be per_symbol or per_day", file=sys.stderr)
        return 2
    output_dir.mkdir(parents=True, exist_ok=True)
    image_dir = output_dir / "charts"
    image_dir.mkdir(parents=True, exist_ok=True)

    with event_path.open(encoding="utf-8-sig", newline="") as handle:
        event_rows = [
            row for row in csv.DictReader(handle)
            if row["symbol"] != "513310.SH" and row.get(f"trigger_{model_name}", "").strip()
        ]
    event_rows.sort(key=lambda row: float(row.get("peak_return_from_start", row.get("rise_pct", "0"))), reverse=True)

    selected_by_day: dict[tuple[str, str], dict[str, str]] = {}
    for event in event_rows:
        key = (event["symbol"], "") if selection_mode == "per_symbol" else (event["symbol"], event["date"])
        selected_by_day.setdefault(key, event)
    selected = list(selected_by_day.values())[:top_n]
    forced = next((row for row in event_rows if row["symbol"] == "520890.SH" and row["date"] == "2025-04-18"), None)
    if forced and (forced["symbol"], forced["date"]) not in {(row["symbol"], row["date"]) for row in selected}:
        selected.append(forced)

    plotted: list[dict[str, object]] = []
    with zipfile.ZipFile(bundle_path) as bundle:
        for rank, event in enumerate(selected, start=1):
            rows = read_member_day(bundle, event["symbol"], event["date"])
            if not rows:
                continue
            filename = f"rank_{rank:03d}_{event['symbol'].replace('.', '_')}_{event['date'].replace('-', '')}.png"
            output_path = image_dir / filename
            plot_event(rows, event, output_path, model_name)
            plotted.append({"rank": rank, **event, "image": str(output_path)})

    trigger_field = f"trigger_{model_name}"
    offset_field = f"trigger_offset_{model_name}_bars"
    fields = ["rank", "symbol", "date", "session", "start_time", "peak_time", "trough_time", "peak_return_from_start", "rise_pct", "drop_pct", "tier", trigger_field, offset_field, "image"]
    with (output_dir / "index.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(plotted)
    make_contact_sheet(plotted, output_dir / "contact_sheet.png")
    (output_dir / "selection.json").write_text(json.dumps({
        "source_event_file": str(event_path),
        "selection": "events matched by the selected early-warning model, one strongest episode per selected key, ranked by rise magnitude",
        "selection_mode": selection_mode,
        "model_name": model_name,
        "top_n": top_n,
        "plotted_count": len(plotted),
        "forced_cases": ["520890.SH 2025-04-18"],
        "excluded_symbols": ["513310.SH"],
        "caveat": "The chart is based on OHLCV minute K; it does not show historical IOPV or order-book fields.",
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output_dir": str(output_dir), "plotted_count": len(plotted)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
