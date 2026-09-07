#!/usr/bin/env python3
"""Parameterised official PCF fetch/parse for ETF basket research.

The parser stores requested and returned dates separately so a generic
issuer page returning the latest available PCF cannot silently become a
historical observation.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import html
import json
import re
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
PCF_ROOT = ROOT / "data/raw/pcf_html"
OUT_ROOT = ROOT / "data/raw/new_period_520600"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def clean(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", value))).strip()


def number(value: str):
    value = clean(value).replace(",", "").replace("%", "")
    if value in {"", "-", "—", "None"}:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def requested_weekdays(start: str, end: str):
    d0 = date.fromisoformat(start)
    d1 = date.fromisoformat(end)
    out = []
    d = d0
    while d <= d1:
        if d.weekday() < 5:
            out.append(d.strftime("%Y%m%d"))
        d += timedelta(days=1)
    return out


def parse_pcf(path: Path, fund_id: str) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace")
    date_match = re.search(r'name="date"[^>]+value="(\d{8})"', text)
    if not date_match:
        raise ValueError(f"returned PCF date missing: {path}")
    actual_date = date_match.group(1)

    def label_value(label: str) -> str:
        m = re.search(rf"<th>{re.escape(label)}.*?</th>\s*<td>(.*?)</td>", text, re.S)
        return clean(m.group(1)) if m else ""

    header = {
        "公告日期": f"{actual_date[:4]}-{actual_date[4:6]}-{actual_date[6:]}",
        "基金代码": fund_id.split(".")[0],
        "基金名称": label_value("基金名称：") or "",
        "预估现金差额元": label_value("最小申购、赎回单位的预估现金部分(单位:元)："),
        "最小申购赎回单位份": label_value("最小申购、赎回单位(单位:份)："),
        "现金替代比例上限百分比": label_value("现金替代比例上限："),
        "是否需要公告IOPV": label_value("是否需要公布IOPV："),
        "申购赎回模式": label_value("申购赎回模式："),
    }
    marker = text.find("<!-- 成分证券列表")
    body = text[marker:] if marker >= 0 else text
    rows = re.findall(r"<tr>\s*((?:<td>.*?</td>\s*){8})</tr>", body, re.S)
    components = []
    for block in rows:
        cells = [clean(x) for x in re.findall(r"<td>(.*?)</td>", block, re.S)]
        if len(cells) != 8 or not re.fullmatch(r"\d{5}", cells[0]):
            continue
        components.append({
            "记录ID": "",
            "公告日期": header["公告日期"],
            "基金代码": fund_id.split(".")[0],
            "基金名称": header["基金名称"],
            "成分股代码": cells[0],
            "成分股名称": cells[1],
            "数量股": number(cells[2]),
            "现金替代标志": cells[3],
            "现金替代比例百分比": number(cells[4]),
            "固定替代金额元": number(cells[6]),
        })
    if not components:
        raise ValueError(f"no component rows parsed: {path}")
    return {
        "requested_date": path.stem.split("requested_")[-1],
        "date": actual_date,
        "fund_id": fund_id,
        "header": header,
        "components": components,
        "source_path": str(path.relative_to(ROOT.parent.parent)),
        "source_sha256": sha256(path),
        "parsed_at_utc": datetime.now(timezone.utc).isoformat(),
    }


def fetch_page(fund_id: str, requested: str) -> dict:
    code = fund_id.split(".")[0]
    url = f"https://www.gffunds.com.cn/proxy/pcflist/{code}?date={requested}"
    out_dir = PCF_ROOT / code
    out_dir.mkdir(parents=True, exist_ok=True)
    requested_path = out_dir / f"requested_{requested}.html"
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    started = datetime.now(timezone.utc).isoformat()
    try:
        with urlopen(req, timeout=30) as response:
            body = response.read()
        requested_path.write_bytes(body)
        parsed = parse_pcf(requested_path, fund_id)
        actual = parsed["date"]
        status = "SUCCESS" if actual == requested else "PARTIAL"
        canonical = out_dir / f"{actual}.html"
        if actual == requested:
            canonical.write_bytes(body)
        return {
            "requested_date": requested,
            "actual_date": actual,
            "status": status,
            "error_code": None,
            "error_summary": None if status == "SUCCESS" else "issuer page returned another date",
            "returned_rows": len(parsed["components"]),
            "raw_path": str(requested_path.relative_to(ROOT.parent.parent)),
            "canonical_path": str(canonical.relative_to(ROOT.parent.parent)) if actual == requested else None,
            "sha256": sha256(requested_path),
            "url": url,
            "attempted_at_utc": started,
            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:
        return {
            "requested_date": requested,
            "actual_date": None,
            "status": "OTHER_ERROR",
            "error_code": type(exc).__name__,
            "error_summary": str(exc),
            "returned_rows": None,
            "raw_path": str(requested_path.relative_to(ROOT.parent.parent)) if requested_path.exists() else None,
            "canonical_path": None,
            "sha256": sha256(requested_path) if requested_path.exists() else "",
            "url": url,
            "attempted_at_utc": started,
            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--fund-id", default="520600.SH")
    p.add_argument("--start", default="2026-08-04")
    p.add_argument("--end", default="2026-09-04")
    p.add_argument("--fetch", action="store_true")
    args = p.parse_args()
    code = args.fund_id.split(".")[0]
    dates = requested_weekdays(args.start, args.end)
    attempts = [fetch_page(args.fund_id, d) for d in dates] if args.fetch else []
    pages = []
    for path in sorted((PCF_ROOT / code).glob("*.html")):
        if path.name.startswith("requested_"):
            continue
        try:
            parsed = parse_pcf(path, args.fund_id)
            if args.start.replace("-", "") <= parsed["date"] <= args.end.replace("-", ""):
                pages.append(parsed)
        except Exception as exc:
            attempts.append({"requested_date": path.stem, "status": "OTHER_ERROR", "error_code": type(exc).__name__, "error_summary": str(exc), "raw_path": str(path.relative_to(ROOT.parent.parent))})
    pages = {x["date"]: x for x in pages}
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    with gzip.open(OUT_ROOT / f"{code}_pcf_parsed.jsonl.gz", "wt", encoding="utf-8") as f:
        for d in sorted(pages):
            f.write(json.dumps(pages[d], ensure_ascii=False, separators=(",", ":")) + "\n")
    with (OUT_ROOT / f"{code}_pcf_fetch_attempts.json").open("w", encoding="utf-8") as f:
        json.dump(attempts, f, ensure_ascii=False, indent=2)
    if attempts:
        with (OUT_ROOT / f"{code}_pcf_fetch_attempts.csv").open("w", encoding="utf-8-sig", newline="") as f:
            fields = sorted({k for x in attempts for k in x})
            w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(attempts)
    summary = {
        "fund_id": args.fund_id,
        "requested_weekdays": dates,
        "requested_count": len(dates),
        "exact_pages": sorted(pages),
        "exact_page_count": len(pages),
        "missing_exact_dates": [d for d in dates if d not in pages],
        "rows_per_page": {d: len(pages[d]["components"]) for d in sorted(pages)},
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    (OUT_ROOT / f"{code}_pcf_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
