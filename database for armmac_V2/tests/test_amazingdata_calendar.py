"""Offline contract tests for the experimental AmazingData calendar wrapper."""
from __future__ import annotations

import datetime as dt
import inspect
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "python"))
sys.path.insert(0, str(ROOT / "experimental" / "amazingdata_compat"))

import tgw_macos.interface as tgw_i  # noqa: E402
from amazingdata_re.base_data import BaseData, _DEFAULT_CALENDAR_DATE  # noqa: E402


class AmazingDataCalendarTests(unittest.TestCase):
    def setUp(self) -> None:
        self.task_id = 7123
        self.parameters: list[tuple[int, str, str]] = []
        self.rows: list[dict[str, str]] | None = [
            {"TRADE_DAYS": "20240202"},
            {"TRADE_DAYS": "20240102"},
        ]
        self.pages: dict[int, list[dict[str, str]] | None] = {0: self.rows}
        self.error_code = 0
        self.query_calls: list[tuple[int, int, int, bool]] = []
        self.get_task = mock.patch.object(tgw_i, "GetTaskID", return_value=self.task_id)
        self.set_param = mock.patch.object(tgw_i, "SetThirdInfoParam", self._set_param)
        self.query = mock.patch.object(tgw_i, "_QueryThirdInfoPage", self._query)
        self.get_task.start()
        self.set_param.start()
        self.query.start()
        self.addCleanup(self.get_task.stop)
        self.addCleanup(self.set_param.stop)
        self.addCleanup(self.query.stop)

    def _set_param(self, task_id: int, key: str, value: str) -> int:
        self.parameters.append((task_id, key, value))
        return 0

    def _query(
        self, task_id: int, *, offset: int, count: int, return_df_format: bool
    ) -> tuple[list[dict[str, str]] | None, int]:
        self.query_calls.append((task_id, offset, count, return_df_format))
        return self.pages.get(offset, []), self.error_code

    def test_signature_and_default_str_contract(self) -> None:
        signature = inspect.signature(BaseData.get_calendar)
        self.assertEqual(
            list(signature.parameters), ["self", "data_type", "market", "date"]
        )
        self.assertEqual(signature.parameters["data_type"].default, "str")
        self.assertEqual(signature.parameters["market"].default, "SH")
        self.assertEqual(signature.parameters["date"].default, _DEFAULT_CALENDAR_DATE)

        base = BaseData()
        result = base.get_calendar()

        self.assertEqual(result, [20240102, 20240202])
        self.assertIs(base.calendar, result)
        self.assertEqual(
            self.parameters,
            [
                (self.task_id, "function_id", "A010061003"),
                (self.task_id, "start_date", "19900101"),
                (self.task_id, "end_date", str(_DEFAULT_CALENDAR_DATE)),
                (self.task_id, "market", "SSE"),
            ],
        )
        self.assertEqual(self.query_calls, [(self.task_id, 0, 1000, False)])

    def test_datetime_contract_is_datetime_list(self) -> None:
        base = BaseData()
        result = base.get_calendar(data_type="datetime")

        self.assertEqual(len(result), 2)
        self.assertTrue(all(type(item) is dt.datetime for item in result))
        self.assertEqual(result, sorted(result))
        self.assertIs(base.calendar, result)

    def test_explicit_str_contract_is_the_default_integer_calendar(self) -> None:
        base = BaseData()
        result = base.get_calendar(data_type="str")

        self.assertEqual(result, [20240102, 20240202])
        self.assertTrue(all(type(item) is int for item in result))
        self.assertIs(base.calendar, result)

    def test_empty_success_is_an_empty_calendar(self) -> None:
        self.pages = {0: []}
        base = BaseData()

        self.assertEqual(base.get_calendar(), [])
        self.assertEqual(base.calendar, [])

    def test_query_error_is_not_silently_converted_to_empty_success(self) -> None:
        self.pages = {0: []}
        self.error_code = -76

        with self.assertRaisesRegex(RuntimeError, "QueryThirdInfo failed: -76"):
            BaseData().get_calendar()

    def test_unobserved_market_and_data_type_fail_before_any_request(self) -> None:
        base = BaseData()
        with self.assertRaises(NotImplementedError):
            base.get_calendar(market="SZ")
        with self.assertRaises(NotImplementedError):
            base.get_calendar(data_type="date")
        self.assertEqual(self.parameters, [])
        self.assertEqual(self.query_calls, [])

    def test_invalid_date_is_rejected_before_any_request(self) -> None:
        base = BaseData()
        with self.assertRaises(ValueError):
            base.get_calendar(date=20268)
        with self.assertRaises(TypeError):
            base.get_calendar(date="20260829")  # type: ignore[arg-type]
        with self.assertRaises(NotImplementedError):
            base.get_calendar(date=_DEFAULT_CALENDAR_DATE - 1)
        self.assertEqual(self.parameters, [])
        self.assertEqual(self.query_calls, [])

    def test_full_page_fetches_the_next_offset_before_converting_results(self) -> None:
        self.pages = {
            0: [{"TRADE_DAYS": "20240101"}] * 1000,
            1000: [{"TRADE_DAYS": "20240102"}],
        }

        result = BaseData().get_calendar()

        self.assertEqual(len(result), 1001)
        self.assertEqual(
            self.query_calls,
            [
                (self.task_id, 0, 1000, False),
                (self.task_id, 1000, 1000, False),
            ],
        )


if __name__ == "__main__":
    unittest.main()
