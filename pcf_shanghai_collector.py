#!/usr/bin/env python3
"""Download and normalize official ETF PCF files for the Shanghai universe.

The collector is deliberately append-only at the per-target level.  Each
attempt and each normalized record is written as a gzip JSONL member, so an
interrupted run can be resumed without losing the data already obtained.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import hashlib
import html
import json
import re
import shutil
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Iterable


WORKSPACE = Path("/Users/ellis/工具程序开发")
UNIVERSE_PATH = WORKSPACE / "machome五合一_v1.1.0_本地IOPV与L1保留版/services/iopv/universe.json"
DEFAULT_ROOT = WORKSPACE / "tmp/pcf_shanghai_1y_20250909_20260909"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 ETF-PCF-Collector/1.0"
HTTP_TIMEOUT = 30
PRINT_LOCK = threading.Lock()


class NoData(Exception):
    pass


def log(message: str) -> None:
    with PRINT_LOCK:
        print(message, flush=True)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    p.add_argument("--start", default="2025-09-09")
    p.add_argument("--end", default="2026-09-09")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--only-code", action="append", default=[])
    p.add_argument("--refresh-code", action="append", default=[], help="re-fetch these codes into a recoverable revision")
    p.add_argument("--probe", action="store_true", help="query only the end date")
    return p.parse_args()


def dates_between(start: dt.date, end: dt.date) -> list[dt.date]:
    out = []
    cur = start
    while cur <= end:
        if cur.weekday() < 5:
            out.append(cur)
        cur += dt.timedelta(days=1)
    return out


def norm_date(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    m = re.search(r"(20\d{2})[-/]?(\d{2})[-/]?(\d{2})", s)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", html.unescape(str(value))).strip()


def num(value: Any) -> int | float | None:
    if value is None:
        return None
    s = clean_text(value).replace(",", "").replace("，", "")
    if not s or s in {"-", "—", "--", "N/A", "null", "None"}:
        return None
    s = s.replace("%", "")
    m = re.search(r"[-+]?\d+(?:\.\d+)?", s)
    if not m:
        return None
    x = float(m.group(0))
    return int(x) if x.is_integer() else x


def pct(value: Any) -> int | float | None:
    """Return percentage points, regardless of provider convention.

    XML providers commonly publish 0.15 for 15%, while HTML/JSON pages
    commonly publish ``15.0%``.  The normalized schema uses percentage
    points (15.0), matching the reference CSV naming.
    """
    x = num(value)
    if x is None:
        return None
    raw = clean_text(value)
    if "%" not in raw and abs(float(x)) <= 1:
        x = float(x) * 100
        return int(x) if x.is_integer() else x
    return x


def bool_value(value: Any) -> bool | None:
    s = clean_text(value).lower()
    if not s:
        return None
    if s in {"1", "y", "yes", "true", "是", "公布", "需要", "允许", "申购和赎回皆允许"}:
        return True
    if s in {"0", "n", "no", "false", "否", "不公布", "不需要", "不允许"}:
        return False
    return None


def canonical_flag(value: Any) -> str:
    raw = clean_text(value)
    if not raw:
        return "UNKNOWN"
    if any(x in raw for x in ("退补", "补券", "非沪深市场")):
        return "REPLENISH"
    if any(x in raw for x in ("禁止", "不允许", "不得")):
        return "PROHIBITED"
    if any(x in raw for x in ("必须", "强制")):
        return "REQUIRED"
    if any(x in raw for x in ("允许", "可替代", "允许现金")):
        return "ALLOWED"
    # Some providers use numeric flags.  Preserve the raw code separately.
    if raw in {"0"}:
        return "PROHIBITED"
    if raw in {"1", "2"}:
        return "ALLOWED"
    if raw in {"5"}:
        return "REPLENISH"
    return "UNKNOWN"


def market_name(value: Any, underlying: Any = None) -> str | None:
    s = clean_text(value).upper()
    if "香港" in s or "港交" in s or "港股" in s or "港" in s or "HKEX" in s or s in {"103", "HK"}:
        return "HK"
    if "上海" in s or "SSE" in s or s in {"101", "SH"}:
        return "SH"
    if "深圳" in s or "SZSE" in s or s in {"102", "SZ"}:
        return "SZ"
    s2 = clean_text(underlying).upper()
    if s2 in {"HK", "HKEX"}:
        return "HK"
    return None


def component_code(value: Any, market: str | None) -> str:
    s = clean_text(value)
    if not s:
        return ""
    if s.isdigit():
        width = 5 if market == "HK" else 6
        return str(int(s)).zfill(width)
    return s


def local_tag(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def xml_values(root: ET.Element) -> dict[str, str]:
    values: dict[str, str] = {}
    for elem in root.iter():
        key = local_tag(elem.tag)
        if elem.text and clean_text(elem.text):
            values.setdefault(key, clean_text(elem.text))
    return values


def xml_find(root: ET.Element, names: Iterable[str]) -> str | None:
    wanted = set(names)
    for elem in root.iter():
        if local_tag(elem.tag) in wanted:
            return clean_text(elem.text)
    return None


def xml_components(root: ET.Element) -> list[dict[str, Any]]:
    rows = []
    for elem in root.iter():
        if local_tag(elem.tag) not in {"Component", "ETFComponent", "Constituent"}:
            continue
        vals = {local_tag(c.tag): clean_text(c.text) for c in elem.iter() if c is not elem}
        if not any(k in vals for k in ("InstrumentID", "StockCode", "SecurityID", "InstrumentCode")):
            continue
        raw_market = vals.get("UnderlyingSecurityID") or vals.get("Market") or vals.get("ListingMarket")
        market = market_name(raw_market, raw_market)
        code = vals.get("InstrumentID") or vals.get("StockCode") or vals.get("SecurityID") or vals.get("InstrumentCode")
        rows.append({
            "component_code": component_code(code, market),
            "component_name": vals.get("InstrumentName") or vals.get("StockName") or vals.get("SecurityName"),
            "component_market": market,
            "quantity_shares": num(vals.get("Quantity") or vals.get("ShareNumber") or vals.get("Shares")),
            "cash_substitution_flag": canonical_flag(vals.get("SubstitutionFlag") or vals.get("CashSubstitutionMark")),
            "cash_substitution_flag_raw": vals.get("SubstitutionFlag") or vals.get("CashSubstitutionMark"),
            "purchase_cash_premium_pct": pct(vals.get("CreationPremiumRate") or vals.get("PremiumPercentage")),
            "redemption_cash_discount_pct": pct(vals.get("RedemptionDiscountRate") or vals.get("DiscountPercentage")),
            "fixed_substitution_amount_cny": num(vals.get("SubstitutionCashAmount") or vals.get("ReplaceAmount") or vals.get("ReplacementAmount")),
        })
    return rows


def parse_xml(body: bytes, requested_code: str, requested_date: str, source_url: str, source_format: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    try:
        root = ET.fromstring(body)
    except ET.ParseError as e:
        raise NoData(f"not XML: {e}")
    v = xml_values(root)
    code = xml_find(root, ("FundInstrumentID", "FundCode", "FundID")) or requested_code
    if re.sub(r"\D", "", code).zfill(6) != requested_code:
        raise NoData(f"returned fund {code}, requested {requested_code}")
    trading_day = norm_date(xml_find(root, ("TradingDay", "TradeDate", "AnnouncementDate")))
    if trading_day and trading_day != requested_date:
        raise NoData(f"returned date {trading_day}, requested {requested_date}")
    market = None
    rows = xml_components(root)
    if rows:
        market = rows[0].get("component_market")
    raw_switch = xml_find(root, ("CreationRedemptionSwitch", "CreationRedemptionMechanism", "CreationRedemptionStatus"))
    basic = {
        "fund_code": requested_code,
        "symbol": requested_code + ".SH",
        "fund_name": xml_find(root, ("FundName", "ShortName")),
        "fund_company": xml_find(root, ("FundCompanyName", "FundManagementCompanyName", "CompanyName")),
        "trade_date": trading_day or requested_date,
        "prev_trade_date": norm_date(xml_find(root, ("PreTradingDay", "PreviousTradingDay"))),
        "prev_cash_difference_cny": num(xml_find(root, ("PreCashComponent", "CashComponent"))),
        "prev_unit_asset_nav_cny": num(xml_find(root, ("NAVperCU", "NAVPerCU"))),
        "prev_nav_cny": num(xml_find(root, ("NAV", "UnitNAV"))),
        "estimated_cash_component_cny": num(xml_find(root, ("EstimatedCashComponent", "EstimateCashComponent"))),
        "cash_substitution_limit_pct": pct(xml_find(root, ("MaxCashRatio", "MaxCashSubstitutionRatio"))),
        "iopv_published": bool_value(xml_find(root, ("PublishIOPVFlag", "IOPVFlag"))),
        "creation_redemption_unit_shares": num(xml_find(root, ("CreationRedemptionUnit", "CreationRedemptionUnitShares"))),
        "creation_limit_shares": num(xml_find(root, ("CreationLimit", "CreationLimitQuantity"))),
        "redemption_limit_shares": num(xml_find(root, ("RedemptionLimit", "RedemptionLimitQuantity"))),
        "net_creation_limit_shares": num(xml_find(root, ("NetCreationLimit",))),
        "net_redemption_limit_shares": num(xml_find(root, ("NetRedemptionLimit",))),
        "creation_allowed": None,
        "redemption_allowed": None,
        "redemption_mode": raw_switch,
        "component_count": len(rows),
        "source_url": source_url,
        "source_format": source_format,
    }
    switch = clean_text(raw_switch)
    if switch:
        basic["creation_allowed"] = "申购" in switch or switch in {"1", "Y", "YES"}
        basic["redemption_allowed"] = "赎回" in switch or switch in {"1", "Y", "YES"}
    return basic, rows


def request_bytes(url: str, data: bytes | None = None, headers: dict[str, str] | None = None) -> bytes:
    h = {"User-Agent": UA, "Accept": "*/*"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code in {400, 404, 410}:
            raise NoData(f"HTTP {e.code}")
        raise


def fetch_xml_huaan(code: str, day: dt.date) -> tuple[bytes, str, str]:
    d = day.strftime("%Y%m%d")
    url = f"https://www.huaan.com.cn/etf/{code}/etffiledownload.jsp?etffilename=etfd_{code}_{d}.xml"
    return request_bytes(url), url, "XML_HUAAN"


def fetch_xml_htpb(code: str, day: dt.date) -> tuple[bytes, str, str]:
    d = day.strftime("%Y%m%d")
    url = f"https://www.huatai-pb.com/etf-web/etf/download?filePath=etfd_{code}_{d}.xml"
    return request_bytes(url), url, "XML_HTPB"


def fetch_xml_bosera(code: str, day: dt.date) -> tuple[bytes, str, str]:
    d = day.strftime("%Y%m%d")
    url = f"https://www.bosera.com/jjcp/etf/files/{code}/{day.year}/ssepcf_{code}_{d}.xml"
    return request_bytes(url), url, "XML_BOSERA"


def fetch_efunds(code: str, day: dt.date) -> tuple[bytes, str, str]:
    ds = day.isoformat()
    api = "https://api.efunds.com.cn/xcowch/front/etffund/downfile?" + urllib.parse.urlencode({"fundCode": code, "tDate": ds, "listType": "ON_SITE"})
    payload = json.loads(request_bytes(api).decode("utf-8-sig"))
    file_url = (payload.get("data") or {}).get("fileName")
    if not file_url:
        raise NoData(f"Efunds returned no file: {payload.get('status')}")
    return request_bytes(file_url), file_url, "XML_EFUNDS"


def html_cells(row: str) -> list[str]:
    cells = re.findall(r"<(?:td|th)\b[^>]*>(.*?)</(?:td|th)>", row, flags=re.I | re.S)
    return [clean_text(re.sub(r"<[^>]+>", " ", c)) for c in cells]


def html_label_value(body: str, label: str) -> str | None:
    def compact(x: str) -> str:
        return re.sub(r"[\s:：]", "", x)
    wanted = compact(label)
    for tr in re.findall(r"<tr\b[^>]*>.*?</tr>", body, flags=re.I | re.S):
        cells = html_cells(tr)
        for i, cell in enumerate(cells[:-1]):
            if wanted in compact(cell):
                return cells[i + 1]
    return None


def parse_gf(body: bytes, code: str, requested_date: str, source_url: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    text = body.decode("utf-8", "replace")
    actual = None
    m = re.search(r'name=["\']date["\'][^>]*value=["\'](\d{8})', text, re.I)
    if m:
        actual = norm_date(m.group(1))
    if actual and actual != requested_date:
        raise NoData(f"returned date {actual}, requested {requested_date}")
    title = clean_text(re.sub(r"<[^>]+>", " ", re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S).group(1))) if re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S) else ""
    if code not in title and not re.search(rf"\b{code}\b", text):
        raise NoData("GF page does not contain requested fund")
    rows = []
    for tr in re.findall(r"<tr\b[^>]*>.*?</tr>", text, flags=re.I | re.S):
        cells = html_cells(tr)
        if len(cells) < 8 or not re.fullmatch(r"\d{4,6}", cells[0].replace(".", "")):
            continue
        # 广发历史页面曾把“挂牌市场”放在代码/名称之后；新页面把
        # 它放在最后。 Detect the market cell instead of assuming a
        # fixed column order, otherwise quantity and replacement flags shift.
        def is_gf_market_cell(cell: str) -> bool:
            s = clean_text(cell).upper()
            return any(token in s for token in ("香港", "上海", "深圳", "交易所", "HKEX", "SSE", "SZSE"))
        market_idx = next((i for i, cell in enumerate(cells[2:], 2) if is_gf_market_cell(cell)), None)
        if market_idx == 2:
            market = market_name(cells[market_idx])
            quantity, flag, premium, discount, amount = cells[3:8]
        else:
            market = market_name(cells[7])
            quantity, flag, premium, discount, amount = cells[2:7]
        rows.append({
            "component_code": component_code(cells[0], market),
            "component_name": cells[1],
            "component_market": market,
            "quantity_shares": num(quantity),
            "cash_substitution_flag": canonical_flag(flag),
            "cash_substitution_flag_raw": flag,
            "purchase_cash_premium_pct": pct(premium),
            "redemption_cash_discount_pct": pct(discount),
            "fixed_substitution_amount_cny": num(amount),
        })
    if not rows:
        raise NoData("GF page has no constituent rows")
    basic = {
        "fund_code": code,
        "symbol": code + ".SH",
        "fund_name": html_label_value(text, "基金名称"),
        "fund_company": html_label_value(text, "基金管理公司名称"),
        "trade_date": actual or requested_date,
        "prev_trade_date": None,
        "prev_cash_difference_cny": num(html_label_value(text, "现金差额(单位:元)")),
        "prev_unit_asset_nav_cny": num(html_label_value(text, "最小申购、赎回单位净值(单位：元)")),
        "prev_nav_cny": num(html_label_value(text, "基金份额净值(单位：元)")),
        "estimated_cash_component_cny": num(html_label_value(text, "最小申购、赎回单位的预估现金部分(单位:元)")),
        "cash_substitution_limit_pct": pct(html_label_value(text, "现金替代比例上限")),
        "iopv_published": bool_value(html_label_value(text, "是否需要公布IOPV")),
        "creation_redemption_unit_shares": num(html_label_value(text, "最小申购、赎回单位(单位:份)")),
        "creation_limit_shares": num(html_label_value(text, "当日累计可申购")),
        "redemption_limit_shares": num(html_label_value(text, "当日累计可赎回")),
        "net_creation_limit_shares": num(html_label_value(text, "当日净申购")),
        "net_redemption_limit_shares": num(html_label_value(text, "当日净赎回")),
        "creation_allowed": "申购" in clean_text(html_label_value(text, "申购赎回的允许情况")),
        "redemption_allowed": "赎回" in clean_text(html_label_value(text, "申购赎回的允许情况")),
        "redemption_mode": html_label_value(text, "申购赎回模式"),
        "component_count": len(rows),
        "source_url": source_url,
        "source_format": "HTML_GF",
    }
    # The GF page labels the latest announcement date; the previous trading
    # date is not always separately exposed in the HTML.
    return basic, rows


def fetch_wanjia(code: str, day: dt.date) -> tuple[bytes, str, str]:
    # 万家基金's etf-web serves an HTML PCF page keyed by an explicit
    # beginDate, so any mainland trading day in the archive is reachable.
    # The backend occasionally returns a bare server-error page under load,
    # so transient failures are retried with a short pause before giving up.
    url = "https://www.wjasset.com/etf-web/etf/v2?" + urllib.parse.urlencode({"fundcode": code, "beginDate": day.isoformat()})
    last_body = b""
    for attempt in range(4):
        body = request_bytes(url)
        text = body.decode("utf-8", "replace")
        if "<table" in text and "此页无法显示" not in text and "服务器异常" not in text:
            return body, url, "HTML_WANJIA"
        last_body = body
        time.sleep(2 + 2 * attempt)
    if "此页无法显示" in text or "服务器异常" in text:
        raise RuntimeError("Wanjia server error page persisted")
    raise NoData("empty wanjia response")


def wanjia_label_map(rows: list[list[str]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for row in rows:
        for i in range(0, len(row) - 1, 2):
            key = clean_text(row[i])
            if not key or i + 1 >= len(row):
                continue
            out[key] = clean_text(row[i + 1])
    return out


def parse_wanjia(body: bytes, code: str, requested_date: str, source_url: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    text = body.decode("utf-8", "replace")
    text = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", " ", text, flags=re.I | re.S)
    # Date-group headings mark the T-1 block and the T-day block.
    headings = re.findall(r"(20\d{2}-\d{2}-\d{2})日\s*信息内容", text)
    if not headings:
        raise NoData("Wanjia page has no dated PCF block")
    if headings[-1] != requested_date:
        raise NoData(f"returned date {headings[-1]}, requested {requested_date}")
    tables: list[list[list[str]]] = []
    for tm in re.finditer(r"<table\b[^>]*>(.*?)</table>", text, flags=re.I | re.S):
        rows: list[list[str]] = []
        for tr in re.finditer(r"<tr\b[^>]*>(.*?)</tr>", tm.group(1), flags=re.I | re.S):
            cells = [clean_text(re.sub(r"<[^>]+>", " ", c)) for c in re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", tr.group(1), flags=re.I | re.S)]
            if any(cells):
                rows.append(cells)
        tables.append(rows)
    basic_info: dict[str, str] = {}
    prev_info: dict[str, str] = {}
    day_info: dict[str, str] = {}
    constituents: list[list[str]] | None = None
    for rows in tables:
        header = " ".join(rows[0]) if rows else ""
        if "最新公告日期" in header:
            basic_info = wanjia_label_map(rows)
        elif any("预估现金差额" in c for row in rows for c in row):
            day_info = wanjia_label_map(rows)
        elif any("现金差额" in c for row in rows for c in row):
            prev_info = wanjia_label_map(rows)
        elif "股票代码" in header and "固定替代金额" in header:
            constituents = [row for row in rows[1:] if len(row) >= 7 and row[0].isdigit()]
    if constituents is None:
        raise NoData("Wanjia page has no constituent table")
    actual_code = re.sub(r"\D", "", basic_info.get("基金代码") or "").zfill(6)
    if actual_code and actual_code != code:
        raise NoData(f"returned fund {actual_code}, requested {code}")
    rows: list[dict[str, Any]] = []
    for row in constituents:
        raw_code = clean_text(row[1])
        market = "HK" if raw_code.isdigit() and len(str(int(raw_code))) <= 5 else None
        rows.append({
            "component_code": component_code(raw_code, market),
            "component_name": clean_text(row[2]),
            "component_market": market,
            "quantity_shares": num(row[3]),
            "cash_substitution_flag": canonical_flag(row[4]),
            "cash_substitution_flag_raw": clean_text(row[4]),
            "purchase_cash_premium_pct": pct(row[5]),
            "redemption_cash_discount_pct": pct(row[6]),
            "fixed_substitution_amount_cny": num(row[7]) if len(row) > 7 else None,
        })
    if not rows:
        raise NoData("Wanjia page has no constituent rows")
    allow = clean_text(day_info.get("申购/赎回切换") or day_info.get("申购赎回切换"))
    basic = {
        "fund_code": code,
        "symbol": code + ".SH",
        "fund_name": basic_info.get("基金名称"),
        "fund_company": basic_info.get("基金管理人公司名称"),
        "trade_date": headings[-1],
        "prev_trade_date": headings[0] if len(headings) > 1 else None,
        "prev_cash_difference_cny": num(prev_info.get("现金差额(单位:元)") or prev_info.get("现金差额（单位：元）")),
        "prev_unit_asset_nav_cny": num(prev_info.get("最小申赎单位净值(单位:元)") or prev_info.get("最小申赎单位净值（单位：元）") or prev_info.get("最小申购赎回单位净值(单位:元)") or prev_info.get("最小申购赎回单位净值（单位：元）")),
        "prev_nav_cny": num(prev_info.get("基金份额净值(单位:元)") or prev_info.get("基金份额净值（单位：元）")),
        "estimated_cash_component_cny": num(day_info.get("T日每个篮子的预估现金差额(单位:元)") or day_info.get("T日每个篮子的预估现金差额（单位：元）")),
        "cash_substitution_limit_pct": pct(day_info.get("总的现金替代比例") or day_info.get("现金替代比例上限")),
        "iopv_published": bool_value(day_info.get("是否需要公布IOPV")),
        "creation_redemption_unit_shares": num(day_info.get("每个篮子（最小申购、赎回单位）对应的ETF份数") or day_info.get("最小申购、赎回单位(单位:份)") or day_info.get("最小申购、赎回单位（单位：份）")),
        "creation_limit_shares": num(day_info.get("申购上限") or day_info.get("当日累计可申购")),
        "redemption_limit_shares": num(day_info.get("当日赎回限额") or day_info.get("当日累计可赎回")),
        "net_creation_limit_shares": num(day_info.get("当日净申购的基金份额上限")),
        "net_redemption_limit_shares": num(day_info.get("当日净赎回的基金份额上限")),
        "creation_allowed": None if not allow else ("申购" in allow),
        "redemption_allowed": None if not allow else ("赎回" in allow),
        "redemption_mode": day_info.get("申赎模式"),
        "component_count": len(rows),
        "source_url": source_url,
        "source_format": "HTML_WANJIA",
    }
    return basic, rows


def json_label_map(items: Any) -> dict[str, Any]:
    if not isinstance(items, list):
        return {}
    return {clean_text(x.get("label")): x.get("value") for x in items if isinstance(x, dict) and "label" in x}


def fetch_chinaamc(code: str, day: dt.date) -> tuple[bytes, str, str]:
    url = "https://accountquery.chinaamc.com/front/front/out/etf/tradeList"
    form = urllib.parse.urlencode({"fundCode": code, "queryDate": day.isoformat(), "instType": ""}).encode()
    return request_bytes(url, form, {"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"}), url, "JSON_CHINAAMC"


def parse_chinaamc(body: bytes, code: str, requested_date: str, source_url: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    payload = json.loads(body.decode("utf-8-sig"))
    if payload.get("status") != 1 or not isinstance(payload.get("data"), dict):
        raise NoData(payload.get("message") or "ChinaAMC no data")
    data = payload["data"]
    actual = norm_date(data.get("secondDate") or data.get("latestDate") or requested_date)
    if actual != requested_date:
        raise NoData(f"returned date {actual}, requested {requested_date}")
    base = json_label_map(data.get("baseInfoContent"))
    first = json_label_map(data.get("firstContent"))
    second = json_label_map(data.get("secondContent"))
    rows = []
    for x in data.get("stockResponseList") or []:
        market = market_name(x.get("listingMarket"))
        rows.append({
            "component_code": component_code(x.get("stockCode"), market),
            "component_name": clean_text(x.get("stockName")),
            "component_market": market,
            "quantity_shares": num(x.get("shareNumber")),
            "cash_substitution_flag": canonical_flag(x.get("cashSubstitutionMark")),
            "cash_substitution_flag_raw": clean_text(x.get("cashSubstitutionMark")),
            "purchase_cash_premium_pct": pct(x.get("premiumPercentage")),
            "redemption_cash_discount_pct": pct(x.get("discountPercentage")),
            "fixed_substitution_amount_cny": num(x.get("replaceAmount") or x.get("subscripReplacAmount")),
        })
    if not rows:
        raise NoData("ChinaAMC has no constituents")
    allow = clean_text(second.get("申购赎回的允许情况"))
    basic = {
        "fund_code": code,
        "symbol": code + ".SH",
        "fund_name": base.get("基金名称"),
        "fund_company": base.get("基金管理公司名称"),
        "trade_date": actual,
        "prev_trade_date": norm_date(data.get("firstDate")),
        "prev_cash_difference_cny": num(first.get("现金差额(单位:元)")),
        "prev_unit_asset_nav_cny": num(first.get("最小申购、赎回单位资产净值(单位:元)")),
        "prev_nav_cny": num(first.get("基金份额净值(单位:元)")),
        "estimated_cash_component_cny": num(second.get("最小申购、赎回单位的预估现金部分(单位:元)")),
        "cash_substitution_limit_pct": pct(second.get("现金替代比例上限")),
        "iopv_published": bool_value(second.get("是否需要公布IOPV")),
        "creation_redemption_unit_shares": num(second.get("最小申购、赎回单位(单位:份)")),
        "creation_limit_shares": num(second.get("当日累计可申购的基金份额上限")),
        "redemption_limit_shares": num(second.get("当日累计可赎回的基金份额上限")),
        "net_creation_limit_shares": num(second.get("当日净申购的基金份额上限")),
        "net_redemption_limit_shares": num(second.get("当日净赎回的基金份额上限")),
        "creation_allowed": "申购" in allow,
        "redemption_allowed": "赎回" in allow,
        "redemption_mode": second.get("申购赎回模式"),
        "component_count": len(rows),
        "source_url": source_url,
        "source_format": "JSON_CHINAAMC",
    }
    return basic, rows


def fetch_nanfang(code: str, day: dt.date) -> tuple[bytes, str, str]:
    # The official nffund.com front door may return 403 to scripted requests;
    # southernfund.com is the manager's official alternate domain and exposes
    # the same first-party API with the same response schema.
    url = "https://www.southernfund.com/nfwebApi/trade/subAndRedempList"
    form = urllib.parse.urlencode({"fundCode": code, "queryDate": day.isoformat()}).encode()
    return request_bytes(url, form, {"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"}), url, "JSON_NANFANG"


def parse_nanfang(body: bytes, code: str, requested_date: str, source_url: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    payload = json.loads(body.decode("utf-8-sig"))
    data = payload.get("data")
    if not isinstance(data, dict) or payload.get("code") not in {"ETS-5BP00000", 0, "0", None}:
        raise NoData(payload.get("message") or "Nanfang no data")
    actual = norm_date(data.get("TradingDay") or data.get("tradeDate"))
    if actual != requested_date:
        raise NoData(f"returned date {actual}, requested {requested_date}")
    source_rows = data.get("data") or data.get("list") or data.get("stockList") or data.get("constituentList") or []
    if isinstance(source_rows, dict):
        source_rows = source_rows.get("rows") or source_rows.get("list") or []
    rows = []
    for x in source_rows:
        if not isinstance(x, dict):
            continue
        market = market_name(x.get("market")) or "HK"
        raw_code = x.get("stockCode") or x.get("securityCode")
        rows.append({
            "component_code": component_code(raw_code, market),
            "component_name": clean_text(x.get("stockName") or x.get("securityName")),
            "component_market": market,
            "quantity_shares": num(x.get("stockQuality") or x.get("quantity") or x.get("shareNumber")),
            "cash_substitution_flag": canonical_flag(x.get("permit")),
            "cash_substitution_flag_raw": clean_text(x.get("permit")),
            "purchase_cash_premium_pct": pct(x.get("percent") or x.get("premiumPercentage")),
            "redemption_cash_discount_pct": pct(x.get("discountRatio") or x.get("discountPercentage")),
            "fixed_substitution_amount_cny": num(x.get("subMoney") or x.get("subMoney2") or x.get("replaceAmount")),
        })
    if not rows:
        raise NoData("Nanfang has no constituents")
    basic = {
        "fund_code": code,
        "symbol": code + ".SH",
        "fund_name": data.get("fundName"),
        "fund_company": "南方基金管理股份有限公司",
        "trade_date": actual,
        "prev_trade_date": norm_date(data.get("PreTradingDay") or data.get("preTradingDay")),
        "prev_cash_difference_cny": num(data.get("CashComponent")),
        "prev_unit_asset_nav_cny": num(data.get("NAVperCU")),
        "prev_nav_cny": num(data.get("NAV")),
        "estimated_cash_component_cny": num(data.get("EstimateCashComponent")),
        "cash_substitution_limit_pct": pct(data.get("MaxCashRatio")),
        "iopv_published": bool_value(data.get("PublishIOPVFlag") or data.get("IOPVFlag")),
        "creation_redemption_unit_shares": num(data.get("CreationRedemptionUnit")),
        "creation_limit_shares": num(data.get("subLimit")),
        "redemption_limit_shares": num(data.get("redeemLimit")),
        "net_creation_limit_shares": None,
        "net_redemption_limit_shares": None,
        "creation_allowed": None,
        "redemption_allowed": None,
        "redemption_mode": None,
        "component_count": len(rows),
        "source_url": source_url,
        "source_format": "JSON_NANFANG",
    }
    return basic, rows


def fetch_99fund(code: str, day: dt.date) -> tuple[bytes, str, str]:
    url = "https://www.99fund.com/cgi-bin/fundproduct/EtfStockAction?" + urllib.parse.urlencode({"function": "DownLoad", "fundId": code, "tradingDay": day.strftime("%Y%m%d"), "urlType": "1"})
    body = request_bytes(url)
    if not body.strip():
        raise NoData("99fund empty file")
    return body, url, "XML_OR_PIPE_99FUND"


def parse_pipe_99fund(body: bytes, code: str, requested_date: str, source_url: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    text = body.decode("gb18030", "replace")
    lines = [x.strip("\ufeff\r\n") for x in text.splitlines() if x.strip()]
    master = next((x for x in lines if x.startswith("|01|")), None)
    if not master:
        raise NoData("99fund unrecognized file")
    f = master.split("|")
    def at(i: int) -> str | None:
        return f[i].strip() if i < len(f) and f[i].strip() else None
    actual = norm_date(at(12))
    if actual != requested_date:
        raise NoData(f"returned date {actual}, requested {requested_date}")
    rows = []
    for line in lines:
        if not line.startswith("|") or line.startswith("|01|") or line.startswith("|99|"):
            continue
        p = line.split("|")
        if len(p) < 9 or not re.fullmatch(r"\d+", p[2].strip() or ""):
            continue
        rows.append({
            "component_code": component_code(p[2], "HK"),
            "component_name": clean_text(p[3]),
            "component_market": "HK",
            "quantity_shares": num(p[4]),
            "cash_substitution_flag": canonical_flag(p[5]),
            "cash_substitution_flag_raw": clean_text(p[5]),
            "purchase_cash_premium_pct": pct(p[6]),
            "redemption_cash_discount_pct": pct(p[7]),
            "fixed_substitution_amount_cny": num(p[8]),
        })
    if not rows:
        raise NoData("99fund has no constituents")
    basic = {
        "fund_code": code,
        "symbol": code + ".SH",
        "fund_name": at(7),
        "fund_company": at(8),
        "trade_date": actual,
        "prev_trade_date": norm_date(at(13)),
        "prev_cash_difference_cny": num(at(16)),
        "prev_unit_asset_nav_cny": num(at(14)),
        "prev_nav_cny": num(at(15)),
        "estimated_cash_component_cny": num(at(18)),
        "cash_substitution_limit_pct": pct(at(19)),
        "iopv_published": bool_value(at(22)),
        "creation_redemption_unit_shares": num(at(11)),
        "creation_limit_shares": num(at(20)),
        "redemption_limit_shares": num(at(21)),
        "net_creation_limit_shares": None,
        "net_redemption_limit_shares": None,
        "creation_allowed": None,
        "redemption_allowed": None,
        "redemption_mode": at(23),
        "component_count": len(rows),
        "source_url": source_url,
        "source_format": "PIPE_99FUND",
    }
    return basic, rows


def parse_any_99fund(body: bytes, code: str, requested_date: str, source_url: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    try:
        return parse_xml(body, code, requested_date, source_url, "XML_99FUND")
    except NoData as xml_error:
        try:
            return parse_pipe_99fund(body, code, requested_date, source_url)
        except NoData:
            raise xml_error


def market_guess(raw_code: str) -> str | None:
    s = clean_text(raw_code)
    if s.isdigit() and len(str(int(s))) <= 5:
        return "HK"
    return None


def _gb(body: bytes) -> str:
    try:
        return body.decode("gb18030")
    except UnicodeDecodeError:
        return body.decode("utf-8", "replace")


def fetch_jsfund(code: str, day: dt.date) -> tuple[bytes, str, str]:
    d = day.strftime("%Y%m%d")
    url = f"https://download.jsfund.cn/pcf/{code}/{day.year}/ssepcf_{code}_{d}.xml"
    return request_bytes(url), url, "XML_JSFUND"


def fetch_gt(code: str, day: dt.date) -> tuple[bytes, str, str]:
    url = f"https://m.gtfund.com/cochin/etf/download/{code}/{day.strftime('%Y%m%d')}"
    return request_bytes(url), url, "XML_GT"


def fetch_ph(code: str, day: dt.date) -> tuple[bytes, str, str]:
    d = day.strftime("%Y%m%d")
    url = f"https://www.phfund.com.cn/common/resource/etf/etfupload/{code}/{day.year}/ssepcf_{code}_{d}.xml"
    body = request_bytes(url)
    head = body[:200].lstrip().lower()
    if head.startswith(b"<!doctype") or head.startswith(b"<html"):
        raise NoData("Penghua returned SPA shell for missing file")
    return body, url, "XML_PH"


def fetch_thfund(code: str, day: dt.date) -> tuple[bytes, str, str]:
    d = day.strftime("%Y%m%d")
    url = f"https://thfundweb.oss-cn-beijing.aliyuncs.com/etf/{d}/ssepcf_{code}_{d}.xml"
    return request_bytes(url), url, "XML_THFUND"


def fetch_hft(code: str, day: dt.date) -> tuple[bytes, str, str]:
    d = day.strftime("%Y%m%d")
    url = f"https://www.hftfund.com/upload/applications/funds-struts/pcf{code}/{code}{d}.xml"
    return request_bytes(url), url, "XML_HFT"


def fetch_eastmoney(code: str, day: dt.date) -> tuple[bytes, str, str]:
    url = "https://www.dongcaijijin.com/etf/queryPRInfo"
    form = urllib.parse.urlencode({"fundCode": code, "date": day.strftime("%Y%m%d"), "type": "2"}).encode()
    body = request_bytes(url, form, {"Content-Type": "application/x-www-form-urlencoded"})
    if not body.strip():
        raise NoData("Eastmoney empty response")
    return body, url, "XML_EASTMONEY"


def fetch_ccb(code: str, day: dt.date) -> tuple[bytes, str, str]:
    url = "https://www.ccbfund.cn/website/v1/api/fund/etf?" + urllib.parse.urlencode({"fundCode": code, "date": day.isoformat()})
    return request_bytes(url), url, "JSON_CCB"


def parse_ccb(body: bytes, code: str, requested_date: str, source_url: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    payload = json.loads(body.decode("utf-8-sig"))
    if payload.get("errcode") not in {0, "0"}:
        raise NoData(payload.get("msg") or "CCB error")
    etf = (payload.get("data") or {}).get("etf")
    if not etf:
        raise NoData("CCB no data")
    base = etf.get("baseInfo") or {}
    pre = etf.get("preTrdDayInfo") or {}
    cur = etf.get("curTrdDayInfo") or {}
    actual = norm_date(base.get("trdDt") or cur.get("trdDt"))
    if actual and actual != requested_date:
        raise NoData(f"returned date {actual}, requested {requested_date}")
    stock_rows = etf.get("stockInfoArr") or []
    rows = []
    for x in stock_rows:
        if not isinstance(x, dict):
            continue
        market = market_guess(x.get("stockCode"))
        rows.append({
            "component_code": component_code(x.get("stockCode"), market),
            "component_name": clean_text(x.get("stockName")),
            "component_market": market,
            "quantity_shares": num(x.get("stockNum")),
            "cash_substitution_flag": canonical_flag(x.get("stockFlag")),
            "cash_substitution_flag_raw": clean_text(x.get("stockFlag")),
            "purchase_cash_premium_pct": pct(x.get("stockRatio")),
            "redemption_cash_discount_pct": pct(x.get("redemptionDiscountRatio")),
            "fixed_substitution_amount_cny": num(x.get("stockAmount")),
        })
    if not rows:
        raise NoData("CCB has no constituents")
    switch = clean_text(cur.get("redemption"))
    both = switch in {"1", "Y", "是", "1"}
    basic = {
        "fund_code": code,
        "symbol": code + ".SH",
        "fund_name": None,
        "fund_company": None,
        "trade_date": actual or requested_date,
        "prev_trade_date": norm_date(pre.get("trdDt")),
        "prev_cash_difference_cny": num(pre.get("cashCmp")),
        "prev_unit_asset_nav_cny": num(pre.get("nAVperCU")),
        "prev_nav_cny": num(pre.get("nAV")),
        "estimated_cash_component_cny": num(cur.get("estimateCashCmp")),
        "cash_substitution_limit_pct": pct(cur.get("maxCashRatio")),
        "iopv_published": bool_value(cur.get("publish")),
        "creation_redemption_unit_shares": num(cur.get("creationRedemptionUnit")),
        "creation_limit_shares": num(cur.get("creationLimit")),
        "redemption_limit_shares": num(cur.get("redemptionLimit")),
        "net_creation_limit_shares": None,
        "net_redemption_limit_shares": None,
        "creation_allowed": both,
        "redemption_allowed": both,
        "redemption_mode": None,
        "component_count": len(rows),
        "source_url": source_url,
        "source_format": "JSON_CCB",
    }
    return basic, rows


def fetch_fullgoal(code: str, day: dt.date) -> tuple[bytes, str, str]:
    base = "https://www.fullgoal.com.cn/ws-business-server/fund/"
    ds = day.isoformat()
    day_url = base + "getFundSg?" + urllib.parse.urlencode({"productCode": code, "tradeDate": ds})
    cfg_url = base + "getFundSgShCfg?" + urllib.parse.urlencode({"productCode": code, "tradeDate": ds, "pageNum": 1, "pageSize": 9999, "isPreview": ""})
    day_body = request_bytes(day_url)
    cfg_body = request_bytes(cfg_url)
    return json.dumps({"day": json.loads(day_body.decode("utf-8-sig")), "cfg": json.loads(cfg_body.decode("utf-8-sig"))}, ensure_ascii=False).encode(), day_url, "JSON_FULLGOAL"


def parse_fullgoal(body: bytes, code: str, requested_date: str, source_url: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    payload = json.loads(body.decode("utf-8-sig"))
    data = payload.get("day", {}).get("data")
    if not isinstance(data, dict):
        raise NoData("Fullgoal no day data")
    actual = norm_date(data.get("annDate") or data.get("tradeDate") or data.get("tradingDay"))
    if actual != requested_date:
        raise NoData(f"returned date {actual}, requested {requested_date}")
    cfg_payload = payload.get("cfg", {})
    stock_rows = ((cfg_payload.get("data") or {}) if isinstance(cfg_payload, dict) else {}).get("list") or []
    rows = []
    for x in stock_rows:
        if not isinstance(x, dict):
            continue
        market = market_name(x.get("gpsc"))
        rows.append({
            "component_code": component_code(x.get("stockCode"), market),
            "component_name": clean_text(x.get("stockName")),
            "component_market": market,
            "quantity_shares": num(x.get("num")),
            "cash_substitution_flag": canonical_flag(x.get("cashBs")),
            "cash_substitution_flag_raw": clean_text(x.get("cashBs")),
            "purchase_cash_premium_pct": pct(x.get("sgXjtd")),
            "redemption_cash_discount_pct": pct(x.get("shXjtd")),
            "fixed_substitution_amount_cny": num(x.get("tdAmount")),
        })
    if not rows:
        raise NoData("Fullgoal has no constituents")
    allow = clean_text(data.get("ifSh"))
    basic = {
        "fund_code": code,
        "symbol": code + ".SH",
        "fund_name": data.get("productAbbr"),
        "fund_company": data.get("manageName"),
        "trade_date": actual,
        "prev_trade_date": norm_date(data.get("ptradeDate")),
        "prev_cash_difference_cny": num(data.get("cashBalance")),
        "prev_unit_asset_nav_cny": num(data.get("minShnav")),
        "prev_nav_cny": num(data.get("shareValue")),
        "estimated_cash_component_cny": num(data.get("minYgcash")),
        "cash_substitution_limit_pct": pct(data.get("maxCashtd")),
        "iopv_published": bool_value(data.get("ifIopv")),
        "creation_redemption_unit_shares": num(data.get("minShdy")),
        "creation_limit_shares": num(data.get("maxSg")),
        "redemption_limit_shares": num(data.get("maxSh")),
        "net_creation_limit_shares": num(data.get("netCreationlimit")),
        "net_redemption_limit_shares": num(data.get("netRedemptionLimit")),
        "creation_allowed": None if not allow else ("申购" in allow),
        "redemption_allowed": None if not allow else ("赎回" in allow),
        "redemption_mode": data.get("creationRedemptionMechanism"),
        "component_count": len(rows),
        "source_url": source_url,
        "source_format": "JSON_FULLGOAL",
    }
    return basic, rows


def _fs_sign(params: dict[str, str]) -> str:
    keys = sorted(params.keys(), key=lambda k: [ord(c) for c in k])
    base = "&".join(f"{k}={params[k]}" for k in keys) + "&key=CD364559FDA24D53B05F01E943ECDFCC"
    return hashlib.md5(base.encode()).hexdigest()


def _fs_req(url: str, params: dict[str, str]) -> dict[str, Any]:
    body = json.dumps({**params, "signature": _fs_sign(params)}, ensure_ascii=False, separators=(",", ":")).encode()
    raw = request_bytes(url, body, {"Content-Type": "application/json", "netNo": "web"})
    return json.loads(raw.decode("utf-8-sig"))


def fetch_fsfund(code: str, day: dt.date) -> tuple[bytes, str, str]:
    ts = str(int(time.time() * 1000))
    common = {"fundCode": code, "startDate": day.strftime("%Y%m%d"), "netNo": "web", "timestamp": ts}
    day_url = "https://api.fsfund.com/v2/webzk/queryController/getFundEtfday"
    share_url = "https://api.fsfund.com/v2/webzk/queryController/getFundShareInfo"
    day = _fs_req(day_url, dict(common))
    share = _fs_req(share_url, dict(common))
    return json.dumps({"day": day, "share": share}, ensure_ascii=False).encode(), day_url, "JSON_FSFUND"


def parse_fsfund(body: bytes, code: str, requested_date: str, source_url: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    payload = json.loads(body.decode("utf-8-sig"))
    day_rows = payload.get("day", {}).get("data") or []
    share_rows = payload.get("share", {}).get("data") or []
    if not day_rows or not share_rows:
        raise NoData("FSFund empty data")
    actual = norm_date(share_rows[0].get("tradingDay")) if isinstance(share_rows[0], dict) else None
    if actual and actual != requested_date:
        raise NoData(f"returned date {actual}, requested {requested_date}")
    info = {clean_text(x.get("indexKey")): clean_text(x.get("indexValue")) for x in day_rows if isinstance(x, dict)}
    rows = []
    for x in share_rows:
        if not isinstance(x, dict):
            continue
        market = market_name(x.get("scid")) or market_guess(x.get("stockCode"))
        rows.append({
            "component_code": component_code(x.get("stockCode"), market),
            "component_name": clean_text(x.get("stockShort")),
            "component_market": market,
            "quantity_shares": num(x.get("number")),
            "cash_substitution_flag": canonical_flag(x.get("tdbz")),
            "cash_substitution_flag_raw": clean_text(x.get("tdbz")),
            "purchase_cash_premium_pct": pct(x.get("sgyjbl")),
            "redemption_cash_discount_pct": pct(x.get("shzjbl")),
            "fixed_substitution_amount_cny": num(x.get("tdje")),
        })
    if not rows:
        raise NoData("FSFund has no constituents")
    switch = clean_text(info.get("CreationRedemption"))
    basic = {
        "fund_code": code,
        "symbol": code + ".SH",
        "fund_name": None,
        "fund_company": None,
        "trade_date": actual or requested_date,
        "prev_trade_date": None,
        "prev_cash_difference_cny": num(info.get("CashComponent")),
        "prev_unit_asset_nav_cny": num(info.get("NAVperCU")),
        "prev_nav_cny": num(info.get("NAV")),
        "estimated_cash_component_cny": num(info.get("EstimateCashComponent")),
        "cash_substitution_limit_pct": pct(info.get("MaxCashRatio")),
        "iopv_published": bool_value(info.get("Publish")),
        "creation_redemption_unit_shares": num(info.get("CreationRedemptionUnit")),
        "creation_limit_shares": num(info.get("CreationLimit")),
        "redemption_limit_shares": num(info.get("RedemptionLimit")),
        "net_creation_limit_shares": num(info.get("NetCreationLimit")),
        "net_redemption_limit_shares": num(info.get("NetRedemptionLimit")),
        "creation_allowed": None if not switch else switch in {"1", "2", "Y"},
        "redemption_allowed": None if not switch else switch in {"1", "3", "Y"},
        "redemption_mode": None,
        "component_count": len(rows),
        "source_url": source_url,
        "source_format": "JSON_FSFUND",
    }
    return basic, rows


HFFUND_TOKEN: dict[str, Any] = {"token": None, "at": 0.0}
HFFUND_LOCK = threading.Lock()


def _hffund_token() -> str:
    with HFFUND_LOCK:
        if HFFUND_TOKEN["token"] and time.time() - HFFUND_TOKEN["at"] < 1800:
            return HFFUND_TOKEN["token"]
        url = "https://www.hffund.com/agate/api/v1/common/session"
        payload = json.loads(request_bytes(url).decode("utf-8-sig"))
        rec = payload.get("record") or []
        token = None
        if rec and isinstance(rec[0], list):
            token = rec[0][0].get("token")
        elif rec and isinstance(rec[0], dict):
            token = rec[0].get("token")
        if not token:
            raise RuntimeError("Hffund session token missing")
        HFFUND_TOKEN.update({"token": token, "at": time.time()})
        return token


def fetch_hffund(code: str, day: dt.date) -> tuple[bytes, str, str]:
    url = "https://www.hffund.com/agate/api/v1/etf/web/qry?" + urllib.parse.urlencode({
        "fundcode": code,
        "tradingdaybeg": day.strftime("%Y%m%d"),
        "tradingdayend": day.strftime("%Y%m%d"),
        "_msgid": f"{int(time.time() * 1000000):020d}-{code}",
    })
    body = request_bytes(url, None, {"token": _hffund_token(), "g_systemtype": "w"})
    return body, url, "JSON_HFFUND"


def parse_hffund(body: bytes, code: str, requested_date: str, source_url: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    payload = json.loads(body.decode("utf-8-sig"))
    rec = payload.get("record") or []
    meta = rec[0] if len(rec) > 0 else []
    stock_rows = rec[1] if len(rec) > 1 else []
    if not meta or not stock_rows:
        raise NoData("Hffund empty data")
    actual = norm_date((meta[0] or {}).get("tradingday")) if isinstance(meta[0], dict) else None
    if actual and actual != requested_date:
        raise NoData(f"returned date {actual}, requested {requested_date}")
    info = {}
    for x in meta:
        if isinstance(x, dict):
            info[clean_text(x.get("key"))] = clean_text(x.get("value"))
    rows = []
    for x in stock_rows:
        if not isinstance(x, dict):
            continue
        market = market_guess(x.get("stockcode"))
        rows.append({
            "component_code": component_code(x.get("stockcode"), market),
            "component_name": clean_text(x.get("stockshort")),
            "component_market": market,
            "quantity_shares": num(x.get("number")),
            "cash_substitution_flag": canonical_flag(x.get("tdbz")),
            "cash_substitution_flag_raw": clean_text(x.get("tdbz")),
            "purchase_cash_premium_pct": pct(x.get("yjbl")),
            "redemption_cash_discount_pct": pct(x.get("discountrate")),
            "fixed_substitution_amount_cny": num(x.get("tdje")),
        })
    if not rows:
        raise NoData("Hffund has no constituents")
    switch = clean_text(info.get("CreationRedemption"))
    basic = {
        "fund_code": code,
        "symbol": code + ".SH",
        "fund_name": None,
        "fund_company": None,
        "trade_date": actual or requested_date,
        "prev_trade_date": None,
        "prev_cash_difference_cny": num(info.get("CashComponent")),
        "prev_unit_asset_nav_cny": num(info.get("NAVperCU")),
        "prev_nav_cny": num(info.get("NAV")),
        "estimated_cash_component_cny": num(info.get("EstimateCashComponent")),
        "cash_substitution_limit_pct": pct(info.get("MaxCashRatio")),
        "iopv_published": bool_value(info.get("Publish")),
        "creation_redemption_unit_shares": num(info.get("CreationRedemptionUnit")),
        "creation_limit_shares": None,
        "redemption_limit_shares": None,
        "net_creation_limit_shares": None,
        "net_redemption_limit_shares": None,
        "creation_allowed": None if not switch else switch in {"1", "2"},
        "redemption_allowed": None if not switch else switch in {"1", "3"},
        "redemption_mode": None,
        "component_count": len(rows),
        "source_url": source_url,
        "source_format": "JSON_HFFUND",
    }
    return basic, rows


def fetch_cifm(code: str, day: dt.date) -> tuple[bytes, str, str]:
    key = day.isoformat()
    with CIFM_LOCK:
        body = CIFM_CACHE.get(key)
    if body is None:
        url = f"https://www.cifm.com/fund/ETF/HisXML/index_{day.strftime('%Y%m%d')}.xml"
        body = request_bytes(url)
        with CIFM_LOCK:
            CIFM_CACHE[key] = body
    text = body.decode("utf-8", "replace")
    if re.search(r"<info>\s*</info>", text) or f"fundcode='{code}'" not in text:
        raise NoData("Cifm no data for fund/date")
    return body, f"https://www.cifm.com/fund/ETF/HisXML/index_{day.strftime('%Y%m%d')}.xml", "XML_CIFM"


CIFM_CACHE: dict[str, bytes] = {}
CIFM_LOCK = threading.Lock()


def parse_cifm(body: bytes, code: str, requested_date: str, source_url: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    root = ET.fromstring(body)
    fund = None
    for elem in root.iter():
        if local_tag(elem.tag) == "fundcode" and elem.get("fundcode") == code:
            fund = elem
            break
    if fund is None:
        raise NoData(f"Cifm missing fund {code}")
    fid = clean_text(fund.get("fundid")) or code
    wanted = {code, fid}
    actual = norm_date(fund.get("tradingday"))
    if actual != requested_date:
        raise NoData(f"returned date {actual}, requested {requested_date}")
    fields: dict[str, str] = {}
    seen: set[tuple[str, str]] = set()
    for elem in root.iter():
        if elem.get("fundid") not in wanted:
            continue
        tag = local_tag(elem.tag)
        key = (tag, clean_text(elem.get("value")))
        if key in seen:
            continue
        seen.add(key)
        if tag != "stock" and "value" in elem.attrib:
            fields.setdefault(tag, clean_text(elem.get("value")))
    stock_seen: set[str] = set()
    rows = []
    for elem in root.iter():
        if local_tag(elem.tag) != "stock" or elem.get("fundid") not in wanted:
            continue
        s_code = clean_text(elem.get("stockcode"))
        if not s_code or s_code in stock_seen:
            continue
        stock_seen.add(s_code)
        market = market_name(elem.get("stockcodedesc")) or market_guess(s_code)
        rows.append({
            "component_code": component_code(s_code, market),
            "component_name": clean_text(elem.get("stockshort")),
            "component_market": market,
            "quantity_shares": num(elem.get("num")),
            "cash_substitution_flag": canonical_flag(elem.get("tdbz")),
            "cash_substitution_flag_raw": clean_text(elem.get("tdbz")),
            "purchase_cash_premium_pct": pct(elem.get("yjbl")),
            "redemption_cash_discount_pct": pct(elem.get("zjbl")),
            "fixed_substitution_amount_cny": num(elem.get("tdje")),
        })
    if not rows:
        raise NoData("Cifm has no constituents")
    allow = clean_text(fields.get("CREATIONREDEMPTION"))
    basic = {
        "fund_code": code,
        "symbol": code + ".SH",
        "fund_name": clean_text(fund.get("fundname")),
        "fund_company": clean_text(fund.get("mangementcompany")),
        "trade_date": actual,
        "prev_trade_date": norm_date(fund.get("pretradingday")),
        "prev_cash_difference_cny": num(fields.get("CASHCOMPONENT")),
        "prev_unit_asset_nav_cny": num(fields.get("NAVPERCU")),
        "prev_nav_cny": num(fields.get("NAV")),
        "estimated_cash_component_cny": num(fields.get("ESTIMATECASHCOMPONENT")),
        "cash_substitution_limit_pct": pct(fields.get("MAXCASHRATIO")),
        "iopv_published": bool_value(fields.get("PUBLISH")),
        "creation_redemption_unit_shares": num(fields.get("CREATIONREDEMPTIONUNIT")),
        "creation_limit_shares": num(fields.get("CREATIONLIMIT")),
        "redemption_limit_shares": num(fields.get("REDEMPTIONLIMIT")),
        "net_creation_limit_shares": num(fields.get("NETCREATIONLIMIT")),
        "net_redemption_limit_shares": num(fields.get("NETREDEMPTIONLIMIT")),
        "creation_allowed": None if not allow else ("申购" in allow),
        "redemption_allowed": None if not allow else ("赎回" in allow),
        "redemption_mode": fields.get("CREATIONREDEMPTIONMECHANISM"),
        "component_count": len(rows),
        "source_url": source_url,
        "source_format": "XML_CIFM",
    }
    return basic, rows


def fetch_igw(code: str, day: dt.date) -> tuple[bytes, str, str]:
    ds = day.isoformat()
    xml_url = f"https://www.igwfmc.com/main/etf/pcf/{ds}/{code}_{ds}.xml"
    try:
        return request_bytes(xml_url), xml_url, "XML_IGW"
    except NoData:
        txt_url = f"https://www.igwfmc.com/main/etf/pcf/{ds}/{code}_{ds}.txt"
        body = request_bytes(txt_url)
        return body, txt_url, "TXT_IGW"


def parse_igw_txt(body: bytes, code: str, requested_date: str, source_url: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    text = _gb(body).replace("\ufeff", "")
    header: dict[str, str] = {}
    rows: list[dict[str, Any]] = []
    in_rows = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line == "TAGTAG":
            in_rows = True
            continue
        if not in_rows:
            if "=" in line:
                k, _, v = line.partition("=")
                header[k.strip()] = v.strip()
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 7 or not parts[0].isdigit():
            continue
        flag = clean_text(parts[3])
        canonical = "ALLOWED" if flag == "5" else canonical_flag(flag)
        market = market_guess(parts[0])
        rows.append({
            "component_code": component_code(parts[0], market),
            "component_name": parts[1],
            "component_market": market,
            "quantity_shares": num(parts[2]),
            "cash_substitution_flag": canonical,
            "cash_substitution_flag_raw": flag,
            "purchase_cash_premium_pct": pct(parts[4]),
            "redemption_cash_discount_pct": pct(parts[5]),
            "fixed_substitution_amount_cny": num(parts[6]),
        })
    if not rows:
        raise NoData("IGW txt has no constituents")
    actual = norm_date(header.get("TradingDay"))
    if actual != requested_date:
        raise NoData(f"returned date {actual}, requested {requested_date}")
    switch = clean_text(header.get("CreationRedemption"))
    basic = {
        "fund_code": code,
        "symbol": code + ".SH",
        "fund_name": None,
        "fund_company": None,
        "trade_date": actual,
        "prev_trade_date": norm_date(header.get("PreTradingDay")),
        "prev_cash_difference_cny": num(header.get("CashComponent")),
        "prev_unit_asset_nav_cny": num(header.get("NAVperCU")),
        "prev_nav_cny": num(header.get("NAV")),
        "estimated_cash_component_cny": num(header.get("EstimateCashComponent")),
        "cash_substitution_limit_pct": pct(header.get("MaxCashRatio")),
        "iopv_published": bool_value(header.get("Publish")),
        "creation_redemption_unit_shares": num(header.get("CreationRedemptionUnit")),
        "creation_limit_shares": None,
        "redemption_limit_shares": None,
        "net_creation_limit_shares": None,
        "net_redemption_limit_shares": None,
        "creation_allowed": None if not switch else switch in {"1", "2"},
        "redemption_allowed": None if not switch else switch in {"1", "3"},
        "redemption_mode": None,
        "component_count": len(rows),
        "source_url": source_url,
        "source_format": "TXT_IGW",
    }
    return basic, rows


def parse_igw(body: bytes, code: str, requested_date: str, source_url: str, source_format: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if source_format.startswith("TXT"):
        return parse_igw_txt(body, code, requested_date, source_url)
    return parse_xml(body, code, requested_date, source_url, "XML_IGW")


ADAPTERS: dict[str, dict[str, Any]] = {}


def add_group(company: str, codes: Iterable[str], adapter: str, detail: str) -> None:
    for code in codes:
        ADAPTERS[code] = {"company": company, "adapter": adapter, "official_detail_url": detail.format(code=code)}


add_group("万家基金", ["520700", "520730"], "WANJIA_HTML", "https://www.wjasset.com/etf-web/etf/index?fundcode={code}")
add_group("华安基金", ["513920", "513240", "520940", "520740", "520840", "513900", "513580"], "HUAAN_XML", "https://www.huaan.com.cn/etf/{code}/sgshqd.jsp")
add_group("嘉实基金", ["513830", "520970", "520960", "520670"], "JSFUND_XML", "https://www.jsfund.cn/main/fund/{code}/fundNav.shtml")
add_group("华夏基金", ["513910", "513660", "513810", "526000", "520510", "520910", "513180", "513330", "513190"], "CHINAAMC_JSON", "https://www.chinaamc.com/fund/{code}/index.shtml")
add_group("建信基金", ["520770"], "CCB_JSON", "https://www.ccbfund.cn/#/fund?fundCode={code}")
add_group("汇添富基金", ["526030", "513280", "513820", "513260", "520820", "520980"], "FUND99", "https://www.99fund.com/main/products/pofund/{code}/ETFlist.shtml")
add_group("景顺长城基金", ["520990", "513780", "513980"], "IGW_XML", "https://www.igwfmc.com/main/jjcp/product/{code}/detail.html")
add_group("国泰基金", ["513020", "520930", "520720", "513720"], "GT_XML", "https://e.gtfund.com/etrade/Jijin/view/id/{code}")
add_group("银华基金", ["513620", "520610", "513160"], "UNKNOWN_SKIP", "https://www.yhfund.com.cn/main/fund/funddetail/index.shtml?product_code={code}")
add_group("易方达基金", ["513200", "513210", "510900", "520810", "520850", "513010", "513090", "513040", "513320"], "EFUNDS_XML", "https://www.efunds.com.cn/fund/{code}.shtml")
add_group("广发基金", ["520710", "513750", "513120", "520900", "513380", "520630", "520600"], "GF_HTML", "https://www.gffunds.com.cn/proxy/pcflist/{code}")
add_group("南方基金", ["513600", "520660", "520680", "520570", "520650"], "NANFANG_JSON", "https://www.nffund.com/main/nffund/personal-financing/detail.shtml?fundCode={code}")
add_group("华宝基金", ["520560", "520880", "520780", "513770"], "FSFUND_JSON", "https://www.fsfund.com/fund/{code}/fundDetail.shtml")
add_group("华泰柏瑞基金", ["513150", "513550", "520890", "520500", "513930", "513130", "513140", "513530"], "HTPB_XML", "https://www.huatai-pb.com/products/zhishu/{code}/index.html")
add_group("招商基金", ["513990", "526050", "520550"], "UNKNOWN_SKIP", "https://www.cmfchina.com/web/fundDetail/{code}/index.html")
add_group("东财基金", ["520530"], "EASTMONEY_XML", "https://www.dongcaijijin.com/fund/cpxq?fcode={code}")
add_group("海富通基金", ["513860"], "HFT_XML", "https://www.hftfund.com/funds-struts/etf/etf_sgsh.jsp?fundcode={code}")
add_group("华富基金", ["520750"], "HFFUND_JSON", "https://www.hffund.com/agate/jweb/#/fund?secondtype={code}&etfflag=1")
add_group("博时基金", ["520690", "526070"], "BOSERA_XML", "https://www.bosera.com/fund/etfList.do?fundCode={code}")
add_group("摩根基金", ["520760", "520950", "513630", "513890"], "CIFM_XML", "https://www.cifm.com/fund/{code}")
add_group("鹏华基金", ["513700", "513170"], "PH_XML", "https://www.phfund.com.cn/fund/{code}")
add_group("兴业基金", ["520790"], "UNKNOWN_SKIP", "https://www.cib-fund.com.cn/fund/{code}")
add_group("天弘基金", ["520920"], "THFUND_XML", "https://www.thfund.com.cn/fundinfo/{code}")
add_group("富国基金", ["520860"], "FULLGOAL_JSON", "https://www.fullgoal.com.cn/fundDetail/{code}/index.html")
add_group("兴银基金", ["513560"], "UNKNOWN_SKIP", "https://www.cxfund.com.cn/fund/{code}")


def load_targets() -> list[dict[str, Any]]:
    raw = json.loads(UNIVERSE_PATH.read_text(encoding="utf-8"))
    targets = []
    for item in raw["candidates"]:
        symbol = item.get("symbol", "")
        if not symbol.endswith(".SH"):
            continue
        code = symbol.split(".")[0]
        meta = ADAPTERS.get(code)
        if not meta:
            meta = {"company": "未识别基金公司", "adapter": "UNKNOWN_SKIP", "official_detail_url": ""}
        targets.append({"order": len(targets) + 1, "fund_code": code, "symbol": symbol, "fund_name": item.get("name"), **meta})
    return targets


def target_dir(root: Path, code: str) -> Path:
    p = root / code
    p.mkdir(parents=True, exist_ok=True)
    return p


def append_jsonl_gz(path: Path, obj: dict[str, Any]) -> None:
    with gzip.open(path, "at", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n")


def read_attempts(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    out = {}
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    x = json.loads(line)
                    out[x["requested_date"]] = x
    except (OSError, EOFError, json.JSONDecodeError):
        return out
    return out


def write_metadata(root: Path, target: dict[str, Any], start: dt.date, end: dt.date) -> None:
    p = target_dir(root, target["fund_code"]) / "metadata.json"
    meta = {
        "fund_code": target["fund_code"],
        "symbol": target["symbol"],
        "fund_name": target.get("fund_name"),
        "fund_company": target["company"],
        "adapter": target["adapter"],
        "official_detail_url": target.get("official_detail_url"),
        "requested_window": {"start": start.isoformat(), "end": end.isoformat(), "weekdays_only": True},
        "files": {"basic": "basic.jsonl.gz", "components": "components.jsonl.gz", "attempts": "attempts.jsonl.gz"},
        "normalization": "PCF canonical JSONL schema v1; raw pages are not retained; raw_sha256 identifies the official response.",
    }
    p.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def fetch_for_target(target: dict[str, Any], requested_dates: list[dt.date], root: Path) -> dict[str, Any]:
    code = target["fund_code"]
    d = target_dir(root, code)
    attempt_path = d / "attempts.jsonl.gz"
    basic_path = d / "basic.jsonl.gz"
    comp_path = d / "components.jsonl.gz"
    attempts = read_attempts(attempt_path)
    write_metadata(root, target, requested_dates[0], requested_dates[-1])
    adapter = target["adapter"]
    if adapter == "UNKNOWN_SKIP":
        if "SKIPPED_NO_OFFICIAL_PCF" not in {x.get("status") for x in attempts.values()}:
            url = target.get("official_detail_url") or ""
            status = "SKIPPED_NO_OFFICIAL_PCF"
            msg = "official detail page identified; no historical PCF endpoint adapter implemented or endpoint requires encrypted session"
            try:
                if url:
                    body = request_bytes(url)
                    digest = hashlib.sha256(body).hexdigest()
                    msg += f"; detail_http_bytes={len(body)}"
                else:
                    digest = None
            except Exception as e:
                digest = None
                msg += f"; detail_request={type(e).__name__}: {e}"
            append_jsonl_gz(attempt_path, {"fund_code": code, "requested_date": requested_dates[-1].isoformat(), "status": status, "source_url": url, "actual_date": None, "message": msg, "raw_sha256": digest, "retrieved_at_utc": dt.datetime.now(dt.timezone.utc).isoformat()})
        return summarize_target(target, requested_dates, read_attempts(attempt_path), root)

    fetchers = {
        "HUAAN_XML": (fetch_xml_huaan, parse_xml),
        "HTPB_XML": (fetch_xml_htpb, parse_xml),
        "BOSERA_XML": (fetch_xml_bosera, parse_xml),
        "EFUNDS_XML": (fetch_efunds, parse_xml),
        "GF_HTML": (lambda c, day: (request_bytes(f"https://www.gffunds.com.cn/proxy/pcflist/{c}?date={day.strftime('%Y%m%d')}"), f"https://www.gffunds.com.cn/proxy/pcflist/{c}?date={day.strftime('%Y%m%d')}", "HTML_GF"), parse_gf),
        "WANJIA_HTML": (fetch_wanjia, parse_wanjia),
        "CHINAAMC_JSON": (fetch_chinaamc, parse_chinaamc),
        "NANFANG_JSON": (fetch_nanfang, parse_nanfang),
        "FUND99": (fetch_99fund, parse_any_99fund),
        "JSFUND_XML": (fetch_jsfund, parse_xml),
        "GT_XML": (fetch_gt, parse_xml),
        "PH_XML": (fetch_ph, parse_xml),
        "THFUND_XML": (fetch_thfund, parse_xml),
        "HFT_XML": (fetch_hft, parse_xml),
        "EASTMONEY_XML": (fetch_eastmoney, parse_xml),
        "IGW_XML": (fetch_igw, parse_igw),
        "CIFM_XML": (fetch_cifm, parse_cifm),
        "CCB_JSON": (fetch_ccb, parse_ccb),
        "FULLGOAL_JSON": (fetch_fullgoal, parse_fullgoal),
        "FSFUND_JSON": (fetch_fsfund, parse_fsfund),
        "HFFUND_JSON": (fetch_hffund, parse_hffund),
    }
    fetcher, parser = fetchers[adapter]
    xml_adapters = {"HUAAN_XML", "HTPB_XML", "BOSERA_XML", "EFUNDS_XML", "JSFUND_XML", "GT_XML", "PH_XML", "THFUND_XML", "HFT_XML", "EASTMONEY_XML", "IGW_XML"}
    for i, day in enumerate(requested_dates, 1):
        ds = day.isoformat()
        prior = attempts.get(ds)
        if prior and prior.get("status") in {"SUCCESS", "NO_DATA", "SKIPPED_NO_OFFICIAL_PCF"}:
            continue
        started = time.time()
        retrieved = dt.datetime.now(dt.timezone.utc).isoformat()
        try:
            body, source_url, source_format = fetcher(code, day)
            digest = hashlib.sha256(body).hexdigest()
            if not body.strip():
                raise NoData("empty response")
            if adapter in {"GF_HTML", "WANJIA_HTML"}:
                basic, components = parser(body, code, ds, source_url)
            else:
                basic, components = parser(body, code, ds, source_url, source_format) if adapter in xml_adapters else parser(body, code, ds, source_url)
            basic["fund_name"] = basic.get("fund_name") or target.get("fund_name")
            basic["fund_company"] = basic.get("fund_company") or target.get("company")
            basic.update({"retrieved_at_utc": retrieved, "raw_sha256": digest, "official_file_url": source_url})
            append_jsonl_gz(basic_path, basic)
            for row_no, row in enumerate(components, 1):
                row.update({"fund_code": code, "symbol": code + ".SH", "fund_name": basic.get("fund_name"), "trade_date": basic["trade_date"], "source_url": source_url, "source_format": basic["source_format"], "retrieved_at_utc": retrieved, "raw_sha256": digest, "component_row_number": row_no})
                append_jsonl_gz(comp_path, row)
            record = {"fund_code": code, "requested_date": ds, "status": "SUCCESS", "source_url": source_url, "actual_date": basic.get("trade_date"), "component_count": len(components), "message": None, "raw_sha256": digest, "retrieved_at_utc": retrieved, "elapsed_seconds": round(time.time() - started, 3)}
            append_jsonl_gz(attempt_path, record)
            attempts[ds] = record
        except NoData as e:
            record = {"fund_code": code, "requested_date": ds, "status": "NO_DATA", "source_url": target.get("official_detail_url"), "actual_date": None, "component_count": 0, "message": str(e), "raw_sha256": None, "retrieved_at_utc": retrieved, "elapsed_seconds": round(time.time() - started, 3)}
            append_jsonl_gz(attempt_path, record)
            attempts[ds] = record
        except Exception as e:
            record = {"fund_code": code, "requested_date": ds, "status": "ERROR", "source_url": target.get("official_detail_url"), "actual_date": None, "component_count": 0, "message": f"{type(e).__name__}: {e}", "raw_sha256": None, "retrieved_at_utc": retrieved, "elapsed_seconds": round(time.time() - started, 3)}
            append_jsonl_gz(attempt_path, record)
            attempts[ds] = record
        if i == 1 or i % 40 == 0 or i == len(requested_dates):
            done = sum(1 for x in attempts.values() if x.get("status") in {"SUCCESS", "NO_DATA"})
            log(f"  {target['company']} {code}: {done}/{len(requested_dates)} dates settled")
    return summarize_target(target, requested_dates, read_attempts(attempt_path), root)


def summarize_target(target: dict[str, Any], requested_dates: list[dt.date], attempts: dict[str, dict[str, Any]], root: Path) -> dict[str, Any]:
    statuses = [attempts.get(d.isoformat(), {}).get("status") for d in requested_dates]
    success = statuses.count("SUCCESS")
    missing = statuses.count("NO_DATA")
    errors = statuses.count("ERROR")
    skipped = statuses.count("SKIPPED_NO_OFFICIAL_PCF")
    if target["adapter"] == "UNKNOWN_SKIP":
        status = "SKIPPED_NO_OFFICIAL_PCF"
    elif errors:
        status = "PARTIAL_ERROR"
    elif success:
        status = "COMPLETE"
    else:
        status = "NO_PCFS_IN_WINDOW"
    return {
        "order": target["order"], "fund_code": target["fund_code"], "symbol": target["symbol"],
        "fund_name": target.get("fund_name"), "fund_company": target["company"], "adapter": target["adapter"],
        "official_detail_url": target.get("official_detail_url"), "status": status,
        "requested_start": requested_dates[0].isoformat(), "requested_end": requested_dates[-1].isoformat(),
        "expected_weekdays": len(requested_dates), "success_count": success, "missing_count": missing,
        "error_count": errors, "skipped_count": skipped, "queried_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "notes": "Exact requested dates only; NO_DATA is not backfilled with the latest available PCF.",
    }


LEDGER_FIELDS = ["order", "fund_code", "symbol", "fund_name", "fund_company", "adapter", "official_detail_url", "status", "requested_start", "requested_end", "expected_weekdays", "success_count", "missing_count", "error_count", "skipped_count", "queried_at_utc", "notes"]


def write_ledger(root: Path, rows: list[dict[str, Any]]) -> None:
    p = root / "target_ledger.csv"
    tmp = root / "target_ledger.csv.tmp"
    with tmp.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=LEDGER_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(sorted(rows, key=lambda x: x["order"]))
    tmp.replace(p)


def write_readme(root: Path, start: dt.date, end: dt.date, targets: list[dict[str, Any]]) -> None:
    if (root / "README.md").exists():
        return
    known = sum(t["adapter"] != "UNKNOWN_SKIP" for t in targets)
    text = f"""# 上海市场 ETF PCF 归一化数据\n\n- 采集窗口：{start.isoformat()} 至 {end.isoformat()}，仅请求工作日。\n- 范围：关联任务本地订阅池中的 {len(targets)} 个上海市场标的；顺序与 `universe.json` 一致。\n- 官方适配器：{known} 个标的已接入可复用的基金公司官网接口。\n- 数据文件：每个基金代码目录下的 `basic.jsonl.gz`（每日一条）和 `components.jsonl.gz`（每个成分股一条）。\n- 台账：`target_ledger.csv`；每日请求结果在各目录的 `attempts.jsonl.gz`。\n- 续跑：成功和明确无数据日期会跳过；错误会在下一次重试；不以最近日期填补缺失日期。\n- 原始网页不落盘；每条成功记录保留 `official_file_url`、`source_format` 和 `raw_sha256`。\n\n字段 schema v1：基础信息统一为基金代码、交易日、T-1 现金差额/单位资产净值/份额净值、T 日预估现金、现金替代比例上限、IOPV、申赎单位及限额；成分信息统一为代码、名称、市场、数量、现金替代标志、申购溢价、赎回折价、固定替代金额。\n"""
    (root / "README.md").write_text(text, encoding="utf-8")


