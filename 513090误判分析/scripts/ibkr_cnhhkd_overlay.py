"""Rebuild the 2026-09-08 SH513090 intraday IOPV with IBKR CNH.HKD 1-minute MIDPOINT bars.

This script only uses IBKR's historical-data API.  It contains no order, account, or
market-data subscription request.  CNH.HKD is quoted as HKD per CNH, so its reciprocal
is used as the CNY per HKD conversion factor required by the PCF valuation.
"""

import asyncio
import csv
from datetime import timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager, ticker
import numpy as np

# ib_insync/eventkit still expects an event loop to exist at import time on Python 3.14.
asyncio.set_event_loop(asyncio.new_event_loop())
from ib_insync import Forex, IB  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "minute_iopv_20260908.csv"
OUTPUT = ROOT / "minute_iopv_20260908_cnhhkd.csv"
PORT = 7496
TZ = ZoneInfo("Asia/Shanghai")
PCF_UNITS = 500_000


def minute_label(bar_date):
    """Return the Shanghai minute label from IB's UTC historical-bar time."""
    if bar_date.tzinfo is None:
        bar_date = bar_date.replace(tzinfo=timezone.utc)
    return bar_date.astimezone(TZ).strftime("%H:%M")


def fetch_cnh_hkd_bars():
    """Fetch the day containing 2026-09-08 as read-only 1-minute MIDPOINT bars."""
    ib = IB()
    try:
        ib.connect("127.0.0.1", PORT, clientId=513090, timeout=15, readonly=True)
        contract = Forex("CNHHKD")
        qualified = ib.qualifyContracts(contract)
        if not qualified:
            raise RuntimeError("TWS could not qualify the CNH.HKD forex contract")
        contract = qualified[0]
        bars = ib.reqHistoricalData(
            contract,
            endDateTime="20260908 16:10:00 Asia/Shanghai",
            durationStr="1 D",
            barSizeSetting="1 min",
            whatToShow="MIDPOINT",
            useRTH=False,
            formatDate=2,
            keepUpToDate=False,
        )
        result = {}
        for bar in bars:
            local = minute_label(bar.date)
            # Retain only the requested local trading day.  A timestamp is preserved
            # exactly; this program deliberately does not fill missing FX minutes.
            local_day = (bar.date if bar.date.tzinfo else bar.date.replace(tzinfo=timezone.utc)).astimezone(TZ).date().isoformat()
            if local_day == "2026-09-08" and float(bar.close) > 0:
                result[local] = float(bar.close)
        if not result:
            raise RuntimeError("TWS returned no CNH.HKD bars for 2026-09-08")
        return result, contract
    finally:
        ib.disconnect()


def xpos(value):
    hour, minute = map(int, value.split(":"))
    raw = hour * 60 + minute - 570
    return raw - (60 if hour >= 13 else 0)


def read_rows(cnh_hkd):
    rows = list(csv.DictReader(SOURCE.open(encoding="utf-8-sig")))
    for row in rows:
        quote = cnh_hkd.get(row["time"])
        row["CNH_HKD_midpoint"] = "" if quote is None else f"{quote:.8f}"
        row["CNY_per_HKD_from_CNH_HKD"] = "" if quote is None else f"{1 / quote:.8f}"
        if quote is None:
            row["IOPV_CNHHKD"] = ""
            row["ETF_premium_CNHHKD"] = ""
        else:
            value = (float(row["stock_HKD"]) / quote + float(row["estimated_cash_CNY"])) / PCF_UNITS
            row["IOPV_CNHHKD"] = f"{value:.10f}"
            etf = float(row["ETF_price"]) if row["ETF_price"] else None
            row["ETF_premium_CNHHKD"] = "" if etf is None else f"{etf / value - 1:.10f}"
    return rows


