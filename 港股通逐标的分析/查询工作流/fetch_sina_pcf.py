#!/usr/bin/env python3
"""抓取新浪的 ETF 申赎摘要/成份股镜像，作为临时交叉校验数据。

重要：新浪不是基金管理人或深交所的原始发布渠道，输出必须标记为 B/C 级线索，
并在正式批量结果中优先替换为基金公司或深交所 PCF 原文件。
"""

from __future__ import annotations

import argparse
import csv
import html
import re
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
TEMP_ROOT = ROOT / "临时数据"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X) AppleWebKit/537.36 Chrome/131 Safari/537.36"


class RowParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows: list[list[str]] = []
        self.row: list[str] | None = None
        self.cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag == "tr":
            # 新浪页面的历史表格偶尔缺少 </tr>，下一行 <tr> 会直接嵌套进当前行。
            # 先收口当前行，避免丢掉日期和 PCF 数据。
            if self.row:
                self.rows.append(self.row)
            self.row = []
            self.cell = None
        elif tag in {"td", "th"} and self.row is not None:
            self.cell = []

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in {"td", "th"} and self.cell is not None and self.row is not None:
            value = re.sub(r"\s+", " ", html.unescape("".join(self.cell))).strip()
            self.row.append(value)
            self.cell = None
        elif tag == "tr" and self.row is not None:
            if self.row:
                self.rows.append(self.row)
            self.row = None

    def close(self):
        super().close()
        # 兼容页面末尾缺少 </tr> 的情况。
        if self.row:
            self.rows.append(self.row)
            self.row = None


def fetch(url: str) -> tuple[str, bytes]:
    req = Request(url, headers={"User-Agent": UA})
    with urlopen(req, timeout=30) as response:
        raw = response.read()
    return raw.decode("gbk", errors="replace"), raw


def date_rows(rows: list[list[str]], date_index: int = 1) -> tuple[str, list[list[str]]]:
    candidates = [r for r in rows if len(r) > date_index and re.fullmatch(r"\d{4}-\d{2}-\d{2}", r[date_index] or "")]
    if not candidates:
        return "", []
    latest = max(r[date_index] for r in candidates)
    return latest, [r for r in candidates if r[date_index] == latest]


def save_csv(path: Path, rows: list[list[str]]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        csv.writer(f).writerows(rows)


def run(code: str) -> None:
    out_dir = TEMP_ROOT / code
    out_dir.mkdir(parents=True, exist_ok=True)
    urls = {
        "basic": f"https://money.finance.sina.com.cn/fund/go.php/vAkFundInfo_JJSGSHJBXX/q/{code}.phtml",
        "components": f"https://money.finance.sina.com.cn/fund/go.php/vAkFundInfo_JJSGSHCFGXX/q/{code}.phtml",
    }
    for kind, url in urls.items():
        text, raw = fetch(url)
        stamp = datetime.now().date().strftime("%Y%m%d")
        (out_dir / f"{code}_pcf_{kind}_{stamp}.html").write_bytes(raw)
        parser = RowParser()
        parser.feed(text)
        latest, rows = date_rows(parser.rows, 1)
        save_csv(out_dir / f"{code}_pcf_{latest}_{kind}.csv", rows)
        print(f"{kind}: {latest}, rows={len(rows)}, source={url}")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--codes", nargs="+")
    p.add_argument("--codes-file", type=Path, help="每行一个基金代码，或 CSV 首列为基金代码；支持 .SZ/.SH 后缀")
    args = p.parse_args()
    raw_codes = list(args.codes or [])
    if args.codes_file:
        import csv

        with args.codes_file.open(encoding="utf-8-sig", newline="") as f:
            raw_codes.extend(row[0].strip() for row in csv.reader(f) if row and row[0].strip())
    codes = []
    seen = set()
    for raw_code in raw_codes:
        code = re.sub(r"\D", "", raw_code)
        if len(code) != 6 or code in seen:
            continue
        seen.add(code)
        codes.append(code)
    if not codes:
        p.error("请提供 --codes 或 --codes-file")
    for code in codes:
        run(code)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
