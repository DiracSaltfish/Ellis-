# interface.py 重建 —— 对齐原版 tgw/interface.py 的对外行为
# 原版职责: 把 SWIG 底层函数包装成 Login/SetLogSpi/Subscribe 等高层接口并持有全局 SPI
import threading

from . import _backend as _b
from ._kline_units import (
    normalize_verified_159691_szse_one_minute_kline_rows,
    require_verified_159691_szse_one_minute_request,
)
from ._protocol import TgwTransportError, TgwTimeoutError
from ._structures import Cfg as _Cfg

_g_backend = None
_g_log_spi = None


def _backend():
    global _g_backend
    if _g_backend is None:
        _g_backend, src = _b.get_backend()
        if _g_log_spi is not None:
            _g_backend.set_log_spi(_g_log_spi)
        print(f"[tgw] backend = {_g_backend.__class__.__name__} ({src})")
    return _g_backend


class ILogSpi:
    """日志 SPI —— 与原版同名接口; on_log 由引擎回调。"""
    def __init__(self):
        self.max_limitation = False     # 原版语义: 登录失败因顶号上限时置位

    def on_log(self, level, msg):
        pass


def SetLogSpi(log_spi):
    global _g_log_spi
    _g_log_spi = log_spi
    if _g_backend is not None:
        _g_backend.set_log_spi(log_spi)


def Login(config: _Cfg, api_mode, path=""):
    """等价原版: IGMDApi_Init(spi, cfg, api_mode, path)==0 才算成功。
    当前实现要求 config 为 Cfg 或具有同名属性的对象。"""
    be = _backend()
    cfg = {
        "username": config.username,
        "password": config.password,
        "server_vip": config.server_vip,
        "server_port": int(config.server_port),
        "force_logout": bool(getattr(config, "force_logout", False)),
    }
    ec = be.init(cfg, api_mode, path)
    if ec != 0:
        return False
    return be.login() == 0


def Close():
    if _g_backend is not None:
        _g_backend.close()


def GetVersion():
    import platform
    be = _backend()
    return getattr(be, "_version", f"tgw-macos-re ({platform.machine()})")


def GetTaskID():
    """原版为递增任务号, 用于 QueryThirdInfo 关联应答。"""
    be = _backend()
    be._task_seq = getattr(be, "_task_seq", 0) + 1
    return be._task_seq


# Official GetErrorMsg text table (official V1.0.9.2 wheel error_code.py,
# matching the C++ manual's ErrorCode chapter). Unmapped codes return the
# official "unknown error code" fallback.
_ERROR_MESSAGES = {
    -100: "失败",
    -99: "未初始化",
    -98: "空指针",
    -97: "参数非法",
    -96: "网络异常",
    -95: "数据无权限",
    -94: "未登录",
    -93: "分配内存失败",
    -92: "通道错误",
    -91: "查询服务端hqs任务队列溢出",
    -90: "账号已登录",
    -89: "查询服务端HQS系统错误",
    -88: "非查询时间段(非查询时间段不支持查询)",
    -87: "数据库和代码表中没有指定的代码",
    -86: "api模式非法",
    -85: "超过最大可用线程资源",
    -84: "数据解析出错",
    -83: "获取数据超时",
    -82: "周流量耗尽",
    -81: "代码表缓存不可用",
    -80: "超过最大订阅限制",
    -79: "丢失连接",
    -78: "超过最大查询数（含代码表）",
    -77: "三方资讯查询未设置功能号",
    -76: "数据为空",
    -75: "用户不存在",
    -74: "账号/密码错误",
    -73: "api接口不能同时多次调用",
    -70: "任务id重复",
    -69: "查询服务端DQS系统错误",
    0: "成功",
}


def GetErrorMsg(error_code):
    return _ERROR_MESSAGES.get(int(error_code), "unknown error code")


# ---------------- 行情查询/订阅 ----------------

def Subscribe(sub_item, push_spi=None):
    if push_spi is not None:
        raise NotImplementedError(
            "typed push SPI callbacks are not implemented; use ReceiveRawEvent()"
        )
    items = sub_item if isinstance(sub_item, list) else [sub_item]
    normalized = []
    for item in items:
        code = getattr(item, "security_code", b"")
        if isinstance(code, bytes):
            code = code.split(b"\0", 1)[0].decode("utf-8")
        normalized.append({
            "market": int(getattr(item, "market", 0)),
            "flag": int(getattr(item, "flag", 0)),
            "security_code": code,
            "category_type": int(getattr(item, "category_type", 0)),
        })
    return _backend().subscribe(normalized)


def UnSubscribe(sub_item, push_spi=None):
    if push_spi is not None:
        raise NotImplementedError(
            "typed push SPI callbacks are not implemented; use ReceiveRawEvent()"
        )
    items = sub_item if isinstance(sub_item, list) else [sub_item]
    normalized = []
    for item in items:
        code = getattr(item, "security_code", b"")
        if isinstance(code, bytes):
            code = code.split(b"\0", 1)[0].decode("utf-8")
        normalized.append({
            "market": int(getattr(item, "market", 0)),
            "flag": int(getattr(item, "flag", 0)),
            "security_code": code,
            "category_type": int(getattr(item, "category_type", 0)),
        })
    return _backend().unsubscribe(normalized)