def save_rows(rows):
    columns = list(rows[0])
    with OUTPUT.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def plot(rows, contract):
    font = "/System/Library/Fonts/Supplemental/Arial Unicode.ttf"
    font_manager.fontManager.addfont(font)
    plt.rcParams.update({
        "font.family": font_manager.FontProperties(fname=font).get_name(),
        "axes.unicode_minus": False, "font.size": 12, "axes.spines.top": False,
        "axes.spines.right": False, "axes.edgecolor": "#BCC6D3", "axes.labelcolor": "#46546A",
        "xtick.color": "#46546A", "ytick.color": "#46546A", "savefig.facecolor": "white",
    })
    x = np.array([xpos(row["time"]) for row in rows])
    parity = np.array([float(row["IOPV_exact"]) for row in rows])
    ibkr = np.array([float(row["IOPV_CNHHKD"]) if row["IOPV_CNHHKD"] else np.nan for row in rows])
    market = np.array([float(row["ETF_price"]) if row["ETF_price"] else np.nan for row in rows])
    parity_prem = np.array([float(row["ETF_premium"]) * 100 if row["ETF_premium"] else np.nan for row in rows])
    ibkr_prem = np.array([float(row["ETF_premium_CNHHKD"]) * 100 if row["ETF_premium_CNHHKD"] else np.nan for row in rows])
    blue, gold, teal, ink, grey = "#2563EB", "#BD8700", "#0F766E", "#17283F", "#64748B"
    fig, (ax, ap) = plt.subplots(2, 1, figsize=(16, 10), sharex=True, gridspec_kw={"height_ratios": [3.2, 1.15], "hspace": .14})
    fig.subplots_adjust(left=.075, right=.955, top=.80, bottom=.22)
    fig.text(.075, .945, "SH513090  |  香港证券ETF", fontsize=25, color=ink, weight="bold")
    fig.text(.075, .902, "2026年9月8日：实际分时价格 × PCF重算 IOPV", fontsize=18, color=ink)
    fig.text(.075, .861, "17只当日PCF成分券 · 预估现金9,547.19元 · 每篮50万份 · IBKR TWS: CNH.HKD 1分钟MIDPOINT", fontsize=12, color=grey)
    for axis in (ax, ap):
        axis.set_facecolor("white"); axis.grid(axis="y", color="#E6EBF1", linewidth=.8); axis.set_axisbelow(True)
        axis.axvspan(120, 150, color="#F1F5F9", zorder=0); axis.axvspan(270, 338, color="#FAF7EE", zorder=0)
        axis.axvline(270, color="#94A3B8", linestyle="--", linewidth=1)
    ax.plot(x, parity, color=gold, lw=2.1, label="中间价 0.86482 重算 IOPV", zorder=4)
    ax.plot(x, ibkr, color=teal, lw=2.2, label="IBKR CNH.HKD 重算 IOPV", zorder=5)
    ax.plot(x, market, color=blue, lw=2, label="ETF实际价格", zorder=6)
    ax.set_ylim(1.820, 1.902); ax.set_ylabel("人民币元 / 份", labelpad=12); ax.yaxis.set_major_formatter(ticker.FormatStrFormatter("%.3f"))
    ax.legend(loc="upper right", frameon=False, ncol=3, bbox_to_anchor=(1, 1.10), fontsize=11)
    ax.text(135, 1.898, "A股午休", ha="center", fontsize=10, color=grey)
    ax.text(304, 1.898, "A股收盘后\n仅港股篮子继续估值", ha="center", va="top", fontsize=10, color=grey)
    idx = next(index for index, row in enumerate(rows) if row["time"] == "15:00")
    ax.scatter([270, 270], [ibkr[idx], market[idx]], c=[teal, blue], s=32, zorder=7)
    ax.annotate(f"15:00  IBKR估值 {ibkr[idx]:.4f}", xy=(270, ibkr[idx]), xytext=(216, 1.876), fontsize=11, color=teal, arrowprops={"arrowstyle": "-", "color": teal, "lw": 1}, bbox={"facecolor": "white", "edgecolor": "none", "pad": 3})
    ax.annotate(f"15:00  ETF {market[idx]:.4f}", xy=(270, market[idx]), xytext=(215, 1.838), fontsize=11, color=blue, arrowprops={"arrowstyle": "-", "color": blue, "lw": 1}, bbox={"facecolor": "white", "edgecolor": "none", "pad": 3})
    ap.axhline(0, color=grey, linewidth=1)
    ap.plot(x, parity_prem, color=gold, lw=1.2, alpha=.8, label="相对中间价")
    ap.plot(x, ibkr_prem, color=teal, lw=1.6, label="相对IBKR CNH.HKD")
    ap.fill_between(x, ibkr_prem, 0, where=np.isfinite(ibkr_prem), color=teal, alpha=.10)
    ap.set_ylim(-1.30, .10); ap.set_ylabel("ETF折溢价率", labelpad=12); ap.yaxis.set_major_formatter(ticker.FuncFormatter(lambda val, _: f"{val:.1f}%"))
    ap.legend(loc="lower left", frameon=False, ncol=2, fontsize=10)
    ap.scatter([270], [ibkr_prem[idx]], s=22, color=teal, zorder=5)
    ap.annotate(f"{ibkr_prem[idx]:.4f}%", xy=(270, ibkr_prem[idx]), xytext=(222, -.12), fontsize=11, color=teal, arrowprops={"arrowstyle": "-", "color": teal, "lw": 1})
    ap.set_xlim(0, 342); ap.set_xticks([0, 60, 120, 150, 210, 270, 338]); ap.set_xticklabels(["09:30", "10:30", "11:30", "12:00 / 13:00", "14:00", "15:00", "16:08"])
    ap.set_xlabel("北京时间（港股12:00—13:00午休已压缩；16:00无独立报价）", labelpad=10)
    valid = ibkr_prem[np.isfinite(ibkr_prem)]
    fig.text(.075, .102, f"IBKR口径的 {len(valid)} 个可匹配分钟：ETF相对估值 {valid.min():.4f}% ～ {valid.max():.4f}%。", fontsize=14, color=ink, weight="bold")
    fig.text(.075, .067, "CNH.HKD 是“港元/离岸人民币”；估值换算使用 1 ÷ CNH.HKD 得到“人民币/港元”。未对缺失分钟插值。", fontsize=10.5, color=grey)
    fig.text(.075, .039, f"IBKR合约：{contract.localSymbol}（conId {contract.conId}，IDEALPRO）；历史数据：MIDPOINT、1 min、useRTH=False。", fontsize=10.5, color=grey)
    for suffix in ("png", "svg"):
        fig.savefig(ROOT / f"513090_20260908_分时价格与估值_含IBKR_CNHHKD.{suffix}", dpi=160 if suffix == "png" else None)
    plt.close(fig)


def main():
    quotes, contract = fetch_cnh_hkd_bars()
    rows = read_rows(quotes)
    save_rows(rows)
    plot(rows, contract)
    matched = sum(bool(row["IOPV_CNHHKD"]) for row in rows)
    print(f"contract={contract.localSymbol} conId={contract.conId} matched_minutes={matched}/{len(rows)}")
    print(OUTPUT)
    print(ROOT / "513090_20260908_分时价格与估值_含IBKR_CNHHKD.png")


if __name__ == "__main__":
    main()
