"""Offline contract tests for the official ``GetErrorMsg`` text table.

Expected strings were called directly from the Linux x86 official V1.0.9.2
wheel without Login or any query.  They deliberately include the two
V1.0.8-header-only codes (-70/-69) and three codes absent from the official
table, so a future table edit cannot silently widen the fallback behavior.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "python"))

from tgw_macos import ErrorCode, GetErrorMsg  # noqa: E402


OFFICIAL_ERROR_MESSAGES = {
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

OFFICIAL_ERROR_ENUM = {
    "kFailure": -100,
    "kUnInited": -99,
    "kNullSpi": -98,
    "kParamIllegal": -97,
    "kNetError": -96,
    "kPermissionError": -95,
    "kLogonFailed": -94,
    "kAllocateMemoryFailed": -93,
    "kChannelError": -92,
    "kOverLoad": -91,
    "kLogoned": -90,
    "kHqsError": -89,
    "kNonQueryTimePeriod": -88,
    "kDbAndCodeTableNoCode": -87,
    "kIllegalMode": -86,
    "kThreadBusy": -85,
    "kParseDataError": -84,
    "kTimeout": -83,
    "kFlowOverLimit": -82,
    "kCodeTableCacheNotAvailable": -81,
    "kOverMaxSubLimit": -80,
    "kLostConnection": -79,
    "kOverMaxQueryLimit": -78,
    "kFunctionIdNull": -77,
    "kDataEmpty": -76,
    "kUserNotExist": -75,
    "kVerifyFailure": -74,
    "kApiInterfaceUsing": -73,
    "kTaskIdRepeat": -70,
    "kDqsError": -69,
    "kSuccess": 0,
}


class GetErrorMsgContractTests(unittest.TestCase):
    def test_public_error_enum_matches_official_linux_wheel(self):
        self.assertEqual(
            {member.name: member.value for member in ErrorCode},
            OFFICIAL_ERROR_ENUM,
        )

    def test_all_official_registered_codes_match_linux_wheel_text(self):
        self.assertEqual(len(OFFICIAL_ERROR_MESSAGES), 31)
        self.assertEqual(
            {code: GetErrorMsg(code) for code in OFFICIAL_ERROR_MESSAGES},
            OFFICIAL_ERROR_MESSAGES,
        )

    def test_header_only_error_codes_are_public_enum_members(self):
        self.assertEqual(ErrorCode.kTaskIdRepeat, -70)
        self.assertEqual(ErrorCode.kDqsError, -69)
        self.assertEqual(GetErrorMsg(ErrorCode.kTaskIdRepeat), "任务id重复")
        self.assertEqual(GetErrorMsg(ErrorCode.kDqsError), "查询服务端DQS系统错误")

    def test_unregistered_codes_use_the_official_fallback(self):
        for code in (-72, -71, 1):
            with self.subTest(code=code):
                self.assertEqual(GetErrorMsg(code), "unknown error code")
