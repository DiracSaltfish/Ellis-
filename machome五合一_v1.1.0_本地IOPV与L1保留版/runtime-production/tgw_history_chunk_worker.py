#!/usr/bin/env python3
"""bj-only worker: fetch one raw TGW minute-history chunk into gzip JSONL."""
from __future__ import annotations

import gzip
import json
import os
import sys
import time

import AmazingData as ad
import tgw


def main() -> None:
    request, output = json.loads(sys.argv[1]), sys.argv[2]
    result = {"schema": "tgw-native-history-chunk.v1", "begin": request["begin"], "end": request["end"], "symbols": request["symbols"], "errors": {}}
    market = {"SH": tgw.MarketType.kSSE, "SZ": tgw.MarketType.kSZSE, "HK": tgw.MarketType.kHKEx}
    with gzip.open(output, "wt", encoding="utf-8", compresslevel=6) as handle:
        handle.write(json.dumps({"type": "header", **result}, ensure_ascii=False, separators=(",", ":")) + "\n")
        try:
            ad.login(username=os.environ["AMAZINGDATA_USERNAME"], password=os.environ["AMAZINGDATA_PASSWORD"], host="101.230.159.234", port=8600)
            for symbol in request["symbols"]:
                try:
                    code, suffix = symbol.split(".")
                    query = tgw.ReqKline(); query.security_code = code; query.market_type = market[suffix]
                    query.cq_flag = query.cq_date = query.qj_flag = query.cyc_def = 0; query.cyc_type = 10000; query.auto_complete = 1
                    query.begin_date = request["begin"]; query.end_date = request["end"]; query.begin_time = 930; query.end_time = 1600
                    rows, error = tgw.QueryKline(query, return_df_format=False)
                    if error or rows is None:
                        result["errors"][symbol] = "query_error"; rows = []
                except Exception as exc:
                    result["errors"][symbol] = type(exc).__name__; rows = []
                handle.write(json.dumps({"type": "symbol", "symbol": symbol, "rows": rows}, ensure_ascii=False, separators=(",", ":")) + "\n")
        finally:
            try: ad.logout(os.environ.get("AMAZINGDATA_USERNAME", ""))
            except Exception: pass
        result["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        handle.write(json.dumps({"type": "footer", **result}, ensure_ascii=False, separators=(",", ":")) + "\n")


if __name__ == "__main__":
    main()
