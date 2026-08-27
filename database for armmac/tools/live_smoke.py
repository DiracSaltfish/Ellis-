#!/usr/bin/env python3
"""Minimal credential-safe live login check for the macOS backend."""
from __future__ import annotations

import argparse
import collections
import configparser
import getpass
import statistics
import sys
import threading
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "python"))

import tgw_macos as tgw  # noqa: E402


def _strip_inline(value: str) -> str:
    return value.split("#", 1)[0].strip()


def load_config(path: Path) -> tuple[list[str], int, str, str, int]:
    parser = configparser.ConfigParser()
    with path.open(encoding="utf-8") as stream:
        parser.read_file(stream)
    section = parser["galaxy"]
    raw_hosts = _strip_inline(section["host"])
    hosts = [item.strip() for item in raw_hosts.replace("，", " ").split() if item.strip()]
    mode_name = _strip_inline(section.get("api_mode", "kInternetMode"))
    mode = int(getattr(tgw.ApiMode, mode_name))
    return hosts, section.getint("port"), _strip_inline(section["username"]), section["password"].strip(), mode


def main() -> int:
    cli = argparse.ArgumentParser()
    cli.add_argument("--config", type=Path, default=ROOT / "config" / "galaxy_account.ini")
    cli.add_argument(
        "--username-stdin",
        action="store_true",
        help="read a one-run username override from stdin without persisting it",
    )
    cli.add_argument("--force-logout", action="store_true")
    cli.add_argument("--subscribe", metavar="CODE")
    cli.add_argument("--market", type=int, default=int(tgw.MarketType.kSSE))
    cli.add_argument("--data-type", type=int, default=int(tgw.SubscribeDataType.kSnapshot))
    cli.add_argument("--category", type=int, default=0)
    cli.add_argument("--wait", type=float, default=15.0)
    cli.add_argument(
        "--duration",
        type=float,
        default=0.0,
        help="observe push events for this many seconds and print counters only",
    )
    cli.add_argument(
        "--calendar",
        action="store_true",
        help="run the reconstructed ThirdInfo calendar query and print shape only",
    )
    cli.add_argument(
        "--kline",
        metavar="CODE",
        help="run one reconstructed K-line query and print shape only",
    )
    cli.add_argument(
        "--cyc-type",
        type=int,
        default=10008,
        help="public MDDatatype cycle for --kline (10000 1-minute, 10008 daily, "
             "10009 weekly, 10010 monthly, 10011 seasonal, 10012 yearly)",
    )
    cli.add_argument("--begin-date", type=int, default=20260825)
    cli.add_argument("--end-date", type=int, default=20260825)
    cli.add_argument(
        "--kline-begin-time",
        type=int,
        default=0,
        help="K-line HHmm start time; use 900 for the verified 1-minute sample",
    )
    cli.add_argument(
        "--kline-end-time",
        type=int,
        default=0,
        help="K-line HHmm end time; use 1500 for the verified 1-minute sample",
    )
    cli.add_argument(
        "--snapshot",
        metavar="CODE",
        help="run one reconstructed L1 snapshot query and print shape only",
    )
    cli.add_argument(
        "--snapshot-async",
        action="store_true",
        help="with --snapshot: use the async query_spi contract and print "
             "callback counters only",
    )
    cli.add_argument("--date", type=int, default=20260825)
    cli.add_argument("--begin-time", type=int, default=93000000)
    cli.add_argument("--end-time", type=int, default=93030000)
    cli.add_argument(
        "--etf-info",
        metavar="CODE",
        help="run one reconstructed ETF info query and print shape only",
    )
    cli.add_argument(
        "--securities-info",
        metavar="CODE",
        help="run one reconstructed securities-info query and print shape only",
    )
    cli.add_argument(
        "--ex-factor",
        metavar="CODE",
        help="run one reconstructed ex-factor table query and print shape only",
    )
    cli.add_argument(
        "--code-table",
        action="store_true",
        help="run one reconstructed full-market code table query and print "
             "desensitized shape only",
    )
    args = cli.parse_args()
    hosts, port, username, password, mode = load_config(args.config)
    if args.username_stdin:
        username = (
            getpass.getpass("one-run username: ")
            if sys.stdin.isatty()
            else sys.stdin.readline().strip()
        )
        if not username:
            cli.error("--username-stdin requires a non-empty line on stdin")
    for index, host in enumerate(hosts):
        cfg = tgw.Cfg().set(
            server_vip=host,
            server_port=port,
            username=username,
            password=password,
            force_logout=args.force_logout,
        )
        ok = tgw.Login(cfg, mode)
        backend = tgw.interface._backend()
        tag = None
        if isinstance(backend.logon_response, dict):
            headers = backend.logon_response.get("headers")
            tag = headers.get("tag") if isinstance(headers, dict) else None
        print(
            f"endpoint[{index}] transport_state={backend.state} "
            f"authenticated={ok} response_tag={tag or '<none>'}"
        )
        if ok:
            try:
                if args.calendar:
                    task_id = tgw.GetTaskID()
                    tgw.SetThirdInfoParam(task_id, "function_id", "A010061003")
                    tgw.SetThirdInfoParam(task_id, "start_date", "20260801")
                    tgw.SetThirdInfoParam(task_id, "end_date", "20260826")
                    tgw.SetThirdInfoParam(task_id, "market", "SSE")
                    rows, error = tgw.QueryThirdInfo(task_id, return_df_format=False)
                    columns = sorted(rows[0]) if rows else []
                    print(
                        f"calendar_query_error={error} rows={len(rows)} columns={columns}"
                    )
                if args.kline:
                    request = tgw.ReqKline().set_code(args.kline)
                    request.market_type = args.market
                    request.cq_flag = 0
                    request.cq_date = 0
                    request.qj_flag = 0
                    request.cyc_type = args.cyc_type
                    request.cyc_def = 0
                    request.auto_complete = 1
                    request.begin_date = args.begin_date
                    request.end_date = args.end_date
                    request.begin_time = args.kline_begin_time
                    request.end_time = args.kline_end_time
                    rows, error = tgw.QueryKline(request, return_df_format=False)
                    if rows:
                        first = rows[0]
                        print(
                            f"kline_query_error={error} rows={len(rows)} "
                            f"columns={sorted(first)} "
                            f"column_types={{{', '.join(
                                f'{key!r}: {type(first[key]).__name__!r}'
                                for key in sorted(first)
                            )}}}"
                        )
                    else:
                        print(f"kline_query_error={error} rows=0 columns=[]")
                if args.snapshot:
                    request = tgw.ReqDefault().set_code(args.snapshot)
                    request.market_type = args.market
                    request.date = args.date
                    request.begin_time = args.begin_time
                    request.end_time = args.end_time
                    if args.snapshot_async:
                        deliveries = []
                        done = threading.Event()

                        class Collector:
                            def __call__(self, result, err_code):
                                if isinstance(err_code, int):
                                    shown_err = err_code
                                elif err_code is None:
                                    shown_err = None
                                else:
                                    shown_err = f"{type(err_code).__name__}:{str(err_code)[:160]}"
                                deliveries.append(
                                    (
                                        type(result).__name__ if result is not None else None,
                                        len(result) if isinstance(result, list) else None,
                                        shown_err,
                                    )
                                )
                                done.set()

                        started = time.monotonic()
                        submitted, submit_err = tgw.QuerySnapshot(
                            request, query_spi=Collector(), return_df_format=False
                        )
                        done.wait(timeout=20.0)
                        print(
                            f"snapshot_async_submit={submitted!r} "
                            f"submit_error={submit_err!r} "
                            f"callbacks={deliveries} "
                            f"elapsed_sec={round(time.monotonic() - started, 3)}"
                        )
                    else:
                        rows, error = tgw.QuerySnapshot(request, return_df_format=False)
                        columns = sorted(rows[0]) if rows else []
                        print(
                            f"snapshot_query_error={error} rows={len(rows) if rows else 0} "
                            f"columns={columns}"
                        )
                if args.etf_info:
                    item = tgw.SubCodeTableItem().set_code(args.etf_info)
                    item.market = args.market
                    pairs, error = tgw.QueryETFInfo(item, return_df_format=False)
                    columns = sorted(pairs[0][0]) if pairs else []
                    cons_counts = [len(constituents) for _, constituents in pairs]
                    cons_columns = (
                        sorted(pairs[0][1][0]) if pairs and pairs[0][1] else []
                    )
                    print(
                        f"etf_query_error={error} records={len(pairs)} "
                        f"basic_columns={columns} constituent_counts={cons_counts} "
                        f"constituent_columns={cons_columns}"
                    )
                if args.securities_info:
                    item = tgw.SubCodeTableItem().set_code(args.securities_info)
                    item.market = args.market
                    rows, error = tgw.QuerySecuritiesInfo(
                        item, return_df_format=False
                    )
                    columns = sorted(rows[0]) if rows else []
                    column_types = {}
                    markets = set()
                    varieties = set()
                    if rows:
                        column_types = {
                            key: type(rows[0][key]).__name__ for key in columns
                        }
                        markets = {row.get("market_type") for row in rows}
                        varieties = {
                            row.get("variety_category") for row in rows
                        }
                    print(
                        f"securities_info_query_error={error} rows={len(rows)} "
                        f"columns={columns} "
                        f"column_types={{{', '.join(
                            f'{key!r}: {column_types[key]!r}' for key in columns
                        )}}} "
                        f"distinct_market_types={sorted(m for m in markets if isinstance(m, int))} "
                        f"distinct_variety_categories={sorted(v for v in varieties if isinstance(v, int))}"
                    )
                if args.ex_factor:
                    rows, error = tgw.QueryExFactorTable(
                        args.ex_factor, return_df_format=False
                    )
                    columns = sorted(rows[0]) if rows else []
                    column_types = {}
                    ex_dates = set()
                    monotonic = False
                    if rows:
                        column_types = {
                            key: type(rows[0][key]).__name__ for key in columns
                        }
                        ex_dates = {
                            len(str(row.get("ex_date"))) for row in rows
                            if isinstance(row.get("ex_date"), int)
                        }
                        cum_factors = [
                            row.get("cum_factor") for row in rows
                            if isinstance(row.get("cum_factor"), float)
                        ]
                        monotonic = all(
                            left <= right
                            for left, right in zip(cum_factors, cum_factors[1:])
                        )
                    print(
                        f"ex_factor_query_error={error} rows={len(rows)} "
                        f"columns={columns} "
                        f"column_types={{{', '.join(
                            f'{key!r}: {column_types[key]!r}' for key in columns
                        )}}} "
                        f"ex_date_digit_lengths={sorted(ex_dates)} "
                        f"cum_factor_monotonic={monotonic}"
                    )
                if args.code_table:
                    rows, error = tgw.QueryCodeTable(return_df_format=False)
                    columns = sorted(rows[0]) if rows else []
                    column_types = {}
                    markets = set()
                    stypes = set()
                    currencies = set()
                    code_lens = collections.Counter()
                    dup_codes = 0
                    if rows:
                        column_types = {
                            key: type(rows[0][key]).__name__ for key in columns
                        }
                        codes = []
                        for row in rows:
                            markets.add(row.get("market_type"))
                            stypes.add(row.get("security_type"))
                            currencies.add(row.get("currency"))
                            code_lens[len(str(row.get("security_code", "")))] += 1
                            codes.append(row.get("security_code"))
                        dup_codes = len(codes) - len(set(codes))
                    print(
                        f"code_table_query_error={error} rows={len(rows)} "
                        f"columns={columns} "
                        f"column_types={{{', '.join(
                            f'{key!r}: {column_types[key]!r}' for key in columns
                        )}}} "
                        f"distinct_market_types={sorted(m for m in markets if isinstance(m, int))} "
                        f"distinct_security_type_count={len(stypes)} "
                        f"distinct_currency_count={len(currencies)} "
                        f"code_length_histogram={dict(sorted(code_lens.items()))} "
                        f"duplicate_code_rows={dup_codes}"
                    )
            except Exception as exc:
                print(f"query_failed={type(exc).__name__}: {exc}")
                tgw.Close()
                return 4
            if args.subscribe:
                item = tgw.SubscribeItem()
                item.market = args.market
                item.flag = args.data_type
                item.security_code = args.subscribe.encode("utf-8")
                item.category_type = args.category
                subscribe_result = tgw.Subscribe(item)
                print(f"subscribe_result={subscribe_result}")
                if subscribe_result != 0:
                    tgw.Close()
                    return 2
                if args.duration > 0:
                    started = time.monotonic()
                    deadline = started + args.duration
                    timestamps = []
                    tags = collections.Counter()
                    deltas = collections.Counter()
                    key_sets = collections.Counter()
                    while time.monotonic() < deadline:
                        remaining = deadline - time.monotonic()
                        try:
                            event = tgw.ReceiveRawEvent(
                                timeout=min(args.wait, max(0.01, remaining))
                            )
                        except Exception:
                            continue
                        received_at = time.monotonic()
                        if not isinstance(event, dict):
                            continue
                        timestamps.append(received_at)
                        headers = event.get("headers")
                        tag = headers.get("tag") if isinstance(headers, dict) else None
                        tags[str(tag or "<none>")] += 1
                        deltas[str(event.get("is_delta", "<none>"))] += 1
                        data = event.get("data")
                        if isinstance(data, dict):
                            key_sets[",".join(sorted(data))] += 1
                    intervals = [
                        right - left for left, right in zip(timestamps, timestamps[1:])
                    ]
                    print(
                        "push_observation="
                        f"duration_sec:{args.duration},messages:{len(timestamps)},"
                        f"tags:{dict(tags)},delta_flags:{dict(deltas)},"
                        f"median_gap_sec:{round(statistics.median(intervals), 3) if intervals else None},"
                        f"max_gap_sec:{round(max(intervals), 3) if intervals else None},"
                        f"data_key_sets:{dict(key_sets)}"
                    )
                    tgw.UnSubscribe(item)
                    tgw.Close()
                    return 0 if timestamps else 3
                try:
                    event = tgw.ReceiveRawEvent(timeout=args.wait)
                    if isinstance(event, dict):
                        headers = event.get("headers")
                        event_tag = headers.get("tag") if isinstance(headers, dict) else None
                        data = event.get("data")
                        keys = sorted(data) if isinstance(data, dict) else []
                        print(
                            f"push_received=True tag={event_tag or '<none>'} "
                            f"status={event.get('status')!r} data_keys={keys}"
                        )
                    else:
                        print(f"push_received=True type={type(event).__name__}")
                except Exception as exc:
                    print(f"push_received=False error={type(exc).__name__}: {exc}")
                    tgw.Close()
                    return 3
                tgw.UnSubscribe(item)
            tgw.Close()
            return 0
        tgw.Close()
        # The module singleton cannot be reused after a failed/closed attempt.
        tgw.interface._g_backend = None
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
