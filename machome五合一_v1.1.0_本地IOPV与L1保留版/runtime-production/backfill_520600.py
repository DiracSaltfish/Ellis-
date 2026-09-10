#!/usr/bin/env python3
"""Build an auditable 520600 historical-I0PV import bundle.

The official 广发 PCF is HTML, while historical minute prices come from the
native Linux TGW SDK on bj.  Credentials remain on bj in its systemd
EnvironmentFile; this script never reads, stores, or prints them.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import re
import shlex
import subprocess
import sys
import urllib.request
from pathlib import Path

ZONE = dt.timezone(dt.timedelta(hours=8))
FUND = "520600.SH"
FUND_NAME = "广发中证港股通汽车ETF"
GFF = "https://www.gffunds.com.cn/proxy/pcflist/520600?date={date}"


def command(args: list[str], *, input_text: str | None = None) -> str:
    return subprocess.run(args, input=input_text, text=True, check=True, stdout=subprocess.PIPE).stdout


def plain(value: str) -> str:
    return " ".join(html.unescape(re.sub(r"(?is)<[^>]+>", " ", value)).replace("\xa0", " ").split())


def num(value: str) -> float:
    return float(value.replace(",", "").strip())


def fetch_pcf(day: str) -> tuple[bytes, dict]:
    request = urllib.request.Request(GFF.format(date=day.replace("-", "")), headers={"User-Agent": "Mozilla/5.0"})
    raw = urllib.request.urlopen(request, timeout=20).read()
    body = raw.decode("utf-8")
    marker = day + "日内容信息"
    start = body.find(marker)
    if start < 0:
        raise ValueError(f"{day}: official PCF identity/date mismatch")
    fields = body[start:]
    fields = fields[: fields.lower().find("</table>") + len("</table>")]
    cash = unit = None
    for label, value in re.findall(r"(?is)<tr[^>]*>\s*<th[^>]*>(.*?)</th>\s*<td[^>]*>(.*?)</td>\s*</tr>", fields):
        label, value = plain(label), plain(value)
        if "预估现金部分" in label:
            cash = num(value)
        if "最小申购、赎回单位" in label and "单位:份" in label:
            unit = num(value)
    if not (cash is not None and unit and unit > 0):
        raise ValueError(f"{day}: required PCF fields missing")
    start = body.find("成份股信息内容")
    if start < 0:
        raise ValueError(f"{day}: component table missing")
    table = body[start:]
    table = table[: table.lower().find("</table>") + len("</table>")]
    components = []
    seen = set()
    for row in re.findall(r"(?is)<tr[^>]*>(.*?)</tr>", table):
        cells = [plain(c) for c in re.findall(r"(?is)<td[^>]*>(.*?)</td>", row)]
        if not cells or cells[0] == "证券代码":
            continue
        if len(cells) != 8 or not re.fullmatch(r"\d{1,5}", cells[0]) or cells[7] != "香港交易所":
            raise ValueError(f"{day}: malformed/non-HK component")
        symbol = cells[0].zfill(5) + ".HK"
        if symbol in seen or num(cells[2]) <= 0:
            raise ValueError(f"{day}: duplicate/invalid component")
        seen.add(symbol)
        components.append({"symbol": symbol, "quantity": num(cells[2])})
    if not components:
        raise ValueError(f"{day}: empty basket")
    return raw, {"symbol": FUND, "name": FUND_NAME, "trade_date": day, "pcf_sha256": hashlib.sha256(raw).hexdigest(), "unit": unit, "cash": cash, "components": components, "source": GFF.format(date=day.replace("-", ""))}


REMOTE_QUERY = r'''
import AmazingData as ad, tgw, json, os, sys
request = json.loads(sys.argv[1]); out = sys.argv[2]
result = {"schema": "tgw-native-kline.v1", "rows": {}, "errors": {}}
try:
    ad.login(username=os.environ["AMAZINGDATA_USERNAME"], password=os.environ["AMAZINGDATA_PASSWORD"], host="101.230.159.234", port=8600)
    for symbol in request["symbols"]:
        code, suffix = symbol.split(".")
        q = tgw.ReqKline(); q.security_code = code
        q.market_type = tgw.MarketType.kHKEx if suffix == "HK" else tgw.MarketType.kSSE
        q.cq_flag = q.cq_date = q.qj_flag = q.cyc_def = 0; q.cyc_type = 10000; q.auto_complete = 1
        q.begin_date = request["begin"]; q.end_date = request["end"]; q.begin_time = 930; q.end_time = 1600
        try:
            rows, err = tgw.QueryKline(q, return_df_format=False)
            if err or rows is None: result["errors"][symbol] = "query_error"
            else: result["rows"][symbol] = rows
        except Exception as exc: result["errors"][symbol] = type(exc).__name__
finally:
    try: ad.logout(os.environ.get("AMAZINGDATA_USERNAME", ""))
    except Exception: pass
with open(out, "w", encoding="utf-8") as handle: json.dump(result, handle, ensure_ascii=False, separators=(",", ":"))
'''


def native_quotes(symbols: list[str], begin: int, end: int, target: Path) -> dict:
    if target.exists():
        cached = json.loads(target.read_text())
        if cached.get("schema") == "tgw-native-kline.v1":
            return cached
    remote = f"/tmp/520600-tgw-{begin}-{end}.json"
    payload = json.dumps({"symbols": symbols, "begin": begin, "end": end}, separators=(",", ":"))
    shell = "set -euo pipefail; set -a; . /etc/galaxy-relay/relay.env; set +a; exec /opt/galaxy-relay/venv/bin/python - '" + payload + "' " + remote + " >/dev/null 2>&1"
    command(["ssh", "bj", "sudo -n /bin/bash -c " + shlex.quote(shell)], input_text=REMOTE_QUERY)
    command(["scp", f"bj:{remote}", str(target)])
    return json.loads(target.read_text())


def minute_map(rows: list[dict], day: str, symbol: str) -> dict[str, dict]:
    result: dict[str, dict] = {}
    expected_code = symbol.split(".")[0]
    for row in rows:
        stamp = int(row["kline_time"])
        if str(stamp)[:8] != day.replace("-", "") or str(row["security_code"]).zfill(len(expected_code)) != expected_code:
            continue
        hhmm = stamp % 10000
        minute = f"{hhmm // 100:02d}:{hhmm % 100:02d}"
        if minute < "09:30" or minute > "16:00" or ("12:00" < minute < "13:00"):
            continue
        if int(row.get("close_price", 0)) <= 0:
            continue
        if minute in result:
            raise ValueError(f"{day} {symbol}: duplicate minute")
        # This is the SDK's actual row for the minute, never an artificial
        # carry/forward fill.  A zero-turnover row is retained for historical
        # valuation and surfaced in the stored point; it remains non-signal.
        result[minute] = {"price": int(row["close_price"]) / 1_000_000, "volume": int(row.get("volume_trade", 0))}
    return result


def fx_history(path: Path) -> dict[str, dict]:
    """Load the auditable 1navs gold-line series for this exact backfill."""
    source = json.loads(path.read_text())
    if source.get("field") != "人民币对港币中间价（HKD/CNY）" or source.get("unit") != "CNY per HKD":
        raise ValueError("unexpected central-parity input")
    rows = source.get("days")
    if not isinstance(rows, list):
        raise ValueError("invalid central-parity input")
    by_date = {r.get("trade_date"): r for r in rows if isinstance(r, dict)}
    if len(by_date) != len(rows) or any(not isinstance(r.get("hkd_cny_central_parity"), (int, float)) or r["hkd_cny_central_parity"] <= 0 for r in rows):
        raise ValueError("invalid central-parity rates")
    return by_date


def sessions() -> list[str]:
    out = []
    # HK equities trade through 12:00 and from 13:00 through 16:00.  This
    # deliberately includes the 30-minute A-share lunch overlap (11:31–12:00)
    # and the 60-minute post-A-close window (15:01–16:00).
    for hour, start, end in ((9, 30, 59), (10, 0, 59), (11, 0, 59), (12, 0, 0), (13, 0, 59), (14, 0, 59), (15, 0, 59), (16, 0, 0)):
        for minute in range(start, end + 1): out.append(f"{hour:02d}:{minute:02d}")
    return out


def build(args: argparse.Namespace) -> None:
    days = [d.strip() for d in args.days.split(",") if d.strip()]
    archive = Path(args.archive).resolve(); archive.mkdir(parents=True, exist_ok=True)
    pcfs: dict[str, dict] = {}
    for day in days:
        raw, pcf = fetch_pcf(day)
        (archive / f"pcf-{day}.html").write_bytes(raw)
        pcfs[day] = pcf
    symbols = sorted({FUND, *(c["symbol"] for p in pcfs.values() for c in p["components"])})
    native = native_quotes(symbols, int(days[0].replace("-", "")), int(days[-1].replace("-", "")), archive / "native-tgw-kline.json")
    if native["errors"]:
        raise RuntimeError("native TGW source rejected: " + ", ".join(sorted(native["errors"])))
    daily_fx = fx_history(Path(args.fx_history))
    stamp = dt.datetime.now(tz=ZONE).isoformat()
    points, summary = [], {"days": {}, "source": "native TGW exact one-minute close; 广发基金 PCF; 1navs 人民币对港币中间价 + 最终结算汇率", "rows": 0}
    for day, pcf in pcfs.items():
        q = {symbol: minute_map(native["rows"].get(symbol, []), day, symbol) for symbol in symbols}
        day_fx = daily_fx.get(day)
        # The gold 1navs curve is RMB per HKD central parity.  A different
        # CFETS reference rate must never be substituted for this field.
        mid = (day_fx or {}).get("hkd_cny_central_parity")
        buy = (day_fx or {}).get("actual_buy_settlement")
        sell = (day_fx or {}).get("actual_sell_settlement")
        if not mid or mid <= 0:
            raise RuntimeError(f"{day}: missing daily HKD/CNY midpoint")
        successful = premium = settlement = 0
        last_etf = None
        for minute in sessions():
            missing = [c["symbol"] for c in pcf["components"] if minute not in q[c["symbol"]]]
            observed_etf = q[FUND].get(minute)
            if observed_etf:
                last_etf = {**observed_etf, "minute": minute}
            etf = last_etf
            carried_etf = observed_etf is None and etf is not None
            assets = None if missing else sum(c["quantity"] * q[c["symbol"]][minute]["price"] for c in pcf["components"])
            zero_turnover = [c["symbol"] for c in pcf["components"] if minute in q[c["symbol"]] and q[c["symbol"]][minute]["volume"] == 0]
            point = {"symbol": FUND, "name": FUND_NAME, "trade_date": day, "calculated_at": stamp, "minute": minute, "pcf_sha256": pcf["pcf_sha256"], "channel": "shanghai", "midpoint_fx": mid, "components": len(pcf["components"]), "priced": len(pcf["components"]) - len(missing), "stale": zero_turnover, "reasons": ["historical_reconstruction", "source:native_tgw_exact_minute", "fx:1navs_rmb_hkd_central_parity", "not_live_signal"], "eligible_for_signal": False, "run_id": "520600-history-backfill", "mode": "historical_reconstruction", "unit": pcf["unit"], "cash": pcf["cash"]}
            if zero_turnover:
                point["reasons"].append("component_zero_turnover_minute_close_retained")
            if carried_etf:
                point["reasons"].append("etf_price_carried_from_last_a_share_minute")
            if assets is not None:
                value = (assets * mid + pcf["cash"]) / pcf["unit"]
                point.update({"midpoint_iopv": value, "hkd_assets": assets, "cny_assets": pcf["cash"]}); successful += 1
                if etf:
                    etf_price = etf["price"]
                    point.update({"etf_price": etf_price, "etf_quote_at": f"{day}T{etf['minute']}:00+08:00", "etf_price_carried": carried_etf, "midpoint_premium_pct": (etf_price / value - 1) * 100}); premium += 1
                if buy and sell:
                    buy_value, sell_value = (assets * sell + pcf["cash"]) / pcf["unit"], (assets * buy + pcf["cash"]) / pcf["unit"]
                    point.update({"settlement_buy_fx": sell, "settlement_sell_fx": buy, "settlement_buy_iopv": buy_value, "settlement_sell_iopv": sell_value})
                    if etf: point.update({"settlement_buy_premium_pct": (etf_price / buy_value - 1) * 100, "settlement_sell_premium_pct": (etf_price / sell_value - 1) * 100})
                    settlement += 1
                else:
                    point["reasons"].append("official_final_settlement_unavailable")
            else:
                point["reasons"].append("missing_exact_component_minute")
            points.append(point)
        summary["days"][day] = {"components": len(pcf["components"]), "midpoint_rows": successful, "premium_rows": premium, "settlement_rows": settlement, "midpoint_fx": mid, "actual_buy_settlement": buy, "actual_sell_settlement": sell}
    output = archive / "validated.jsonl"
    output.write_text("".join(json.dumps(p, ensure_ascii=False, allow_nan=False) + "\n" for p in points))
    (archive / "pcf-index.json").write_text(json.dumps(pcfs, ensure_ascii=False, indent=2))
    summary["rows"] = len(points); summary["sha256"] = hashlib.sha256(output.read_bytes()).hexdigest()
    (archive / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", required=True, help="comma-separated YYYY-MM-DD trading dates")
    parser.add_argument("--archive", required=True)
    parser.add_argument("--fx-history", default=str(Path(__file__).with_name("fx-520600-central-parity-20260901-20260909.json")))
    build(parser.parse_args())
