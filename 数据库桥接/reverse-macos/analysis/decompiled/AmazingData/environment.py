# Source Generated with Decompyle++
# File: environment.pyc (Python 3.12)

import threading

class QueryLock(object):
    query_lock = threading.RLock()


class QueryPara(object):
    retry_times = 3
    req_backward_factor_len = 300
    req_adj_factor_len = 300
    req_hist_code_list_len = 30
    req_block_trading_len = 200
    req_long_hu_bang_len = 200
    req_share_holder_len = 200
    req_holder_num_len = 200
    req_option_basic_info_len = 200
    req_option_std_ctr_specs_len = 200
    req_option_mon_ctr_specs_len = 200
    req_dividend_len = 200
    req_right_issue_len = 200
    req_equity_structure_len = 200
    req_equity_restricted_len = 200
    req_stock_basic_len = 200
    req_equity_pledge_freeze_len = 200
    req_profit_notice_len = 200
    req_profit_excess_len = 200
    req_hist_stock_status_len = 200
    req_balance_sheet_len = 200
    req_income_len = 200
    req_cash_flow_len = 200
    req_margin_detail_len = 200
    req_fund_share_len = 200
    req_fund_nav_len = 200
    req_fund_iopv_len = 200
    req_index_weight_len = 50
    req_index_constituent_len = 200
    req_industry_weight_len = 50
    req_industry_constituent_len = 200
    req_industry_daily_len = 2
    req_treasury_yield_len = 200
    req_etf_pdf_len = 300
    req_kzz_issuance_len = 50
    req_kzz_share_len = 50
    req_kzz_conv_len = 50
    req_kzz_conv_change_len = 50
    req_kzz_corr_len = 50
    req_kzz_put_call_item_len = 50
    req_kzz_call_len = 50
    req_kzz_put_len = 50
    req_kzz_put_explanation_len = 50
    req_kzz_call_explanation_len = 50
    req_kzz_suspend_len = 50
    req_announcement_stock_len = 200
    req_announcement_fund_len = 200
    req_announcement_bond_len = 200
    req_snapshot_len = 50
    req_kline_len = 500
    req_kline_day_date_len = 1500
    req_kline_min1_date_len = 10
    req_kline_min3_date_len = 30
    req_kline_min5_date_len = 50
    req_kline_min10_date_len = 100
    req_kline_min15_date_len = 120
    req_kline_min30_date_len = 200
    req_kline_min60_date_len = 400
    req_kline_min120_date_len = 800
    req_kline_week_date_len = 1500
    req_kline_month_date_len = 1500
    req_kline_season_date_len = 1500
    req_kline_year_date_len = 1500


class EnvSnapshot(object):
    wait_event = threading.Event()
    req_list_len = None
    data = { }
    error_list = []
    refresh_event = (lambda cls: cls.wait_event = threading.Event()cls.req_list_len = None)()
    refresh_error_list = (lambda cls: cls.error_list = [])()
    refresh_data = (lambda cls: cls.data = { })()


class EnvKline(object):
    wait_event = threading.Event()
    req_list_len = None
    data = { }
    error_list = []
    refresh_event = (lambda cls: cls.wait_event = threading.Event()cls.req_list_len = None)()
    refresh_error_list = (lambda cls: cls.error_list = [])()
    refresh_data = (lambda cls: cls.data = { })()


class EnvBackwardFactor(object):
    wait_event = threading.Event()
    req_list_len = None
    data = { }
    error_list = []
    refresh_event = (lambda cls: cls.wait_event = threading.Event()cls.req_list_len = None)()
    refresh_error_list = (lambda cls: cls.error_list = [])()
    refresh_data = (lambda cls: cls.data = { })()


class EnvAdjFactor(object):
    wait_event = threading.Event()
    req_list_len = None
    data = { }
    error_list = []
    refresh_event = (lambda cls: cls.wait_event = threading.Event()cls.req_list_len = None)()
    refresh_error_list = (lambda cls: cls.error_list = [])()
    refresh_data = (lambda cls: cls.data = { })()


