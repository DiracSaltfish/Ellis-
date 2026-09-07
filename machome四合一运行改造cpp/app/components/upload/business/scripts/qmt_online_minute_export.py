#coding:gbk
#
# QMT embedded Python script for exporting local/online CN 1m bars over TCP.
#
# ThinkTrader embedded Python is single-threaded. Do not use threading or
# blocking socket loops here. The TCP server below is non-blocking and is
# serviced by QMT's run_time callback, with handlebar as a fallback driver.
#
# Request example:
#   {"symbols":["501300.SH"],"trade_date":"20260602","download":true}
# Response example:
#   {"ok":true,"rows":[...],"count":242}

import errno
import json
import socket
import traceback
from datetime import datetime, timedelta


HOST = "0.0.0.0"
PORT = 9999
VERSION = "20260602-1946-diagnostic"
PERIOD = "1m"
SESSION_START = "093000"
SESSION_END = "150000"

# Bar fields documented for 1m K lines. bid/ask fields are tick/quote fields,
# not normal historical 1m bar fields.
QMT_FIELDS = ["time", "open", "high", "low", "close", "volume", "amount"]

_context = None
_server_socket = None
_server_started = False
_timer_started = False
_clients = []
_last_heartbeat = None


def _log(message):
    text = "[qmt-online] {} {}".format(datetime.now().strftime("%H:%M:%S"), message)
    try:
        print(text, flush=True)
    except TypeError:
        print(text)


def _error_text(exc):
    text = str(exc)
    if text:
        return "{}: {}".format(exc.__class__.__name__, text)
    return "{}: {!r}".format(exc.__class__.__name__, exc)


def _log_exception(prefix, exc):
    _log("{} {}".format(prefix, _error_text(exc)))
    try:
        detail = traceback.format_exc()
        for line in detail.strip().splitlines():
            _log("{} {}".format(prefix, line))
    except Exception:
        pass


def _is_would_block(exc):
    err = getattr(exc, "errno", None)
    winerr = getattr(exc, "winerror", None)
    return err in (errno.EAGAIN, errno.EWOULDBLOCK) or winerr == 10035


def _set_nonblocking(sock):
    try:
        sock.setblocking(False)
    except Exception:
        sock.settimeout(0)


def _close_socket(sock):
    try:
        sock.close()
    except Exception:
        pass


