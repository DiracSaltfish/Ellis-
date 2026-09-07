"""Experimental AmazingData ``BaseData`` compatibility surface.

Only ``BaseData.get_calendar``'s documented default-SH branch is implemented.
The official high-level wrapper currently uses the newer online ThirdInfo
function id ``A010061003``; that differs from the older C++ manual directory
and is deliberately not generalized to the other directory function ids.

``get_code_info(EXTRA_ETF)`` has an offline normalisation contract below, but
is deliberately not wired to the live backend yet: the official Linux wrapper
does three full-market, empty-code ``QuerySecuritiesInfo`` calls, while the
Mac transport has only been wire-verified for one non-empty SSE code.  Raising
explicitly avoids advertising a partial first response as the full code table.
"""

import datetime as _datetime
import re as _re


_DEFAULT_CALENDAR_DATE = int(_datetime.date.today().strftime("%Y%m%d"))
_CALENDAR_FUNCTION_ID = "A010061003"
_CALENDAR_START_DATE = "19900101"
_CALENDAR_EXCHANGE = "SSE"
_CALENDAR_PAGE_SIZE = 1000
_MAX_CALENDAR_PAGES = 100

_CODE_INFO_EXTRA_ETF = "EXTRA_ETF"
_CODE_INFO_COLUMNS = (
    "symbol",
    "security_status",
    "pre_close",
    "high_limited",
    "low_limited",
    "price_tick",
    "list_day",
)
_CODE_INFO_RAW_COLUMNS = (
    "security_code",
    "symbol",
    "security_status",
    "pre_close_price",
    "high_limited",
    "low_limited",
    "price_tick",
    "list_day",
)
_CODE_INFO_PRICE_COLUMNS = (
    "pre_close_price",
    "high_limited",
    "low_limited",
    "price_tick",
)
_CODE_INFO_MARKET_SUFFIX = {102: ".SZ", 101: ".SH", 2: ".BJ"}
_EXTRA_ETF_CODE = _re.compile(
    r"(?:"
    r"(?:510|511|512|513|515|516|517|518|520|530|551|560|561|562|563|588|589)\d{3}\.SH"
    r"|159\d{3}\.SZ"
    r")"
)


def _normalise_extra_etf_frames(frames):
    """Apply the observed official ``EXTRA_ETF`` DataFrame conversion.

    ``frames`` is a sequence of ``(market, raw_dataframe)`` pairs in the
    official query order (SZSE, SSE, NEEQ).  This pure helper is intentionally
    separate from live querying so it can be regression-tested without making
    unverified all-market requests on macOS.
    """
    try:
        import pandas as pd
    except ImportError as exc:
        raise RuntimeError("pandas is required for AmazingData code-info conversion") from exc

    converted = []
    for market, raw_frame in frames:
        if market not in _CODE_INFO_MARKET_SUFFIX:
            raise NotImplementedError(
                f"BaseData.get_code_info has no verified market mapping for {market}"
            )
        if not isinstance(raw_frame, pd.DataFrame):
            raise TypeError("QuerySecuritiesInfo result must be a pandas DataFrame")
        missing = [column for column in _CODE_INFO_RAW_COLUMNS if column not in raw_frame]
        if missing:
            raise RuntimeError(
                "QuerySecuritiesInfo result lacks required code-info columns: "
                + ", ".join(missing)
            )
        if not raw_frame["security_code"].map(lambda value: isinstance(value, str)).all():
            raise TypeError("QuerySecuritiesInfo security_code values must be strings")
        frame = raw_frame.loc[:, _CODE_INFO_RAW_COLUMNS].copy()
        frame["code_market"] = (
            frame["security_code"] + _CODE_INFO_MARKET_SUFFIX[market]
        )
        converted.append(frame)

    if not converted:
        empty = pd.DataFrame(columns=_CODE_INFO_COLUMNS)
        empty.index.name = "code_market"
        return empty

    result = pd.concat(converted, ignore_index=True)
    result = result.drop_duplicates(subset="code_market").set_index("code_market")
    result = result.loc[
        result.index.map(lambda value: bool(_EXTRA_ETF_CODE.fullmatch(value)))
    ]
    result = result.loc[:, [
        "symbol",
        "security_status",
        "pre_close_price",
        "high_limited",
        "low_limited",
        "price_tick",
        "list_day",
    ]]
    # Assign each column after an explicit float conversion.  ``.loc`` bulk
    # assignment can preserve an integer dtype for columns whose synthetic or
    # live values happen to divide exactly, unlike the official DataFrame
    # division result which exposes all four price columns as ``float64``.
    for column in _CODE_INFO_PRICE_COLUMNS:
        result[column] = result[column].astype("float64") / 1_000_000
    return result.rename(columns={"pre_close_price": "pre_close"})