def write_link_exports(root: Path, targets: list[dict[str, Any]]) -> None:
    """Export one company-level and one target-level official-link index."""
    source_patterns = {
        "HUAAN_XML": "https://www.huaan.com.cn/etf/{code}/etffiledownload.jsp?etffilename=etfd_{code}_{YYYYMMDD}.xml",
        "HTPB_XML": "https://www.huatai-pb.com/etf-web/etf/download?filePath=etfd_{code}_{YYYYMMDD}.xml",
        "BOSERA_XML": "https://www.bosera.com/jjcp/etf/files/{code}/{YYYY}/ssepcf_{code}_{YYYYMMDD}.xml",
        "EFUNDS_XML": "https://api.efunds.com.cn/xcowch/front/etffund/downfile?fundCode={code}&tDate=YYYY-MM-DD&listType=ON_SITE",
        "GF_HTML": "https://www.gffunds.com.cn/proxy/pcflist/{code}?date={YYYYMMDD}",
        "CHINAAMC_JSON": "POST https://accountquery.chinaamc.com/front/front/out/etf/tradeList (fundCode, queryDate)",
        "NANFANG_JSON": "POST https://www.southernfund.com/nfwebApi/trade/subAndRedempList (fundCode, queryDate)",
        "FUND99": "https://www.99fund.com/cgi-bin/fundproduct/EtfStockAction?function=DownLoad&fundId={code}&tradingDay={YYYYMMDD}&urlType=1",
        "WANJIA_HTML": "https://www.wjasset.com/etf-web/etf/v2?fundcode={code}&beginDate={YYYY-MM-DD}",
        "JSFUND_XML": "https://download.jsfund.cn/pcf/{code}/{YYYY}/ssepcf_{code}_{YYYYMMDD}.xml",
        "GT_XML": "https://m.gtfund.com/cochin/etf/download/{code}/{YYYYMMDD}",
        "PH_XML": "https://www.phfund.com.cn/common/resource/etf/etfupload/{code}/{YYYY}/ssepcf_{code}_{YYYYMMDD}.xml",
        "THFUND_XML": "https://thfundweb.oss-cn-beijing.aliyuncs.com/etf/{YYYYMMDD}/ssepcf_{code}_{YYYYMMDD}.xml",
        "HFT_XML": "https://www.hftfund.com/upload/applications/funds-struts/pcf{code}/{code}{YYYYMMDD}.xml",
        "EASTMONEY_XML": "POST https://www.dongcaijijin.com/etf/queryPRInfo (fundCode, date, type=2)",
        "IGW_XML": "https://www.igwfmc.com/main/etf/pcf/{YYYY-MM-DD}/{code}_{YYYY-MM-DD}.xml (legacy <=2025-11-07: .txt)",
        "CIFM_XML": "https://www.cifm.com/fund/ETF/HisXML/index_{YYYYMMDD}.xml",
        "CCB_JSON": "GET https://www.ccbfund.cn/website/v1/api/fund/etf?fundCode={code}&date={YYYY-MM-DD}",
        "FULLGOAL_JSON": "GET https://www.fullgoal.com.cn/ws-business-server/fund/getFundSgShCfg (productCode, tradeDate)",
        "FSFUND_JSON": "POST https://api.fsfund.com/v2/webzk/queryController/getFundEtfday (MD5-signed)",
        "HFFUND_JSON": "GET https://www.hffund.com/agate/api/v1/etf/web/qry (fundcode, tradingdaybeg/end)",
        "UNKNOWN_SKIP": "待后续核查官网历史 PCF 接口",
    }
    groups: dict[str, list[dict[str, Any]]] = {}
    for target in targets:
        groups.setdefault(target["company"], []).append(target)
    company_path = root / "fund_company_links.csv"
    with company_path.open("w", newline="", encoding="utf-8-sig") as f:
        fields = ["company_order", "fund_company", "official_detail_url", "pcf_source_url_pattern", "adapter", "target_count", "fund_codes", "fund_names"]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for order, (company, items) in enumerate(groups.items(), 1):
            w.writerow({
                "company_order": order,
                "fund_company": company,
                "official_detail_url": items[0].get("official_detail_url"),
                "pcf_source_url_pattern": source_patterns.get(items[0].get("adapter"), ""),
                "adapter": items[0].get("adapter"),
                "target_count": len(items),
                "fund_codes": ",".join(x["fund_code"] for x in items),
                "fund_names": " | ".join(x.get("fund_name") or "" for x in items),
            })
    target_path = root / "target_official_links.csv"
    with target_path.open("w", newline="", encoding="utf-8-sig") as f:
        fields = ["order", "fund_code", "symbol", "fund_name", "fund_company", "official_detail_url", "pcf_source_url_pattern", "adapter"]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for target in targets:
            w.writerow({key: (source_patterns.get(target.get("adapter"), "") if key == "pcf_source_url_pattern" else target.get(key)) for key in fields})