def _normalize_date(value):
    text = str(value or "").strip()
    if not text:
        return datetime.now().date()
    for pattern in ("%Y%m%d", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            pass
    raise ValueError("invalid date: {}".format(value))


def _normalize_symbol(symbol):
    text = str(symbol or "").strip()
    if not text:
        return ""
    upper = text.upper()
    parts = upper.split(".")
    if len(parts) == 2 and len(parts[0]) == 6:
        if parts[1] in ("SH", "SZ"):
            return parts[0] + "." + parts[1]
        if parts[1] in ("XSHG", "SSE"):
            return parts[0] + ".SH"
        if parts[1] in ("XSHE", "SZSE"):
            return parts[0] + ".SZ"

    # SH501300 / SZ000001
    if len(upper) == 8 and upper[:2] in ("SH", "SZ") and upper[2:].isdigit():
        return upper[2:] + "." + upper[:2]

    # 501300SH / 000001SZ
    if len(upper) == 8 and upper[:6].isdigit() and upper[6:] in ("SH", "SZ"):
        return upper[:6] + "." + upper[6:]

    # 501300 -> SH by CN fund/ETF convention; other six-digit codes default SZ.
    if len(upper) == 6 and upper.isdigit():
        if upper.startswith("5"):
            return upper + ".SH"
        return upper + ".SZ"
    return upper


def _request_symbols(payload):
    raw = payload.get("symbols")
    if raw is None:
        raw = payload.get("symbol")
    if isinstance(raw, str):
        raw = [item for item in raw.replace("\uff0c", ",").split(",") if item.strip()]
    if not isinstance(raw, list):
        raise ValueError("symbols is required")
    symbols = []
    seen = set()
    for item in raw:
        symbol = _normalize_symbol(item)
        if symbol and symbol not in seen:
            seen.add(symbol)
            symbols.append(symbol)
    if not symbols:
        raise ValueError("symbols is required")
    return symbols


def _normalize_timestamp(value):
    if isinstance(value, datetime):
        return value
    text = str(value or "").strip()
    if text.isdigit():
        if len(text) == 14:
            return datetime.strptime(text, "%Y%m%d%H%M%S")
        if len(text) >= 13:
            return datetime.fromtimestamp(int(text[:13]) / 1000.0)
        if len(text) == 12:
            return datetime.strptime(text, "%Y%m%d%H%M")
        if len(text) == 8:
            return datetime.strptime(text + "093000", "%Y%m%d%H%M%S")
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y%m%d%H%M%S"):
        try:
            return datetime.strptime(text, pattern)
        except ValueError:
            pass
    raise ValueError("invalid timestamp: {}".format(value))


def _in_session(ts):
    compact = ts.strftime("%H%M%S")
    return compact >= SESSION_START and compact <= SESSION_END


def _safe_list_value(values, index):
    if index < len(values):
        return values[index]
    return ""


def _frame_column_values(frame, *keys):
    columns = getattr(frame, "columns", [])
    for key in keys:
        if key and key in columns:
            try:
                return list(frame[key])
            except Exception:
                pass
    return []


def _rows_from_dataframe(symbol, frame, source):
    rows = []
    if frame is None or getattr(frame, "empty", False):
        return rows
    index_list = list(frame.index)
    time_list = _frame_column_values(frame, "time", "stime", "timetag")
    open_list = _frame_column_values(frame, "open")
    high_list = _frame_column_values(frame, "high")
    low_list = _frame_column_values(frame, "low")
    close_list = _frame_column_values(frame, "close")
    volume_list = _frame_column_values(frame, "volume")
    amount_list = _frame_column_values(frame, "amount")

    for index, raw_index_ts in enumerate(index_list):
        close_value = _safe_list_value(close_list, index)
        if close_value in (None, ""):
            continue
        raw_ts = _safe_list_value(time_list, index) or raw_index_ts
        ts = _normalize_timestamp(raw_ts)
        if not _in_session(ts):
            continue
        rows.append(
            {
                "trade_date": ts.date().isoformat(),
                "symbol": symbol,
                "minute": ts.strftime("%Y-%m-%d %H:%M"),
                "timestamp": ts.replace(second=0, microsecond=0).isoformat(),
                "last_price": close_value,
                "open": _safe_list_value(open_list, index),
                "high": _safe_list_value(high_list, index),
                "low": _safe_list_value(low_list, index),
                "close": close_value,
                "volume": _safe_list_value(volume_list, index),
                "amount": _safe_list_value(amount_list, index),
                "source": source,
            }
        )
    return rows


def _rows_from_field_dict(symbol, field_dict, source):
    rows = []
    time_list = list(field_dict.get("time") or field_dict.get("timetag") or field_dict.get("stime") or [])
    open_list = list(field_dict.get("open") or [])
    high_list = list(field_dict.get("high") or [])
    low_list = list(field_dict.get("low") or [])
    close_list = list(field_dict.get("close") or [])
    volume_list = list(field_dict.get("volume") or [])
    amount_list = list(field_dict.get("amount") or [])
    total = max(len(close_list), len(time_list))

    for index in range(total):
        if index >= len(close_list):
            continue
        close_value = close_list[index]
        if close_value in (None, ""):
            continue
        ts = _normalize_timestamp(time_list[index] if index < len(time_list) else "")
        if not _in_session(ts):
            continue
        rows.append(
            {
                "trade_date": ts.date().isoformat(),
                "symbol": symbol,
                "minute": ts.strftime("%Y-%m-%d %H:%M"),
                "timestamp": ts.replace(second=0, microsecond=0).isoformat(),
                "last_price": close_value,
                "open": _safe_list_value(open_list, index),
                "high": _safe_list_value(high_list, index),
                "low": _safe_list_value(low_list, index),
                "close": close_value,
                "volume": _safe_list_value(volume_list, index),
                "amount": _safe_list_value(amount_list, index),
                "source": source,
            }
        )
    return rows


def _rows_from_symbol_payload(symbol, payload, source):
    if isinstance(payload, dict):
        return _rows_from_field_dict(symbol, payload, source)
    return _rows_from_dataframe(symbol, payload, source)


def _rows_from_payload(payload, symbols, source):
    rows = []
    if not isinstance(payload, dict):
        _log("market payload type={} is not dict".format(type(payload).__name__))
        return rows
    _log("market payload keys={} symbols={}".format(",".join([str(key) for key in payload.keys()][:8]), ",".join(symbols)))
    for symbol in symbols:
        if symbol in payload:
            rows.extend(_rows_from_symbol_payload(symbol, payload.get(symbol), source))
    if not rows and "close" in payload and len(symbols) == 1:
        rows.extend(_rows_from_field_dict(symbols[0], payload, source))
    rows.sort(key=lambda item: (item["symbol"], item["minute"]))
    return rows


def _download_history(C, symbols, start_time, end_time):
    for symbol in symbols:
        ok = False
        try:
            download_history_data(symbol, PERIOD, start_time, end_time)
            ok = True
        except Exception as exc:
            _log("download_history_data global failed {}: {}".format(symbol, exc))
        if not ok and C is not None and hasattr(C, "download_history_data"):
            try:
                C.download_history_data(symbol, PERIOD, start_time, end_time)
                ok = True
            except Exception as exc:
                _log("C.download_history_data failed {}: {}".format(symbol, exc))


def _fetch_rows(C, symbols, trade_date, download):
    start_time = "{}{}".format(trade_date.strftime("%Y%m%d"), SESSION_START)
    end_time = "{}{}".format(trade_date.strftime("%Y%m%d"), SESSION_END)
    _log("fetch start symbols={} period={} start={} end={} download={}".format(",".join(symbols), PERIOD, start_time, end_time, download))
    if download:
        _download_history(C, symbols, start_time, end_time)
    payload = _call_get_market_data_ex(C, symbols, start_time, end_time)
    _log("fetch payload type={}".format(type(payload).__name__))
    return _rows_from_payload(payload, symbols, "qmt_online_1m")


def _call_get_market_data_ex(C, symbols, start_time, end_time):
    attempts = [
        (
            "kwargs-with-subscribe",
            lambda: C.get_market_data_ex(
                QMT_FIELDS,
                symbols,
                period=PERIOD,
                start_time=start_time,
                end_time=end_time,
                count=-1,
                dividend_type="none",
                fill_data=False,
                subscribe=False,
            ),
        ),
        (
            "kwargs-no-subscribe",
            lambda: C.get_market_data_ex(
                QMT_FIELDS,
                symbols,
                period=PERIOD,
                start_time=start_time,
                end_time=end_time,
                count=-1,
                dividend_type="none",
                fill_data=False,
            ),
        ),
        (
            "positional-no-subscribe",
            lambda: C.get_market_data_ex(QMT_FIELDS, symbols, PERIOD, start_time, end_time, -1, "none", False),
        ),
        (
            "positional-with-subscribe",
            lambda: C.get_market_data_ex(QMT_FIELDS, symbols, PERIOD, start_time, end_time, -1, "none", False, False),
        ),
    ]
    last_exc = None
    for name, func in attempts:
        try:
            _log("get_market_data_ex attempt={}".format(name))
            payload = func()
            _log("get_market_data_ex attempt={} ok type={}".format(name, type(payload).__name__))
            return payload
        except Exception as exc:
            last_exc = exc
            _log_exception("get_market_data_ex attempt={} failed:".format(name), exc)
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("get_market_data_ex attempts exhausted")


def _handle_payload(payload):
    if payload.get("ping"):
        return {"ok": True, "type": "pong", "version": VERSION, "server_time": datetime.now().isoformat()}

    if payload.get("inspect"):
        symbols = []
        try:
            symbols = _request_symbols(payload)
        except Exception:
            pass
        return {
            "ok": True,
            "type": "inspect",
            "version": VERSION,
            "period": PERIOD,
            "session_start": SESSION_START,
            "session_end": SESSION_END,
            "fields": QMT_FIELDS,
            "symbols": symbols,
            "server_time": datetime.now().isoformat(),
        }

    symbols = _request_symbols(payload)
    start_day = _normalize_date(payload.get("start_date") or payload.get("trade_date") or payload.get("date"))
    end_day = _normalize_date(payload.get("end_date") or payload.get("trade_date") or payload.get("date") or start_day)
    if start_day > end_day:
        raise ValueError("start_date must be <= end_date")
    days = (end_day - start_day).days + 1
    if days > 5:
        raise ValueError("date range limit is 5 days")

    download = payload.get("download")
    if download is None:
        download = True

    C = _context
    if C is None:
        raise RuntimeError("QMT context is not ready")

    rows = []
    current = start_day
    while current <= end_day:
        if current.weekday() < 5:
            rows.extend(_fetch_rows(C, symbols, current, bool(download)))
        current = current + timedelta(days=1)

    return {
        "ok": True,
        "symbols": symbols,
        "start_date": start_day.isoformat(),
        "end_date": end_day.isoformat(),
        "session": "09:30-15:00",
        "count": len(rows),
        "rows": rows,
    }


def _json_default(value):
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            pass
    return str(value)


def _json_line_bytes(payload):
    line = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=_json_default) + "\n"
    return line.encode("utf-8")