class BaseData:
    def __init__(self):
        self.calendar = []          # 升序 int8 交易日列表, 如 20260826
        self.stock_list = []
        self.code_list_hist = []
        self.block_trading = {}

    def get_calendar(self, data_type='str', market='SH', date=_DEFAULT_CALENDAR_DATE):
        """Return the official default-SH trading calendar.

        Official Linux observation resolves the manual's ``List[int]`` versus
        ``data_type`` ambiguity as follows: the default and ``'str'`` branch
        return sorted ``list[int]`` YYYYMMDD values; ``'datetime'`` returns
        sorted ``list[datetime.datetime]`` midnight values.  Other markets and
        data types remain deliberately unsupported until separately observed.
        """
        import tgw_macos.interface as tgw_i

        if market != 'SH':
            raise NotImplementedError(
                "BaseData.get_calendar currently supports only market='SH'"
            )
        if data_type not in ('str', 'datetime'):
            raise NotImplementedError(
                "BaseData.get_calendar supports data_type='str' or 'datetime'"
            )
        if isinstance(date, bool) or not isinstance(date, int):
            raise TypeError("date must be an integer YYYYMMDD value")
        date_text = str(date)
        if len(date_text) != 8 or not date_text.isdigit():
            raise ValueError("date must be an eight-digit YYYYMMDD value")
        if date != _DEFAULT_CALENDAR_DATE:
            raise NotImplementedError(
                "BaseData.get_calendar currently supports only its official default date"
            )

        parameters = (
            ('function_id', _CALENDAR_FUNCTION_ID),
            ('start_date', _CALENDAR_START_DATE),
            ('end_date', date_text),
            ('market', _CALENDAR_EXCHANGE),
        )
        rows = []
        for page_number in range(_MAX_CALENDAR_PAGES):
            task_id = tgw_i.GetTaskID()
            for key, value in parameters:
                result = tgw_i.SetThirdInfoParam(task_id, key, value)
                if result != 0:
                    raise RuntimeError(f"SetThirdInfoParam failed for {key}: {result}")
            page, error_code = tgw_i._QueryThirdInfoPage(
                task_id,
                offset=len(rows),
                count=_CALENDAR_PAGE_SIZE,
                return_df_format=False,
            )
            if error_code != 0:
                raise RuntimeError(f"QueryThirdInfo failed: {error_code}")
            if page is None:
                page = []
            if not isinstance(page, list):
                raise TypeError("QueryThirdInfo calendar result must be a list of rows")
            rows.extend(page)
            if len(page) < _CALENDAR_PAGE_SIZE:
                break
        else:
            raise RuntimeError("calendar pagination exceeded the verified safety limit")

        days = sorted(int(row['TRADE_DAYS']) for row in rows)
        if data_type == 'datetime':
            calendar = [
                _datetime.datetime.strptime(str(day), "%Y%m%d") for day in days
            ]
        else:
            calendar = days
        self.calendar = calendar
        return calendar

    def get_code_info(self, security_type="EXTRA_STOCK_A"):
        """Return latest ETF code information once full-market wire parity exists.

        The official signature defaults to ``EXTRA_STOCK_A``.  This experimental
        surface deliberately supports neither that default nor other security
        types until each has independent evidence.  Even ``EXTRA_ETF`` remains
        blocked because its official three-market empty-code request sequence
        has no completed Mac wire/pagination proof yet.
        """
        if not isinstance(security_type, str):
            raise TypeError("security_type must be a string")
        if security_type != _CODE_INFO_EXTRA_ETF:
            raise NotImplementedError(
                "BaseData.get_code_info currently accepts only security_type='EXTRA_ETF'"
            )
        raise NotImplementedError(
            "BaseData.get_code_info(EXTRA_ETF) requires verified macOS "
            "three-market empty-code QuerySecuritiesInfo paging before live use"
        )
