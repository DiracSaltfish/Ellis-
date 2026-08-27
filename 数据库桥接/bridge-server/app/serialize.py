"""DataFrame / SDK 对象 / 基础类型 <-> JSON 可传输结构。

服务端 pack()：任何返回值 → JSON 安全的 dict/list/标量。
客户端（bridge-client）按相同信封还原 pandas DataFrame。

DataFrame 信封：
{"__type__":"df","columns":[...],"index":[...],"index_dtype":"datetime64[ns]",
 "dtypes":{"col":"dtype"},"rows":[[...],...]}
对象信封：
{"__type__":"obj","class":"TGWSnapshotL1","fields":{...}}
"""
from __future__ import annotations

import datetime as _dt
from typing import Any

import numpy as np
import pandas as pd

DF = "df"
OBJ = "obj"


def _clean_scalar(v: Any) -> Any:
    if v is None or v is pd.NaT:
        return None
    if isinstance(v, (pd.Timestamp, _dt.datetime, _dt.date, pd.Timedelta)):
        return str(v)
    if isinstance(v, (_dt.time,)):
        return str(v)
    if isinstance(v, np.datetime64):
        return None if np.isnat(v) else str(v)
    if isinstance(v, (np.bool_, bool)):
        return bool(v)
    if isinstance(v, (np.integer, int)):
        return int(v)
    if isinstance(v, (np.floating, float)):
        f = float(v)
        return None if (np.isnan(f) or np.isinf(f)) else f
    if isinstance(v, (bytes, bytearray)):
        return bytes(v).decode("utf-8", errors="replace")
    if isinstance(v, np.ndarray):
        return [_clean_scalar(x) for x in v.tolist()]
    return v


def df_pack(df: pd.DataFrame) -> dict:
    cols = [str(c) for c in df.columns]
    dtypes = {str(c): str(t) for c, t in zip(df.columns, df.dtypes)}
    idx = df.index
    index_vals = [_clean_scalar(v) for v in idx.tolist()]
    rows: list[list] = []
    values = df.to_numpy(dtype=object)
    for row in values:
        rows.append([_clean_scalar(v) for v in row])
    return {
        "__type__": DF,
        "columns": cols,
        "dtypes": dtypes,
        "index": index_vals,
        "index_dtype": str(idx.dtype),
        "rows": rows,
    }


def pack(obj: Any, depth: int = 0) -> Any:
    if depth > 8:
        return str(obj)
    if obj is None or isinstance(obj, (str, int, float, bool)):
        if isinstance(obj, float) and (np.isnan(obj) or np.isinf(obj)):
            return None
        return obj
    if isinstance(obj, pd.DataFrame):
        return df_pack(obj)
    if isinstance(obj, pd.Series):
        return df_pack(obj.to_frame())
    if isinstance(obj, dict):
        return {str(k): pack(v, depth + 1) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [pack(v, depth + 1) for v in obj]
    # numpy 标量 / 时间类型等
    cleaned = _clean_scalar(obj)
    if not isinstance(cleaned, (np.generic, np.ndarray, pd.Timestamp,
                                _dt.datetime, _dt.date, _dt.time)):
        return cleaned
    # 普通 Python 对象（如 TGWSnapshotL1 等 SDK 推送结构）
    d = getattr(obj, "__dict__", None)
    if isinstance(d, dict):
        return {
            "__type__": OBJ,
            "class": type(obj).__name__,
            "fields": {str(k): pack(v, depth + 1) for k, v in d.items()},
        }
    return str(obj)


def is_envelope(v: Any) -> bool:
    return isinstance(v, dict) and v.get("__type__") in (DF, OBJ)
