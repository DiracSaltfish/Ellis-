from __future__ import annotations

import argparse

import tgw_macos as tgw

from common import login_from_config


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/galaxy_account.ini")
    args = parser.parse_args()
    if not login_from_config(args.config):
        print("login failed")
        return 1
    try:
        task_id = tgw.GetTaskID()
        for key, value in {
            "function_id": "A010061003",
            "start_date": "20260801",
            "end_date": "20260826",
            "market": "SSE",
        }.items():
            tgw.SetThirdInfoParam(task_id, key, value)
        calendar, error = tgw.QueryThirdInfo(task_id, return_df_format=False)
        print("calendar", "error=", error, "rows=", len(calendar))

        request = tgw.ReqKline().set_code("510300")
        request.market_type = tgw.MarketType.kSSE
        request.cq_flag = 0
        request.cyc_type = 10008
        request.begin_date = 20260825
        request.end_date = 20260825
        kline, error = tgw.QueryKline(request, return_df_format=False)
        print("daily kline", "error=", error, "rows=", len(kline))
    finally:
        tgw.Close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