def write_handoff(root: Path, start: dt.date, end: dt.date, all_targets: list[dict[str, Any]], ledger: dict[str, dict[str, Any]]) -> None:
    counts: dict[str, int] = {}
    for row in ledger.values():
        counts[row.get("status", "PENDING")] = counts.get(row.get("status", "PENDING"), 0) + 1
    pending = [row["fund_code"] for row in sorted(ledger.values(), key=lambda x: int(x.get("order", 999999))) if row.get("status") not in {"COMPLETE", "SKIPPED_NO_OFFICIAL_PCF"}]
    skipped = [row["fund_code"] for row in sorted(ledger.values(), key=lambda x: int(x.get("order", 999999))) if row.get("status") == "SKIPPED_NO_OFFICIAL_PCF"]
    research_start = skipped[0] if skipped else (pending[0] if pending else "无")
    text = f"""# PCF 采集交接说明

## 当前范围与状态

- 来源任务：`codex://threads/01a08633-aa30-7c43-ac82-4dd8777b9bd0`
- 范围：关联任务本地订阅池中的上海市场标的，共 {len(all_targets)} 个；顺序以 `target_ledger.csv` 的 `order` 为准。
- 请求窗口：{start.isoformat()} 至 {end.isoformat()}；262 个工作日请求日期。
- 当前台账状态：{json.dumps(counts, ensure_ascii=False, sort_keys=True)}
- 首个待处理代码：`{pending[0] if pending else "无"}`
- 首个需二次核查/尝试新增官网适配器的代码：`{research_start}`

## 先读这两个文件

1. `fund_company_links.csv`：按首次出现顺序列出每家基金公司的官网入口、适配器和旗下代码。
2. `target_ledger.csv`：逐标的断点台账；`COMPLETE` 和 `SKIPPED_NO_OFFICIAL_PCF` 可跳过，`PARTIAL_ERROR` 需要重试，其他状态从第一个开始。

`target_official_links.csv` 是逐标的官网入口索引，适合按代码直接定位。

## 续跑方式

在工作区执行：

```bash
python3 /Users/ellis/工具程序开发/pcf_shanghai_collector.py --workers 4
```

采集器会读取每个代码目录的 `attempts.jsonl.gz`：成功和明确无数据日期跳过，错误日期重试；不以最近日期填补缺失日期。若只重试某几个代码，使用 `--only-code 520600 --only-code 513920`，当前版本会保留完整台账；若确需重采某代码，使用 `--refresh-code CODE`，旧目录会可恢复地移入 `_revisions/`。

## 已接入的官网适配器

当前已接入可复用适配器的标的状态是 COMPLETE，主要包括华安、华夏、汇添富、易方达、广发、南方、华泰柏瑞、博时。适配器分别处理官方 XML、JSON、HTML 和汇添富历史管线文本，并统一输出：

- `CODE/basic.jsonl.gz`：每日一条归一化基础 PCF；
- `CODE/components.jsonl.gz`：每日成分股明细；
- `CODE/attempts.jsonl.gz`：每个请求日期的 SUCCESS / NO_DATA / ERROR；
- `CODE/metadata.json`：字段和官方入口说明。

百分比统一为“百分点”（例如 15.0 表示 15%）；成分股代码按市场归一化为 HK 五位、沪深六位；每条成功记录保留 `official_file_url`、`source_format`、`raw_sha256`，不保留大体积原始网页。

## 已跳过项目

以下代码已经查询/记录为 `SKIPPED_NO_OFFICIAL_PCF`，原因是目前只确认到官网详情/列表页，未确认可公开回溯的历史 PCF 接口，或接口需要未实现的加密会话：

`{", ".join(skipped) if skipped else "无"}`

如后续 agent 找到某家公司的历史 PCF 官方接口，应先在采集器的 `ADAPTERS` 中补充该公司适配器，再运行 `--refresh-code` 重采对应代码；不要把第三方镜像或集思录数据当作官方 PCF 写入。
"""
    (root / "HANDOFF.md").write_text(text, encoding="utf-8")