class EnvHistCodeList(object):
    wait_event = threading.Event()
    req_list_len = None
    data = { }
    error_list = []
    refresh_event = (lambda cls: cls.wait_event = threading.Event()cls.req_list_len = None)()
    refresh_error_list = (lambda cls: cls.error_list = [])()
    refresh_data = (lambda cls: cls.data = { })()


class EnvBlockTrading(object):
    wait_event = threading.Event()
    req_list_len = None
    data = { }
    error_list = []
    refresh_event = (lambda cls: cls.wait_event = threading.Event()cls.req_list_len = None)()
    refresh_error_list = (lambda cls: cls.error_list = [])()
    refresh_data = (lambda cls: cls.data = { })()


class EnvLongHuBang(object):
    wait_event = threading.Event()
    req_list_len = None
    data = { }
    error_list = []
    refresh_event = (lambda cls: cls.wait_event = threading.Event()cls.req_list_len = None)()
    refresh_error_list = (lambda cls: cls.error_list = [])()
    refresh_data = (lambda cls: cls.data = { })()


class EnvShareHolder(object):
    wait_event = threading.Event()
    req_list_len = None
    data = { }
    error_list = []
    refresh_event = (lambda cls: cls.wait_event = threading.Event()cls.req_list_len = None)()
    refresh_error_list = (lambda cls: cls.error_list = [])()
    refresh_data = (lambda cls: cls.data = { })()


class EnvOptionBasicInfo(object):
    wait_event = threading.Event()
    req_list_len = None
    data = { }
    error_list = []
    refresh_event = (lambda cls: cls.wait_event = threading.Event()cls.req_list_len = None)()
    refresh_error_list = (lambda cls: cls.error_list = [])()
    refresh_data = (lambda cls: cls.data = { })()


class EnvOptionStdCtrSpecs(object):
    wait_event = threading.Event()
    req_list_len = None
    data = { }
    error_list = []
    refresh_event = (lambda cls: cls.wait_event = threading.Event()cls.req_list_len = None)()
    refresh_error_list = (lambda cls: cls.error_list = [])()
    refresh_data = (lambda cls: cls.data = { })()


class EnvOptionMonCtrSpecs(object):
    wait_event = threading.Event()
    req_list_len = None
    data = { }
    error_list = []
    refresh_event = (lambda cls: cls.wait_event = threading.Event()cls.req_list_len = None)()
    refresh_error_list = (lambda cls: cls.error_list = [])()
    refresh_data = (lambda cls: cls.data = { })()


class EnvHolderNum(object):
    wait_event = threading.Event()
    req_list_len = None
    data = { }
    error_list = []
    refresh_event = (lambda cls: cls.wait_event = threading.Event()cls.req_list_len = None)()
    refresh_error_list = (lambda cls: cls.error_list = [])()
    refresh_data = (lambda cls: cls.data = { })()


class EnvProfitNotice(object):
    wait_event = threading.Event()
    req_list_len = None
    data = { }
    error_list = []
    refresh_event = (lambda cls: cls.wait_event = threading.Event()cls.req_list_len = None)()
    refresh_error_list = (lambda cls: cls.error_list = [])()
    refresh_data = (lambda cls: cls.data = { })()


class EnvProfitExcess(object):
    wait_event = threading.Event()
    req_list_len = None
    data = { }
    error_list = []
    refresh_event = (lambda cls: cls.wait_event = threading.Event()cls.req_list_len = None)()
    refresh_error_list = (lambda cls: cls.error_list = [])()
    refresh_data = (lambda cls: cls.data = { })()


class EnvHistStockStatus(object):
    wait_event = threading.Event()
    req_list_len = None
    data = { }
    error_list = []
    refresh_event = (lambda cls: cls.wait_event = threading.Event()cls.req_list_len = None)()
    refresh_error_list = (lambda cls: cls.error_list = [])()
    refresh_data = (lambda cls: cls.data = { })()