def _new_client(conn, addr):
    _set_nonblocking(conn)
    client = {"conn": conn, "addr": addr, "buffer": b"", "out": b"", "close": False}
    _clients.append(client)
    _log("accepted {}".format(addr))


def _accept_ready_clients():
    sock = _server_socket
    if sock is None:
        return
    while True:
        try:
            conn, addr = sock.accept()
        except socket.error as exc:
            if _is_would_block(exc):
                break
            _log("accept failed: {}".format(exc))
            break
        _new_client(conn, addr)


def _queue_response(client, response):
    client["out"] = client.get("out", b"") + _json_line_bytes(response)


def _read_client(client, max_requests):
    conn = client["conn"]
    addr = client["addr"]
    processed = 0
    while processed < max_requests:
        try:
            chunk = conn.recv(65536)
        except socket.error as exc:
            if _is_would_block(exc):
                break
            _log("client {} recv failed: {}".format(addr, exc))
            client["close"] = True
            break
        if not chunk:
            client["close"] = True
            break

        client["buffer"] = client.get("buffer", b"") + chunk
        while b"\n" in client["buffer"] and processed < max_requests:
            line, client["buffer"] = client["buffer"].split(b"\n", 1)
            raw = line.decode("utf-8", "replace").strip()
            if not raw:
                continue
            try:
                payload = json.loads(raw)
                _log("request {} keys={}".format(addr, ",".join(sorted(payload.keys()))))
                response = _handle_payload(payload)
            except Exception as exc:
                response = {"ok": False, "error": _error_text(exc)}
                _log_exception("request {} failed:".format(addr), exc)
            _queue_response(client, response)
            _log("response {} ok={} count={}".format(addr, response.get("ok"), response.get("count", "")))
            processed += 1
    return processed


