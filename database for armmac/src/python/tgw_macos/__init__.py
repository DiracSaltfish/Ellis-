# tgw_macos —— 银河 TGW 包装层的 macOS ARM64 重建版（行为级伪源码）
#
# 结构对照原版 tgw 包:
#   原版: __init__.py 按 win/linux x64 + py版本 分发到平台包(SWIG代理+原生so/pyd)
#   本版: darwin/arm64 默认使用真实 TLS/WebSocket 互联网后端；
#         模拟后端和旧 C++ 骨架仅可显式选择，不会伪装成功登录。
import os
import platform
import sys
from enum import IntEnum

if platform.system() != "Darwin":
    raise Exception("tgw_macos 仅面向 macOS；其他平台请使用厂商原版包")


class ApiMode(IntEnum):
    kColocationMode = 1   # 托管机房专线
    kInternetMode = 2     # 互联网


class ErrorCode(IntEnum):
    """Public error codes; values verified against the official V1.0.9.2
    Python wheel and the C++ manual's ErrorCode table (PDF 页 62 / 正文 54)."""

    kFailure = -100
    kUnInited = -99
    kNullSpi = -98
    kParamIllegal = -97
    kNetError = -96
    kPermissionError = -95
    kLogonFailed = -94
    kAllocateMemoryFailed = -93
    kChannelError = -92
    kOverLoad = -91
    kLogoned = -90
    kHqsError = -89
    kNonQueryTimePeriod = -88
    kDbAndCodeTableNoCode = -87
    kIllegalMode = -86
    kThreadBusy = -85
    kParseDataError = -84
    kTimeout = -83
    kFlowOverLimit = -82
    kCodeTableCacheNotAvailable = -81
    kOverMaxSubLimit = -80
    kLostConnection = -79
    kOverMaxQueryLimit = -78
    kFunctionIdNull = -77
    kDataEmpty = -76
    kUserNotExist = -75
    kVerifyFailure = -74
    kApiInterfaceUsing = -73
    kTaskIdRepeat = -70
    kDqsError = -69
    kSuccess = 0


class LogLevel(IntEnum):
    kTrace = 0
    kDebug = 1
    kInfo = 2
    kWarn = 3
    kError = 4
    kFatal = 5


class MarketType(IntEnum):
    kNone = 0
    kNEEQ = 2
    kSHFE = 3
    kCFFEX = 4
    kDCE = 5
    kCZCE = 6
    kINE = 7
    kSSE = 101
    kSZSE = 102
    kHKEx = 103
    kBK = 201


class SubscribeDataType(IntEnum):
    kNone = 0
    k1MinKline = 1
    k3MinKline = 2
    k5MinKline = 3
    k10MinKline = 4
    k15MinKline = 5
    k30MinKline = 6
    k60MinKline = 7
    k120MinKline = 8
    kSnapshotDerive = 9
    kSnapshot = 10
    kOptionSnapshot = 11
    kHKTSnapshot = 12
    kIndexSnapshot = 13
    kAfterHourFixedPriceSnapshot = 14
    kCSIIndexSnapshot = 15
    kCnIndexSnapshot = 16
    kHKTRealtimeLimit = 17
    kHKTProductStatus = 18
    kHKTVCM = 19
    kFutureSnapshot = 20
    kSnapshotL2 = 21
    kTickOrder = 22
    kTickExecution = 23
    kOrderQueue = 24


from ._structures import (  # noqa: E402
    Cfg, ColocaCfg, LogonResponse, SubscribeItem, ReqKline, ReqDefault,
    SubCodeTableItem, MDCodeTable, MDCodeTableRecord, MDExFactorTable,
)
from ._backend import get_backend                                                 # noqa: E402
from ._kline_units import (                                                       # noqa: E402
    normalize_verified_159691_szse_one_minute_kline_rows,
)
from . import interface                                                           # noqa: E402,E402

# 与原版一致的顶层再导出
Login = interface.Login
Close = interface.Close
GetVersion = interface.GetVersion
GetTaskID = interface.GetTaskID
SetLogSpi = interface.SetLogSpi
Subscribe = interface.Subscribe
UnSubscribe = interface.UnSubscribe
QueryKline = interface.QueryKline
# Explicit opt-in adapter; its deliberately narrow scope is documented in
# MACOS_SDK_USAGE.md and validated at runtime.
NormalizeVerified159691SzseOneMinuteKlineRows = (
    normalize_verified_159691_szse_one_minute_kline_rows
)
QuerySnapshot = interface.QuerySnapshot
QueryETFInfo = interface.QueryETFInfo
QuerySecuritiesInfo = interface.QuerySecuritiesInfo
QueryExFactorTable = interface.QueryExFactorTable
QueryThirdInfo = interface.QueryThirdInfo
QueryCodeTable = interface.QueryCodeTable
SetThirdInfoParam = interface.SetThirdInfoParam
GetErrorMsg = interface.GetErrorMsg
ReceiveRawEvent = interface.ReceiveRawEvent

__version__ = "1.0.9.2.macos.re6"