class EnvBalanceSheet(object):
    wait_event = threading.Event()
    req_list_len = None
    data = { }
    error_list = []
    refresh_event = (lambda cls: cls.wait_event = threading.Event()cls.req_list_len = None)()
    refresh_error_list = (lambda cls: cls.error_list = [])()
    refresh_data = (lambda cls: cls.data = { })()


class EnvEquityPledgeFreeze(object):
    wait_event = threading.Event()
    req_list_len = None
    data = { }
    error_list = []
    refresh_event = (lambda cls: cls.wait_event = threading.Event()cls.req_list_len = None)()
    refresh_error_list = (lambda cls: cls.error_list = [])()
    refresh_data = (lambda cls: cls.data = { })()


class EnvEquityRestricted(object):
    wait_event = threading.Event()
    req_list_len = None
    data = { }
    error_list = []
    refresh_event = (lambda cls: cls.wait_event = threading.Event()cls.req_list_len = None)()
    refresh_error_list = (lambda cls: cls.error_list = [])()
    refresh_data = (lambda cls: cls.data = { })()


class EnvStockBasic(object):
    wait_event = threading.Event()
    req_list_len = None
    data = { }
    error_list = []
    refresh_event = (lambda cls: cls.wait_event = threading.Event()cls.req_list_len = None)()
    refresh_error_list = (lambda cls: cls.error_list = [])()
    refresh_data = (lambda cls: cls.data = { })()


class EnvDividend(object):
    wait_event = threading.Event()
    req_list_len = None
    data = { }
    error_list = []
    refresh_event = (lambda cls: cls.wait_event = threading.Event()cls.req_list_len = None)()
    refresh_error_list = (lambda cls: cls.error_list = [])()
    refresh_data = (lambda cls: cls.data = { })()


class EnvRightIssue(object):
    wait_event = threading.Event()
    req_list_len = None
    data = { }
    error_list = []
    refresh_event = (lambda cls: cls.wait_event = threading.Event()cls.req_list_len = None)()
    refresh_error_list = (lambda cls: cls.error_list = [])()
    refresh_data = (lambda cls: cls.data = { })()


class EnvEquityStructure(object):
    wait_event = threading.Event()
    req_list_len = None
    data = { }
    error_list = []
    refresh_event = (lambda cls: cls.wait_event = threading.Event()cls.req_list_len = None)()
    refresh_error_list = (lambda cls: cls.error_list = [])()
    refresh_data = (lambda cls: cls.data = { })()


class EnvIncome(object):
    wait_event = threading.Event()
    req_list_len = None
    data = { }
    error_list = []
    refresh_event = (lambda cls: cls.wait_event = threading.Event()cls.req_list_len = None)()
    refresh_error_list = (lambda cls: cls.error_list = [])()
    refresh_data = (lambda cls: cls.data = { })()


class EnvCashFlow(object):
    wait_event = threading.Event()
    req_list_len = None
    data = { }
    error_list = []
    refresh_event = (lambda cls: cls.wait_event = threading.Event()cls.req_list_len = None)()
    refresh_error_list = (lambda cls: cls.error_list = [])()
    refresh_data = (lambda cls: cls.data = { })()


class EnvMarginDetail(object):
    wait_event = threading.Event()
    req_list_len = None
    data = { }
    error_list = []
    refresh_event = (lambda cls: cls.wait_event = threading.Event()cls.req_list_len = None)()
    refresh_error_list = (lambda cls: cls.error_list = [])()
    refresh_data = (lambda cls: cls.data = { })()


class EnvFundNav(object):
    wait_event = threading.Event()
    req_list_len = None
    data = { }
    error_list = []
    refresh_event = (lambda cls: cls.wait_event = threading.Event()cls.req_list_len = None)()
    refresh_error_list = (lambda cls: cls.error_list = [])()
    refresh_data = (lambda cls: cls.data = { })()


