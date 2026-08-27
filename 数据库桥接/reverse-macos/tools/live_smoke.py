#!/usr/bin/env python3
"""Minimal credential-safe live login check for the macOS backend."""
from __future__ import annotations

import argparse
import collections
import configparser
import getpass
import statistics
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src_reconstructed" / "python"))

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
    cli.add_argument("--data-type", type=int, default=int(tgw.SubscribeDataType.kAfterHourFixedPriceSnapshot))
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
        help="run one reconstructed daily K-line query and print shape only",
    )
    cli.add_argument(
        "--snapshot",
        metavar="CODE",
        help="run one reconstructed L1 snapshot query and print shape only",
    )
    cli.add_argument("--date", type=int, default=20260825)
    cli.add_argument("--begin-time", type=int, default=93000000)
    cli.add_argument("--end-time", type=int, default=93030000)
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
                    request.cyc_type = 10008
                    request.cyc_def = 0
                    request.auto_complete = 1
                    request.begin_date = 20260825
                    request.end_date = 20260825
                    request.begin_time = 0
                    request.end_time = 0
                    rows, error = tgw.QueryKline(request, return_df_format=False)
                    columns = sorted(rows[0]) if rows else []
                    print(f"kline_query_error={error} rows={len(rows)} columns={columns}")
                if args.snapshot:
                    request = tgw.ReqDefault().set_code(args.snapshot)
                    request.market_type = args.market
                    request.date = args.date
                    request.begin_time = args.begin_time
                    request.end_time = args.end_time
                    rows, error = tgw.QuerySnapshot(request, return_df_format=False)
                    columns = sorted(rows[0]) if rows else []
                    print(f"snapshot_query_error={error} rows={len(rows)} columns={columns}")
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
                            event = backend.client.recv_event(
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
                    event = backend.client.recv_event(timeout=args.wait)
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
