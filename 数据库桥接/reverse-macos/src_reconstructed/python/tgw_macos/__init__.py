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
    kSuccess = 0
    kFail = -1


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
)
from ._backend import get_backend                                                 # noqa: E402
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
QuerySnapshot = interface.QuerySnapshot
QueryThirdInfo = interface.QueryThirdInfo
SetThirdInfoParam = interface.SetThirdInfoParam
GetErrorMsg = interface.GetErrorMsg

__version__ = "1.0.9.2.macos.re2"
