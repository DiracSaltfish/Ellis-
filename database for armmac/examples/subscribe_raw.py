from __future__ import annotations

import argparse
import time

import tgw_macos as tgw

from common import login_from_config


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/galaxy_account.ini")
    parser.add_argument("--kind", choices=("etf", "hkt"), default="etf")
    parser.add_argument("--duration", type=float, default=30.0)
    args = parser.parse_args()

    if args.kind == "etf":
        market, flag, code = (
            tgw.MarketType.kSZSE,
            tgw.SubscribeDataType.kSnapshot,
            "159518",
        )
    else:
        market, flag, code = (
            tgw.MarketType.kSSE,
            tgw.SubscribeDataType.kHKTSnapshot,
            "02800",
        )

    if not login_from_config(args.config):
        print("login failed")
        return 1
    item = tgw.SubscribeItem().set_code(code)
    item.market = market
    item.flag = flag
    item.category_type = 0
    state: dict[str, object] | None = None
    received = 0
    try:
        if tgw.Subscribe(item) != 0:
            print("subscribe failed")
            return 2
        deadline = time.monotonic() + max(0.0, args.duration)
        while time.monotonic() < deadline:
            try:
                event = tgw.ReceiveRawEvent(
                    timeout=min(5.0, max(0.01, deadline - time.monotonic()))
                )
            except TimeoutError:
                continue
            if not isinstance(event, dict) or not isinstance(event.get("data"), dict):
                continue
            is_delta = bool(event.get("is_delta"))
            if not is_delta:
                state = dict(event["data"])
            elif state is not None:
                state.update(event["data"])
            received += 1
            headers = event.get("headers")
            tag = headers.get("tag") if isinstance(headers, dict) else None
            print(
                f"event={received} tag={tag} delta={int(is_delta)} "
                f"fields={len(event['data'])} merged_fields={len(state or {})}"
            )
    finally:
        tgw.UnSubscribe(item)
        tgw.Close()
    return 0 if received else 3


if __name__ == "__main__":
    raise SystemExit(main())