class EnvFundShare(object):
    wait_event = threading.Event()
    req_list_len = None
    data = { }
    error_list = []
    refresh_event = (lambda cls: cls.wait_event = threading.Event()cls.req_list_len = None)()
    refresh_error_list = (lambda cls: cls.error_list = [])()
    refresh_data = (lambda cls: cls.data = { })()


class EnvFundIopv(object):
    wait_event = threading.Event()
    req_list_len = None
    data = { }
    error_list = []
    refresh_event = (lambda cls: cls.wait_event = threading.Event()cls.req_list_len = None)()
    refresh_error_list = (lambda cls: cls.error_list = [])()
    refresh_data = (lambda cls: cls.data = { })()


class EnvIndexWeight(object):
    wait_event = threading.Event()
    req_list_len = None
    data = { }
    error_list = []
    refresh_event = (lambda cls: cls.wait_event = threading.Event()cls.req_list_len = None)()
    refresh_error_list = (lambda cls: cls.error_list = [])()
    refresh_data = (lambda cls: cls.data = { })()


class EnvIndexConstituent(object):
    wait_event = threading.Event()
    req_list_len = None
    data = { }
    error_list = []
    refresh_event = (lambda cls: cls.wait_event = threading.Event()cls.req_list_len = None)()
    refresh_error_list = (lambda cls: cls.error_list = [])()
    refresh_data = (lambda cls: cls.data = { })()


class EnvIndustryWeight(object):
    wait_event = threading.Event()
    req_list_len = None
    data = { }
    error_list = []
    refresh_event = (lambda cls: cls.wait_event = threading.Event()cls.req_list_len = None)()
    refresh_error_list = (lambda cls: cls.error_list = [])()
    refresh_data = (lambda cls: cls.data = { })()


class EnvIndustryConstituent(object):
    wait_event = threading.Event()
    req_list_len = None
    data = { }
    error_list = []
    refresh_event = (lambda cls: cls.wait_event = threading.Event()cls.req_list_len = None)()
    refresh_error_list = (lambda cls: cls.error_list = [])()
    refresh_data = (lambda cls: cls.data = { })()


class EnvIndustryDaily(object):
    wait_event = threading.Event()
    req_list_len = None
    data = { }
    error_list = []
    refresh_event = (lambda cls: cls.wait_event = threading.Event()cls.req_list_len = None)()
    refresh_error_list = (lambda cls: cls.error_list = [])()
    refresh_data = (lambda cls: cls.data = { })()


class EnvTreasuryYield(object):
    wait_event = threading.Event()
    req_list_len = None
    data = { }
    error_list = []
    refresh_event = (lambda cls: cls.wait_event = threading.Event()cls.req_list_len = None)()
    refresh_error_list = (lambda cls: cls.error_list = [])()
    refresh_data = (lambda cls: cls.data = { })()


class EnvKzzIssuance(object):
    wait_event = threading.Event()
    req_list_len = None
    data = { }
    error_list = []
    refresh_event = (lambda cls: cls.wait_event = threading.Event()cls.req_list_len = None)()
    refresh_error_list = (lambda cls: cls.error_list = [])()
    refresh_data = (lambda cls: cls.data = { })()


class EnvKzzShare(object):
    wait_event = threading.Event()
    req_list_len = None
    data = { }
    error_list = []
    refresh_event = (lambda cls: cls.wait_event = threading.Event()cls.req_list_len = None)()
    refresh_error_list = (lambda cls: cls.error_list = [])()
    refresh_data = (lambda cls: cls.data = { })()


class EnvKzzConv(object):
    wait_event = threading.Event()
    req_list_len = None
    data = { }
    error_list = []
    refresh_event = (lambda cls: cls.wait_event = threading.Event()cls.req_list_len = None)()
    refresh_error_list = (lambda cls: cls.error_list = [])()
    refresh_data = (lambda cls: cls.data = { })()