def QueryKline(req_kline_cfg, query_spi=None, return_df_format=True, *, normalized=False):
    """Query raw K-line protocol rows, with an opt-in verified unit view.

    ``normalized=False`` preserves the official-compatible raw integer output.
    ``normalized=True`` is deliberately limited to the independently reconciled
    SZSE 159691 one-minute request on 2026-08-26.  See ``MACOS_SDK_USAGE.md``
    for the output schema and units.
    """
    if query_spi is not None:
        raise NotImplementedError(
            "asynchronous query SPI callbacks are not implemented on macOS"
        )
    if type(normalized) is not bool:
        raise TypeError("normalized must be a bool")
    if normalized:
        require_verified_159691_szse_one_minute_request(req_kline_cfg)
    result = _backend().query("kline", {
        "task_id": GetTaskID(),
        "request": req_kline_cfg,
    })
    if normalized:
        result = normalize_verified_159691_szse_one_minute_kline_rows(result)
    if return_df_format:
        try:
            import pandas as pd
        except ImportError as exc:
            raise RuntimeError(
                "pandas is required for return_df_format=True; use False for JSON rows"
            ) from exc
        result = pd.DataFrame(result)
    return result, 0


def _snapshot_dataframe(rows: list[dict[str, object]]):
    try:
        import pandas as pd
    except ImportError as exc:
        raise RuntimeError(
            "pandas is required for return_df_format=True; use False for JSON rows"
        ) from exc
    return pd.DataFrame(rows)


def QuerySnapshot(req_snapshot, query_spi=None, return_df_format=True):
    """对齐官方同步/异步合约的快照查询（已验证：SZSE 159518, data_type=0）。

    同步模式返回 ``(result, error_code)``：成功 ``(rows, 0)``；空数据按官方
    语义返回 ``(None, -76/kDataEmpty)``。异步模式提交成功立即返回 ``(True,
    None)``，结果稍后在后台线程经 ``query_spi(result, err_code)`` 交付（与
    官方 wrapper 一致，直接调用用户对象本身）：数据批次 ``(rows_or_df,
    None)``，错误 ``(None, error_code)``，内部异常 ``(None, str(exc))``，
    超时映射 ``kTimeout=-83``。
    """
    request_payload = {
        "task_id": GetTaskID(),
        "request": req_snapshot,
    }
    if query_spi is not None:
        # Synchronous submit phase mirrors the official (True/False, err)
        # contract; local validation failures surface before returning.
        prepared = _backend().build_query("snapshot", request_payload)

        def _deliver():
            try:
                rows, error = _backend().run_query(prepared)
                if error is not None:
                    query_spi(None, int(error))
                    return
                query_spi(
                    _snapshot_dataframe(rows) if return_df_format else rows, None
                )
            except TgwTimeoutError:
                query_spi(None, -83)
            except Exception as exc:  # official wrapper passes str(e) through
                query_spi(None, str(exc))

        threading.Thread(target=_deliver, name="tgw-query-snapshot", daemon=True).start()
        return True, None
    rows, error = _backend().query("snapshot", request_payload)
    if error is not None:
        return None, error
    if rows and return_df_format:
        rows = _snapshot_dataframe(rows)
    return rows, 0


def QueryETFInfo(req_etf_info_cfg, query_spi=None, return_df_format=True):
    """对齐官方 QueryETFInfo 同步子范围（已验证：单条 SSE ETF 样本）。

    返回容器与官方 json 格式一致：[(basic_info_dict, [constituent_dict...]), ...]。
    数值保持服务端原样（缩放由调用方按头文件注释处理）。异步 SPI 未实现。
    """
    if query_spi is not None:
        raise NotImplementedError(
            "asynchronous query SPI callbacks are not implemented on macOS"
        )
    items_in = req_etf_info_cfg if isinstance(req_etf_info_cfg, list) else [req_etf_info_cfg]
    if len(items_in) != 1:
        raise NotImplementedError(
            "only the single-item ETF info query has been wire-verified"
        )
    item = items_in[0]
    code = getattr(item, "security_code", b"")
    if isinstance(code, bytes):
        code = code.split(b"\0", 1)[0].decode("utf-8")
    result = _backend().query("etf_info", {
        "task_id": GetTaskID(),
        "items": [{"market": int(getattr(item, "market", 0)), "security_code": code}],
    })
    if return_df_format:
        try:
            import pandas as pd
        except ImportError as exc:
            raise RuntimeError(
                "pandas is required for return_df_format=True; use False for JSON rows"
            ) from exc
        result = [
            (pd.DataFrame([basic]), pd.DataFrame(constituents))
            for basic, constituents in result
        ]
    return result, 0