def rewrite_jsonl_gz(path: Path, rows: list[dict[str, Any]]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    tmp.replace(path)


def repair_existing_records(root: Path, targets: list[dict[str, Any]]) -> None:
    """Repair deterministic normalization issues without issuing new requests."""
    by_code = {x["fund_code"]: x for x in targets}
    hk_formats = {"JSON_CHINAAMC", "PIPE_99FUND", "JSON_NANFANG", "HTML_GF", "XML_BOSERA", "HTML_WANJIA", "JSON_FULLGOAL"}
    for code, target in by_code.items():
        d = root / code
        basic_path = d / "basic.jsonl.gz"
        if basic_path.exists():
            basics = []
            with gzip.open(basic_path, "rt", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    row = json.loads(line)
                    row["fund_name"] = row.get("fund_name") or target.get("fund_name")
                    row["fund_company"] = row.get("fund_company") or target.get("company")
                    basics.append(row)
            rewrite_jsonl_gz(basic_path, basics)
        comp_path = d / "components.jsonl.gz"
        if comp_path.exists():
            comps = []
            with gzip.open(comp_path, "rt", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    row = json.loads(line)
                    if row.get("cash_substitution_flag_raw") not in (None, ""):
                        row["cash_substitution_flag"] = canonical_flag(row["cash_substitution_flag_raw"])
                    if not row.get("component_market") and row.get("source_format") in hk_formats:
                        row["component_market"] = "HK"
                    if row.get("component_market"):
                        row["component_code"] = component_code(row.get("component_code"), row["component_market"])
                    comps.append(row)
            rewrite_jsonl_gz(comp_path, comps)


def refresh_codes(root: Path, codes: set[str]) -> None:
    """Move prior generated files aside so selected targets can be re-fetched."""
    if not codes:
        return
    stamp = dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    revision_root = root / "_revisions" / stamp
    for code in sorted(codes):
        src_dir = root / code
        if not src_dir.exists():
            continue
        dst = revision_root / code
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src_dir), str(dst))
        log(f"moved previous generated files for {code} to recoverable revision {dst}")


def main() -> int:
    args = parse_args()
    start = dt.date.fromisoformat(args.start)
    end = dt.date.fromisoformat(args.end)
    requested_dates = [end] if args.probe else dates_between(start, end)
    if not requested_dates:
        raise SystemExit("no requested dates")
    root = args.root
    root.mkdir(parents=True, exist_ok=True)
    all_targets = load_targets()
    write_link_exports(root, all_targets)
    targets = all_targets
    if args.only_code:
        wanted = {x.zfill(6) for x in args.only_code}
        targets = [x for x in targets if x["fund_code"] in wanted]
    refresh = {x.zfill(6) for x in args.refresh_code}
    refresh_codes(root, refresh)
    write_readme(root, start, end, all_targets)
    existing: dict[str, dict[str, Any]] = {}
    if (root / "target_ledger.csv").exists():
        with (root / "target_ledger.csv").open(encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                existing[row["fund_code"]] = row
    # Group by first appearance, preserving the page/universe order.
    groups: dict[str, list[dict[str, Any]]] = {}
    for t in targets:
        groups.setdefault(t["company"], []).append(t)
    ledger = {}
    for t in all_targets:
        row = dict(existing.get(t["fund_code"], {}))
        row.update({"order": t["order"], "fund_code": t["fund_code"], "symbol": t["symbol"], "fund_name": t.get("fund_name"), "fund_company": t["company"], "adapter": t["adapter"], "official_detail_url": t.get("official_detail_url")})
        if t["fund_code"] in refresh:
            row["status"] = "PENDING"
        row.setdefault("status", "PENDING")
        ledger[t["fund_code"]] = row
    repair_existing_records(root, all_targets)
    write_ledger(root, list(ledger.values()))
    log(f"PCF collector: {len(targets)} Shanghai targets, {len(groups)} fund companies, {len(requested_dates)} requested dates")
    for company, group in groups.items():
        pending = [t for t in group if ledger[t["fund_code"]].get("status") not in {"COMPLETE", "NO_PCFS_IN_WINDOW", "SKIPPED_NO_OFFICIAL_PCF"}]
        if not pending:
            log(f"[{company}] already settled; skip")
            continue
        log(f"[{company}] start {len(pending)} targets ({', '.join(t['fund_code'] for t in pending)})")
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            future_map = {pool.submit(fetch_for_target, t, requested_dates, root): t for t in pending}
            for fut in as_completed(future_map):
                t = future_map[fut]
                try:
                    result = fut.result()
                except Exception as e:
                    result = {"order": t["order"], "fund_code": t["fund_code"], "symbol": t["symbol"], "fund_name": t.get("fund_name"), "fund_company": t["company"], "adapter": t["adapter"], "official_detail_url": t.get("official_detail_url"), "status": "PARTIAL_ERROR", "error_count": 1, "notes": f"worker failure: {type(e).__name__}: {e}"}
                ledger[t["fund_code"]] = result
                write_ledger(root, list(ledger.values()))
                log(f"[{company}] {t['fund_code']} -> {result.get('status')} success={result.get('success_count', 0)} missing={result.get('missing_count', 0)} errors={result.get('error_count', 0)}")
    write_ledger(root, list(ledger.values()))
    write_handoff(root, start, end, all_targets, ledger)
    complete = sum(ledger[x["fund_code"]].get("status") == "COMPLETE" for x in all_targets)
    skipped = sum(ledger[x["fund_code"]].get("status") == "SKIPPED_NO_OFFICIAL_PCF" for x in all_targets)
    errors = sum(ledger[x["fund_code"]].get("status") == "PARTIAL_ERROR" for x in all_targets)
    log(f"Finished: complete={complete}, skipped={skipped}, partial_error={errors}; ledger={root / 'target_ledger.csv'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