def _flush_client(client):
    out = client.get("out", b"")
    if not out:
        return
    try:
        sent = client["conn"].send(out[:65536])
    except socket.error as exc:
        if _is_would_block(exc):
            return
        _log("client {} send failed: {}".format(client["addr"], exc))
        client["close"] = True
        return
    client["out"] = out[sent:]


def _drop_closed_clients():
    global _clients
    alive = []
    for client in _clients:
        if client.get("close") and not client.get("out"):
            _close_socket(client["conn"])
        else:
            alive.append(client)
    _clients = alive


def _service_once(C):
    global _context, _last_heartbeat
    _context = C
    if not _server_started:
        start_server(C)

    now = datetime.now()
    if _last_heartbeat is None or (now - _last_heartbeat).total_seconds() >= 30:
        _last_heartbeat = now
        _log("service alive clients={}".format(len(_clients)))

    _accept_ready_clients()

    budget = 4
    for client in list(_clients):
        if budget <= 0:
            break
        budget -= _read_client(client, budget)
        _flush_client(client)
    _drop_closed_clients()


def start_server(C):
    global _context, _server_socket, _server_started
    _context = C
    if _server_started and _server_socket is not None:
        return

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((HOST, int(PORT)))
    sock.listen(16)
    _set_nonblocking(sock)
    _server_socket = sock
    _server_started = True
    _log("version {} listening on {}:{}".format(VERSION, HOST, PORT))


def _start_timer(C):
    global _timer_started
    if _timer_started:
        return
    try:
        C.run_time("qmt_online_service", "500nMilliSecond", "2019-10-14 00:00:00")
        _timer_started = True
        _log("run_time service scheduled period=500nMilliSecond")
    except Exception as exc:
        _log("run_time 500nMilliSecond failed: {}".format(exc))
        try:
            C.run_time("qmt_online_service", "1nSecond", "2019-10-14 00:00:00")
            _timer_started = True
            _log("run_time service scheduled period=1nSecond")
        except Exception as exc2:
            _log("run_time service schedule failed: {}".format(exc2))


def init(C):
    global _context
    _context = C
    _start_timer(C)


def after_init(C):
    start_server(C)
    _start_timer(C)
    qmt_online_service(C)


def qmt_online_service(C):
    _service_once(C)


def handlebar(C):
    # Fallback driver for environments where run_time is unavailable.
    _service_once(C)


def stop(C):
    global _server_socket, _server_started, _clients
    for client in _clients:
        _close_socket(client["conn"])
    _clients = []
    if _server_socket is not None:
        _close_socket(_server_socket)
    _server_socket = None
    _server_started = False
    _log("stopped")
