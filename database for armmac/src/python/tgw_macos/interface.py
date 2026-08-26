# interface.py 重建 —— 对齐原版 tgw/interface.py 的对外行为
# 原版职责: 把 SWIG 底层函数包装成 Login/SetLogSpi/Subscribe 等高层接口并持有全局 SPI
from . import _backend as _b
from ._protocol import TgwTransportError
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


def GetErrorMsg(error_code):
    return {0: "success"}.get(error_code, f"error_{error_code}")


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


def QueryKline(req_kline_cfg, query_spi=None, return_df_format=True):
    if query_spi is not None:
        raise NotImplementedError(
            "asynchronous query SPI callbacks are not implemented on macOS"
        )
    result = _backend().query("kline", {
        "task_id": GetTaskID(),
        "request": req_kline_cfg,
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


def QuerySnapshot(req_snapshot, query_spi=None, return_df_format=True):
    if query_spi is not None:
        raise NotImplementedError(
            "asynchronous query SPI callbacks are not implemented on macOS"
        )
    result = _backend().query("snapshot", {
        "task_id": GetTaskID(),
        "request": req_snapshot,
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