class EnvKzzConvChange(object):
    wait_event = threading.Event()
    req_list_len = None
    data = { }
    error_list = []
    refresh_event = (lambda cls: cls.wait_event = threading.Event()cls.req_list_len = None)()
    refresh_error_list = (lambda cls: cls.error_list = [])()
    refresh_data = (lambda cls: cls.data = { })()


class EnvKzzCorr(object):
    wait_event = threading.Event()
    req_list_len = None
    data = { }
    error_list = []
    refresh_event = (lambda cls: cls.wait_event = threading.Event()cls.req_list_len = None)()
    refresh_error_list = (lambda cls: cls.error_list = [])()
    refresh_data = (lambda cls: cls.data = { })()


class EnvKzzPutCallItem(object):
    wait_event = threading.Event()
    req_list_len = None
    data = { }
    error_list = []
    refresh_event = (lambda cls: cls.wait_event = threading.Event()cls.req_list_len = None)()
    refresh_error_list = (lambda cls: cls.error_list = [])()
    refresh_data = (lambda cls: cls.data = { })()


class EnvKzzCall(object):
    wait_event = threading.Event()
    req_list_len = None
    data = { }
    error_list = []
    refresh_event = (lambda cls: cls.wait_event = threading.Event()cls.req_list_len = None)()
    refresh_error_list = (lambda cls: cls.error_list = [])()
    refresh_data = (lambda cls: cls.data = { })()


class EnvKzzPut(object):
    wait_event = threading.Event()
    req_list_len = None
    data = { }
    error_list = []
    refresh_event = (lambda cls: cls.wait_event = threading.Event()cls.req_list_len = None)()
    refresh_error_list = (lambda cls: cls.error_list = [])()
    refresh_data = (lambda cls: cls.data = { })()


class EnvKzzPutExplanation(object):
    wait_event = threading.Event()
    req_list_len = None
    data = { }
    error_list = []
    refresh_event = (lambda cls: cls.wait_event = threading.Event()cls.req_list_len = None)()
    refresh_error_list = (lambda cls: cls.error_list = [])()
    refresh_data = (lambda cls: cls.data = { })()


class EnvKzzCallExplanation(object):
    wait_event = threading.Event()
    req_list_len = None
    data = { }
    error_list = []
    refresh_event = (lambda cls: cls.wait_event = threading.Event()cls.req_list_len = None)()
    refresh_error_list = (lambda cls: cls.error_list = [])()
    refresh_data = (lambda cls: cls.data = { })()


class EnvKzzSuspend(object):
    wait_event = threading.Event()
    req_list_len = None
    data = { }
    error_list = []
    refresh_event = (lambda cls: cls.wait_event = threading.Event()cls.req_list_len = None)()
    refresh_error_list = (lambda cls: cls.error_list = [])()
    refresh_data = (lambda cls: cls.data = { })()


class EnvAnnouncementStock(object):
    wait_event = threading.Event()
    req_list_len = None
    data = { }
    error_list = []
    refresh_event = (lambda cls: cls.wait_event = threading.Event()cls.req_list_len = None)()
    refresh_error_list = (lambda cls: cls.error_list = [])()
    refresh_data = (lambda cls: cls.data = { })()


class EnvAnnouncementFund(object):
    wait_event = threading.Event()
    req_list_len = None
    data = { }
    error_list = []
    refresh_event = (lambda cls: cls.wait_event = threading.Event()cls.req_list_len = None)()
    refresh_error_list = (lambda cls: cls.error_list = [])()
    refresh_data = (lambda cls: cls.data = { })()


class EnvAnnouncementBond(object):
    wait_event = threading.Event()
    req_list_len = None
    data = { }
    error_list = []
    refresh_event = (lambda cls: cls.wait_event = threading.Event()cls.req_list_len = None)()
    refresh_error_list = (lambda cls: cls.error_list = [])()
    refresh_data = (lambda cls: cls.data = { })()

