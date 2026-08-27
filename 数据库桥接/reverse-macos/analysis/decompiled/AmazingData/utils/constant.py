# Source Generated with Decompyle++
# File: constant.pyc (Python 3.12)

from enum import Enum, unique
from datetime import datetime
from typing import Optional
from pydantic import BaseModel
import tgw
Period = <NODE:12>()

class Order(BaseModel):
    biz_index: Optional[int] = 'Order'


class Execution(BaseModel):
    biz_index: Optional[int] = 'Execution'


class OrderQueue(BaseModel):
    md_stream_id: str = 'OrderQueue'


class SnapshotL2(BaseModel):
    trading_phase_code: str = 'SnapshotL2'


class SnapshotOption(BaseModel):
    exercise_price: Optional[float] = 'SnapshotOption'


class SnapshotFuture(BaseModel):
    trading_day: str = 'SnapshotFuture'


class SnapshotHKT(BaseModel):
    trading_phase_code: str = 'SnapshotHKT'


class Snapshot(BaseModel):
    trading_phase_code: str = 'Snapshot'


class SnapshotIndex(BaseModel):
    trading_phase_code: str = 'SnapshotIndex'


class Kline(BaseModel):
    amount: Optional[float] = 'Kline'