def QuerySecuritiesInfo(req_securities_info_cfg, query_spi=None, return_df_format=True):
    """对齐官方 QuerySecuritiesInfo 同步子范围（已验证：单条 SSE 证券静态信息）。

    返回容器与官方 json 格式一致：list[dict]，每行 43 键（MDCodeTableRecord）。
    数值保持服务端原样（缩放由调用方按头文件注释处理）。异步 SPI 未实现。
    """
    if query_spi is not None:
        raise NotImplementedError(
            "asynchronous query SPI callbacks are not implemented on macOS"
        )
    items_in = (
        req_securities_info_cfg
        if isinstance(req_securities_info_cfg, list)
        else [req_securities_info_cfg]
    )
    if len(items_in) != 1:
        raise NotImplementedError(
            "only the single-item securities-info query has been wire-verified"
        )
    item = items_in[0]
    code = getattr(item, "security_code", b"")
    if isinstance(code, bytes):
        code = code.split(b"\0", 1)[0].decode("utf-8")
    result = _backend().query("securities_info", {
        "task_id": GetTaskID(),
        "items": [{"market": int(getattr(item, "market", 0)), "security_code": code}],
    })
    if return_df_format:
        try:
            import pandas as pd
        except ImportError as exc:
            raise RuntimeError(
                "pandas is required for return_df_format=True; use False for JSON rows"
            ) from exc
        result = pd.DataFrame(result)
    return result, 0


def SetThirdInfoParam(task_id, key, value):
    """三方资讯查询参数注入(日历/财务等全部走此通道)。"""
    be = _backend()
    if not hasattr(be, "_third_params"):
        be._third_params = {}
    be._third_params.setdefault(int(task_id), {})[str(key)] = str(value)
    return 0


def QueryThirdInfo(task_id, query_spi=None, return_df_format=True):
    if query_spi is not None:
        raise NotImplementedError(
            "asynchronous query SPI callbacks are not implemented on macOS"
        )
    be = _backend()
    params = getattr(be, "_third_params", {}).pop(int(task_id), {})
    result = be.query("third_info", {"task_id": task_id, "params": params})
    if return_df_format:
        try:
            import pandas as pd
        except ImportError as exc:
            raise RuntimeError(
                "pandas is required for return_df_format=True; use False for JSON rows"
            ) from exc
        result = pd.DataFrame(result)
    return result, 0


def QueryExFactorTable(security_code, query_spi=None, return_df_format=True):
    """对齐官方 QueryExFactorTable 同步子范围（已验证：`000001` 单代码）。

    官方签名为 ``QueryExFactorTable(security_code, query_spi=None,
    return_df_format=True)``，返回 ``(result, err_code)`` 元组。同步模式返回
    ``(rows, 0)``，每行 5 键（inner_code/security_code/ex_date/ex_factor/
    cum_factor），其中 ex_factor/cum_factor 为 Python float（与服务端 double
    的 N38(15) 精度一致）。异步 SPI 未实现，传入时显式报错。
    """
    if query_spi is not None:
        raise NotImplementedError(
            "asynchronous query SPI callbacks are not implemented on macOS"
        )
    code = security_code
    if isinstance(code, bytes):
        code = code.split(b"\0", 1)[0].decode("utf-8")
    result = _backend().query("ex_factor", {
        "task_id": GetTaskID(),
        "security_code": str(code),
    })
    if return_df_format:
        try:
            import pandas as pd
        except ImportError as exc:
            raise RuntimeError(
                "pandas is required for return_df_format=True; use False for JSON rows"
            ) from exc
        result = pd.DataFrame(result)
    return result, 0


def QueryCodeTable(query_spi=None, return_df_format=True):
    """官方 QueryCodeTable 全市场互联网模式同步查询（无业务入参）。

    同步模式返回 ``(rows, 0)``。官方同步 wrapper 在多包响应下可能只保留首个
    批次（已登记竞态）；本实现按异步收集器语义累计**全部**批次，与官方异步
    总数对齐（差异在 evidence 中说明）。异步 SPI 未实现，传入时显式报错。
    """
    if query_spi is not None:
        raise NotImplementedError(
            "asynchronous query SPI callbacks are not implemented on macOS"
        )
    rows = _backend().query("code_table", {"task_id": GetTaskID()})
    if return_df_format:
        try:
            import pandas as pd
        except ImportError as exc:
            raise RuntimeError(
                "pandas is required for return_df_format=True; use False for JSON rows"
            ) from exc
        rows = pd.DataFrame(rows)
    return rows, 0


def ReceiveRawEvent(timeout=None):
    """Return one decoded raw push event from the live WebSocket queue.

    This is the temporary macOS delivery API until the official typed SPI
    callback surface and full/delta state reconstruction are implemented.
    """
    be = _backend()
    client = getattr(be, "client", None)
    if client is None:
        raise RuntimeError("raw push events are available only on the live backend")
    value = client.recv_event(timeout=timeout)
    if isinstance(value, Exception):
        raise TgwTransportError(f"TGW push reader failed: {value}") from value
    return value
