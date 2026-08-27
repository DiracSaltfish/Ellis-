# Source Generated with Decompyle++
# File: download_info_data.pyc (Python 3.12)

import copy
import os
import pandas as pd
import numpy as np
import tgw
from AmazingData.utils.data_transfer import date_to_datetime, datetime_to_int, date_split, date_str_to_int
from AmazingData.config.local_data_folder import LocalDataFolder
from AmazingData.utils.convert import get_tgw_type_code
from AmazingData.utils.save_get_data import save_data_to_hdf5, get_data_from_hdf5, save_data_to_pkl
from AmazingData.environment import QueryLock, QueryPara, EnvBlockTrading, EnvLongHuBang, EnvShareHolder, EnvProfitNotice, EnvProfitExcess, EnvHistCodeList, EnvBackwardFactor, EnvHistStockStatus, EnvBalanceSheet, EnvIncome, EnvCashFlow, EnvMarginDetail, EnvHolderNum, EnvEquityStructure, EnvRightIssue, EnvEquityRestricted, EnvEquityPledgeFreeze, EnvDividend, EnvStockBasic, EnvAdjFactor, EnvOptionBasicInfo, EnvOptionStdCtrSpecs, EnvOptionMonCtrSpecs, EnvFundShare, EnvFundNav, EnvFundIopv, EnvIndexWeight, EnvIndexConstituent, EnvIndustryWeight, EnvIndustryConstituent, EnvIndustryDaily, EnvTreasuryYield, EnvKzzIssuance, EnvKzzShare, EnvKzzConv, EnvKzzConvChange, EnvKzzCorr, EnvKzzPutCallItem, EnvKzzCall, EnvKzzPut, EnvKzzPutExplanation, EnvKzzCallExplanation, EnvKzzSuspend, EnvAnnouncementStock, EnvAnnouncementFund, EnvAnnouncementBond
from AmazingData.download_data.info_spi import HistCodeListSpi, BackwardFactorSpi, BlockTradingSpi, LongHuBangSpi, ShareHolderSpi, ProfitExcessSpi, ProfitNoticeSpi, HistStockStatusSpi, BalanceSheetSpi, IncomeSpi, CashFlowSpi, MarginDetailSpi, HolderNumSpi, EquityStructureSpi, RightIssueSpi, EquityRestrictedSpi, EnvEquityPledgeFreezeSpi, DividendSpi, StockBasicSpi, AdjFactorSpi, OptionBasicInfoSpi, OptionStdCtrSpecsSpi, OptionMonCtrSpecsSpi, FundShareSpi, FundNavSpi, FundIopvSpi, IndexWeightSpi, IndexConstituentSpi, IndustryWeightSpi, IndustryConstituentSpi, IndustryDailySpi, TreasuryYieldSpi, KzzIssuanceSpi, KzzShareSpi, KzzConvSpi, KzzConvChangeSpi, KzzCorrSpi, KzzPutCallItemSpi, KzzCallSpi, KzzPutSpi, KzzPutExplanationSpi, KzzCallExplanationSpi, KzzSuspendSpi, AnnouncementStockSpi, AnnouncementFundSpi, AnnouncementBondSpi

class DownloadInfoData(object):
    
    def __init__(self, local_path):
        self.local_path = local_path
        self.lock = QueryLock.query_lock

    
    def download_block_trading(self, code_list, **kwargs):
        '''
        大宗交易
        '''
        if 'begin_date' in kwargs and 'end_date' in kwargs:
            begin_date = str(kwargs['begin_date'])
            end_date = str(kwargs['end_date'])
        else:
            begin_date = None
            end_date = '20980101'
        self.lock.acquire()
        folder_name = LocalDataFolder.INFODATA.value + '/' + LocalDataFolder.Block_Trading.value
        path = self.local_path + folder_name + '/'
        block_trading_local = pd.DataFrame()
    # WARNING: Decompyle incomplete

    
    def download_longhubang(self, code_list, **kwargs):
        '''
        龙虎榜
        '''
        if 'begin_date' in kwargs and 'end_date' in kwargs:
            begin_date = str(kwargs['begin_date'])
            end_date = str(kwargs['end_date'])
        else:
            begin_date = None
            end_date = '20980101'
        self.lock.acquire()
        folder_name = LocalDataFolder.INFODATA.value + '/' + LocalDataFolder.Long_Hu_Bang.value
        path = self.local_path + folder_name + '/'
        long_hu_bang_local = pd.DataFrame()
    # WARNING: Decompyle incomplete

    
    def download_share_holder(self, code_list, **kwargs):
        '''
        十大股东
        '''
        if 'begin_date' in kwargs and 'end_date' in kwargs:
            begin_date = str(kwargs['begin_date'])
            end_date = str(kwargs['end_date'])
        else:
            begin_date = None
            end_date = '20980101'
        self.lock.acquire()
        folder_name = LocalDataFolder.INFODATA.value + '/' + LocalDataFolder.Share_Holder.value
        path = self.local_path + folder_name + '/'
        share_holder_local = pd.DataFrame()
    # WARNING: Decompyle incomplete

    
    def download_holder_num(self, code_list, **kwargs):
        '''
        股东户数
        '''
        if 'begin_date' in kwargs and 'end_date' in kwargs:
            begin_date = str(kwargs['begin_date'])
            end_date = str(kwargs['end_date'])
        else:
            begin_date = None
            end_date = '20980101'
        self.lock.acquire()
        folder_name = LocalDataFolder.INFODATA.value + '/' + LocalDataFolder.Holder_Num.value
        path = self.local_path + folder_name + '/'
        holder_num_local = pd.DataFrame()
    # WARNING: Decompyle incomplete

    
    def download_option_basic_info(self, code_list):
        '''
        期权基本资料
        '''
        self.lock.acquire()
        folder_name = LocalDataFolder.INFODATA.value + '/' + LocalDataFolder.Option_Basic_Info.value
        path = self.local_path + folder_name + '/'
        option_basic_info_local = pd.DataFrame()
    # WARNING: Decompyle incomplete

    
    def download_option_std_ctr_specs(self, code_list):
        '''
        期权标准合约属性
        '''
        self.lock.acquire()
        folder_name = LocalDataFolder.INFODATA.value + '/' + LocalDataFolder.Option_Std_Ctr_Specs.value
        path = self.local_path + folder_name + '/'
        option_std_ctr_specs_local = pd.DataFrame()
    # WARNING: Decompyle incomplete

    
    def download_option_mon_ctr_specs_change(self, code_list):
        '''
        期权月合约属性
        '''
        self.lock.acquire()
        folder_name = LocalDataFolder.INFODATA.value + '/' + LocalDataFolder.Option_Mon_Ctr_Specs.value
        path = self.local_path + folder_name + '/'
        option_mon_ctr_specs_local = pd.DataFrame()
    # WARNING: Decompyle incomplete

    
    def download_dividend(self, code_list, **kwargs):
        '''
        A股分红
        '''
        if 'begin_date' in kwargs and 'end_date' in kwargs:
            begin_date = str(kwargs['begin_date'])
            end_date = str(kwargs['end_date'])
        else:
            begin_date = None
            end_date = '20980101'
        self.lock.acquire()
        folder_name = LocalDataFolder.INFODATA.value + '/' + LocalDataFolder.Dividend.value
        path = self.local_path + folder_name + '/'
        dividend_local = pd.DataFrame()
    # WARNING: Decompyle incomplete

    
    def download_right_issue(self, code_list, **kwargs):
        '''
        A股配股
        '''
        if 'begin_date' in kwargs and 'end_date' in kwargs:
            begin_date = str(kwargs['begin_date'])
            end_date = str(kwargs['end_date'])
        else:
            begin_date = None
            end_date = '20980101'
        self.lock.acquire()
        folder_name = LocalDataFolder.INFODATA.value + '/' + LocalDataFolder.Right_Issue.value
        path = self.local_path + folder_name + '/'
        right_issue_local = pd.DataFrame()
    # WARNING: Decompyle incomplete

    
    def download_equity_structure(self, code_list, **kwargs):
        '''
        股本结构
        '''
        if 'begin_date' in kwargs and 'end_date' in kwargs:
            begin_date = str(kwargs['begin_date'])
            end_date = str(kwargs['end_date'])
        else:
            begin_date = None
            end_date = '20980101'
        self.lock.acquire()
        folder_name = LocalDataFolder.INFODATA.value + '/' + LocalDataFolder.Equity_Structure.value
        path = self.local_path + folder_name + '/'
        equity_structure_local = pd.DataFrame()
    # WARNING: Decompyle incomplete

    
    def download_equity_restricted(self, code_list, **kwargs):
        '''
        限售股解禁
        '''
        if 'begin_date' in kwargs and 'end_date' in kwargs:
            begin_date = str(kwargs['begin_date'])
            end_date = str(kwargs['end_date'])
        else:
            begin_date = None
            end_date = '20980101'
        self.lock.acquire()
        folder_name = LocalDataFolder.INFODATA.value + '/' + LocalDataFolder.Equity_Restricted.value
        path = self.local_path + folder_name + '/'
        equity_restricted = { }
        local_data_end_date = { }
    # WARNING: Decompyle incomplete

    
    def download_stock_basic(self, code_list):
        '''
        证券基础信息
        '''
        self.lock.acquire()
        all_code_list = code_list
        
        def get_stock_basic(code_list):
            EnvStockBasic.refresh_error_list()
            EnvStockBasic.req_list_len = len(code_list)
            for code in code_list:
                info_data_spi = StockBasicSpi(code)
                task_id = tgw.GetTaskID()
                tgw.SetThirdInfoParam(task_id, 'function_id', 'A010010001')
                tgw.SetThirdInfoParam(task_id, 'market_code', code)
                tgw.QueryThirdInfo(task_id, query_spi = info_data_spi.OnResponse)
            EnvStockBasic.wait_event.wait()
            req_data = copy.deepcopy(EnvStockBasic.data)
            EnvStockBasic.refresh_event()
            if len(EnvStockBasic.error_list) == 0:
                EnvStockBasic.refresh_data()
            return req_data

    # WARNING: Decompyle incomplete

    
    def download_equity_pledge_freeze(self, code_list, **kwargs):
        '''
        股权质押、冻结信息
        '''
        if 'begin_date' in kwargs and 'end_date' in kwargs:
            begin_date = str(kwargs['begin_date'])
            end_date = str(kwargs['end_date'])
        else:
            begin_date = None
            end_date = '20980101'
        self.lock.acquire()
        folder_name = LocalDataFolder.INFODATA.value + '/' + LocalDataFolder.Equity_Pledge_Freeze.value
        path = self.local_path + folder_name + '/'
        equity_pledge_freeze = { }
        local_data_end_date = { }
    # WARNING: Decompyle incomplete

    
    def download_profit_notice(self, code_list, **kwargs):
        '''
        业绩预告
        '''
        if 'begin_date' in kwargs and 'end_date' in kwargs:
            begin_date = str(kwargs['begin_date'])
            end_date = str(kwargs['end_date'])
        else:
            begin_date = None
            end_date = '20980101'
        self.lock.acquire()
        folder_name = LocalDataFolder.INFODATA.value + '/' + LocalDataFolder.Profit_Notice.value
        path = self.local_path + folder_name + '/'
        profit_notice_local = pd.DataFrame()
    # WARNING: Decompyle incomplete

    
    def download_profit_express(self, code_list, **kwargs):
        '''
        业绩预告
        '''
        if 'begin_date' in kwargs and 'end_date' in kwargs:
            begin_date = str(kwargs['begin_date'])
            end_date = str(kwargs['end_date'])
        else:
            begin_date = None
            end_date = '20980101'
        self.lock.acquire()
        folder_name = LocalDataFolder.INFODATA.value + '/' + LocalDataFolder.Profit_Express.value
        path = self.local_path + folder_name + '/'
        profit_express_local = pd.DataFrame()
    # WARNING: Decompyle incomplete

    
    def download_hist_stock_status(self, code_list, **kwargs):
        '''
        历史证券信息
        '''
        if 'begin_date' in kwargs and 'end_date' in kwargs:
            begin_date = str(kwargs['begin_date'])
            end_date = str(kwargs['end_date'])
        else:
            begin_date = None
            end_date = '20980101'
        
        def get_hist_stock_status(code_list, local_data_end_date, query_end_date, function_id = ('A010060002',)):
            EnvHistStockStatus.refresh_error_list()
            EnvHistStockStatus.req_list_len = len(code_list)
            for code in code_list:
                info_data_spi = HistStockStatusSpi(code)
                task_id = tgw.GetTaskID()
                tgw.SetThirdInfoParam(task_id, 'function_id', function_id)
                tgw.SetThirdInfoParam(task_id, 'market_code', code)
                tgw.SetThirdInfoParam(task_id, 'start_date', local_data_end_date[code])
                tgw.SetThirdInfoParam(task_id, 'end_date', query_end_date)
                tgw.QueryThirdInfo(task_id, query_spi = info_data_spi.OnResponse)
            EnvHistStockStatus.wait_event.wait()
            req_data = copy.deepcopy(EnvHistStockStatus.data)
            EnvHistStockStatus.refresh_event()
            if len(EnvHistStockStatus.error_list) == 0:
                EnvHistStockStatus.refresh_data()
            return req_data

        self.lock.acquire()
        folder_name = LocalDataFolder.BASEDATA.value + '/' + LocalDataFolder.Hist_Stock_Status.value
        path = self.local_path + folder_name + '/'
        hist_stock_status = { }
        local_data_end_date = { }
    # WARNING: Decompyle incomplete

    
    def download_balance_sheet(self, code_list, **kwargs):
        '''
        资产负债表
        '''
        if 'begin_date' in kwargs and 'end_date' in kwargs:
            begin_date = str(kwargs['begin_date'])
            end_date = str(kwargs['end_date'])
        else:
            begin_date = None
            end_date = '20980101'
        self.lock.acquire()
        folder_name = LocalDataFolder.INFODATA.value + '/' + LocalDataFolder.Balance_Sheet.value
        path = self.local_path + folder_name + '/'
        balance_sheet = { }
        local_data_end_date = { }
    # WARNING: Decompyle incomplete

    
    def download_income(self, code_list, **kwargs):
        '''
        利润表
        '''
        if 'begin_date' in kwargs and 'end_date' in kwargs:
            begin_date = str(kwargs['begin_date'])
            end_date = str(kwargs['end_date'])
        else:
            begin_date = None
            end_date = '20980101'
        self.lock.acquire()
        folder_name = LocalDataFolder.INFODATA.value + '/' + LocalDataFolder.Income.value
        path = self.local_path + folder_name + '/'
        income = { }
        local_data_end_date = { }
    # WARNING: Decompyle incomplete

    
    def download_cash_flow(self, code_list, **kwargs):
        '''
        现金流量表
        '''
        if 'begin_date' in kwargs and 'end_date' in kwargs:
            begin_date = str(kwargs['begin_date'])
            end_date = str(kwargs['end_date'])
        else:
            begin_date = None
            end_date = '20980101'
        self.lock.acquire()
        folder_name = LocalDataFolder.INFODATA.value + '/' + LocalDataFolder.Cash_Flow.value
        path = self.local_path + folder_name + '/'
        cash_flow = { }
        local_data_end_date = { }
    # WARNING: Decompyle incomplete

    
    def download_hist_code_list(self, calendar):
        '''
        历史代码列表，时序数据库可查的所有品种
        '''
        self.lock.acquire()
        folder_name = LocalDataFolder.BASEDATA.value + '/' + LocalDataFolder.HIST_CODE_LIST.value
        path = self.local_path + folder_name + '/'
        local_data_calendar_list = []
    # WARNING: Decompyle incomplete

    
    def download_backward_factor(self, code_list, calendar):
        '''
        后复权因子
        '''
        self.lock.acquire()
        folder_name = LocalDataFolder.BASEDATA.value + '/' + LocalDataFolder.BACKWARD_FACTOR.value
        path = self.local_path + folder_name + '/'
        local_code_list = []
    # WARNING: Decompyle incomplete

    
    def download_adj_factor(self, code_list, calendar, is_local = (False,)):
        '''
        单次复权因子
        '''
        self.lock.acquire()
        folder_name = LocalDataFolder.BASEDATA.value + '/' + LocalDataFolder.ADJ_FACTOR.value
        path = self.local_path + folder_name + '/'
        local_code_list = []
    # WARNING: Decompyle incomplete

    
    def download_margin_detail(self, code_list, **kwargs):
        '''
        融资融券交易明细
        '''
        if 'begin_date' in kwargs and 'end_date' in kwargs:
            begin_date = str(kwargs['begin_date'])
            end_date = str(kwargs['end_date'])
        else:
            begin_date = None
            end_date = '20980101'
        self.lock.acquire()
        folder_name = LocalDataFolder.INFODATA.value + '/' + LocalDataFolder.Margin_Detail.value
        path = self.local_path + folder_name + '/'
        margin_detail = { }
        local_data_end_date = { }
    # WARNING: Decompyle incomplete

    
    def download_margin_summary(self, **kwargs):
        '''
        融资融券成交汇总
        '''
        if 'begin_date' in kwargs and 'end_date' in kwargs:
            begin_date = str(kwargs['begin_date'])
            end_date = str(kwargs['end_date'])
        else:
            begin_date = None
            end_date = '20980101'
        self.lock.acquire()
        folder_name = LocalDataFolder.INFODATA.value + '/' + LocalDataFolder.Margin_Summary.value
        path = self.local_path + folder_name + '/'
        margin_summary_local = pd.DataFrame()
    # WARNING: Decompyle incomplete

    
    def download_bj_code_mapping(self):
        '''
        北交所2025年4月22日，新旧代码更换
        '''
        folder_name = LocalDataFolder.BASEDATA.value + '/' + LocalDataFolder.BJ_Code_Mapping.value
        path = self.local_path + folder_name + '/'
        task_id = tgw.GetTaskID()
        tgw.SetThirdInfoParam(task_id, 'function_id', 'A010010010')
        req_data = tgw.QueryThirdInfo(task_id)
        result_df = req_data[0]
        if not result_df.empty:
            save_data_to_hdf5(path, LocalDataFolder.BJ_Code_Mapping.value, result_df)
        return result_df

    
    def download_fund_share(self, code_list, **kwargs):
        '''
        基金份额
        '''
        if 'begin_date' in kwargs and 'end_date' in kwargs:
            begin_date = str(kwargs['begin_date'])
            end_date = str(kwargs['end_date'])
        else:
            begin_date = None
            end_date = '20980101'
        self.lock.acquire()
        folder_name = LocalDataFolder.INFODATA.value + '/' + LocalDataFolder.Fund_Share.value
        path = self.local_path + folder_name + '/'
        fund_share = { }
        local_data_end_date = { }
    # WARNING: Decompyle incomplete

    
    def download_fund_nav(self, code_list, **kwargs):
        '''
        基金净值
        '''
        if 'begin_date' in kwargs and 'end_date' in kwargs:
            begin_date = str(kwargs['begin_date'])
            end_date = str(kwargs['end_date'])
        else:
            begin_date = None
            end_date = '20980101'
        self.lock.acquire()
        folder_name = LocalDataFolder.INFODATA.value + '/' + LocalDataFolder.Fund_NAV.value
        path = self.local_path + folder_name + '/'
        fund_nav = { }
        local_data_end_date = { }
    # WARNING: Decompyle incomplete

    
    def download_fund_iopv(self, code_list, **kwargs):
        '''
        基金iopv
        '''
        if 'begin_date' in kwargs and 'end_date' in kwargs:
            begin_date = str(kwargs['begin_date'])
            end_date = str(kwargs['end_date'])
        else:
            begin_date = None
            end_date = '20300101'
        self.lock.acquire()
        folder_name = LocalDataFolder.INFODATA.value + '/' + LocalDataFolder.Fund_Iopv.value
        path = self.local_path + folder_name + '/'
        fund_iopv = { }
        local_data_end_date = { }
    # WARNING: Decompyle incomplete

    
    def download_index_weight(self, code_list, **kwargs):
        '''
        指数权重
        '''
        if 'begin_date' in kwargs and 'end_date' in kwargs:
            begin_date = str(kwargs['begin_date'])
            end_date = str(kwargs['end_date'])
        else:
            begin_date = None
            end_date = str(datetime_to_int() + 10000)
        self.lock.acquire()
        folder_name = LocalDataFolder.INFODATA.value + '/' + LocalDataFolder.Index_Weight.value
        path = self.local_path + folder_name + '/'
        index_weight = { }
        local_data_end_date = { }
    # WARNING: Decompyle incomplete

    
    def download_index_constituent(self, code_list):
        '''
        指数成分股
        '''
        self.lock.acquire()
        folder_name = LocalDataFolder.INFODATA.value + '/' + LocalDataFolder.Index_Constituent.value
        path = self.local_path + folder_name + '/'
        all_code_list = code_list
        
        def get_index_constituent(code_list):
            EnvIndexConstituent.refresh_error_list()
            EnvIndexConstituent.req_list_len = len(code_list)
            for code in code_list:
                info_data_spi = IndexConstituentSpi(code)
                task_id = tgw.GetTaskID()
                tgw.SetThirdInfoParam(task_id, 'function_id', 'A010200002')
                tgw.SetThirdInfoParam(task_id, 'index_code', code)
                tgw.QueryThirdInfo(task_id, query_spi = info_data_spi.OnResponse)
            EnvIndexConstituent.wait_event.wait()
            req_data = copy.deepcopy(EnvIndexConstituent.data)
            EnvIndexConstituent.refresh_event()
            if len(EnvIndexConstituent.error_list) == 0:
                EnvIndexConstituent.refresh_data()
            return req_data

    # WARNING: Decompyle incomplete

    
    def download_industry_base_info(self):
        '''
        行业指数分类标准
        '''
        folder_name = LocalDataFolder.BASEDATA.value + '/' + LocalDataFolder.Industry_Base_Info.value
        path = self.local_path + folder_name + '/'
        task_id = tgw.GetTaskID()
        tgw.SetThirdInfoParam(task_id, 'function_id', 'A010200006')
        req_data = tgw.QueryThirdInfo(task_id)
        result_df = req_data[0]
        result_df['INDEX_CODE'] = result_df['INDEX_CODE'] + '.SI'
        if not result_df.empty:
            save_data_to_hdf5(path, LocalDataFolder.Industry_Base_Info.value, result_df)
        return result_df

    
    def download_industry_weight(self, code_list, **kwargs):
        '''
        行业权重
        '''
        if 'begin_date' in kwargs and 'end_date' in kwargs:
            begin_date = str(kwargs['begin_date'])
            end_date = str(kwargs['end_date'])
        else:
            begin_date = None
            end_date = str(datetime_to_int() + 10000)
        self.lock.acquire()
        folder_name = LocalDataFolder.INFODATA.value + '/' + LocalDataFolder.Industry_Weight.value
        path = self.local_path + folder_name + '/'
        industry_weight = { }
        local_data_end_date = { }
    # WARNING: Decompyle incomplete

    
    def download_industry_constituent(self, code_list):
        '''
        行业成分股
        '''
        self.lock.acquire()
        folder_name = LocalDataFolder.INFODATA.value + '/' + LocalDataFolder.Industry_Constituent.value
        path = self.local_path + folder_name + '/'
        all_code_list = code_list
        
        def get_industry_constituent(code_list):
            EnvIndustryConstituent.refresh_error_list()
            EnvIndustryConstituent.req_list_len = len(code_list)
            for code in code_list:
                info_data_spi = IndustryConstituentSpi(code)
                task_id = tgw.GetTaskID()
                tgw.SetThirdInfoParam(task_id, 'function_id', 'A010200003')
                tgw.SetThirdInfoParam(task_id, 'index_code', code)
                tgw.QueryThirdInfo(task_id, query_spi = info_data_spi.OnResponse)
            EnvIndustryConstituent.wait_event.wait()
            req_data = copy.deepcopy(EnvIndustryConstituent.data)
            EnvIndustryConstituent.refresh_event()
            if len(EnvIndustryConstituent.error_list) == 0:
                EnvIndustryConstituent.refresh_data()
            return req_data

    # WARNING: Decompyle incomplete

    
    def download_industry_daily(self, code_list, **kwargs):
        '''
        行业日行情
        '''
        if 'begin_date' in kwargs and 'end_date' in kwargs:
            begin_date = str(kwargs['begin_date'])
            end_date = str(kwargs['end_date'])
        else:
            begin_date = None
            end_date = '20980101'
        self.lock.acquire()
        folder_name = LocalDataFolder.INFODATA.value + '/' + LocalDataFolder.Industry_Daily.value
        path = self.local_path + folder_name + '/'
        industry_daily = { }
        local_data_end_date = { }
    # WARNING: Decompyle incomplete

    
    def download_treasury_yield(self, code_list, **kwargs):
        '''
        国债收益率
        '''
        if 'begin_date' in kwargs and 'end_date' in kwargs:
            begin_date = str(kwargs['begin_date'])
            end_date = str(kwargs['end_date'])
        else:
            begin_date = None
            end_date = '20980101'
        self.lock.acquire()
        folder_name = LocalDataFolder.INFODATA.value + '/' + LocalDataFolder.Treasury_Yield.value
        path = self.local_path + folder_name + '/'
        treasury_yield = { }
        local_data_end_date = { }
    # WARNING: Decompyle incomplete

    
    def download_kzz_issuance(self, code_list):
        '''
        可转债发行
        '''
        self.lock.acquire()
        folder_name = LocalDataFolder.INFODATA.value + '/' + LocalDataFolder.Kzz.value
        path = self.local_path + folder_name + '/'
        kzz_issuance_local = pd.DataFrame()
    # WARNING: Decompyle incomplete

    
    def download_kzz_share(self, code_list):
        '''
        可转债份额
        '''
        self.lock.acquire()
        folder_name = LocalDataFolder.INFODATA.value + '/' + LocalDataFolder.Kzz.value
        path = self.local_path + folder_name + '/'
        kzz_share_local = pd.DataFrame()
    # WARNING: Decompyle incomplete

    
    def download_kzz_conv(self, code_list):
        '''
        可转债转股数据
        '''
        self.lock.acquire()
        folder_name = LocalDataFolder.INFODATA.value + '/' + LocalDataFolder.Kzz.value
        path = self.local_path + folder_name + '/'
        kzz_conv_local = pd.DataFrame()
    # WARNING: Decompyle incomplete

    
    def download_kzz_conv_change(self, code_list):
        '''
        可转债转股变动数据
        '''
        self.lock.acquire()
        folder_name = LocalDataFolder.INFODATA.value + '/' + LocalDataFolder.Kzz.value
        path = self.local_path + folder_name + '/'
        kzz_conv_change_local = pd.DataFrame()
    # WARNING: Decompyle incomplete

    
    def download_kzz_corr(self, code_list):
        '''
        可转债修正数据
        '''
        self.lock.acquire()
        folder_name = LocalDataFolder.INFODATA.value + '/' + LocalDataFolder.Kzz.value
        path = self.local_path + folder_name + '/'
        kzz_corr_local = pd.DataFrame()
    # WARNING: Decompyle incomplete

    
    def download_kzz_call(self, code_list):
        '''
        可转债赎回数据
        '''
        self.lock.acquire()
        folder_name = LocalDataFolder.INFODATA.value + '/' + LocalDataFolder.Kzz.value
        path = self.local_path + folder_name + '/'
        kzz_call_local = pd.DataFrame()
    # WARNING: Decompyle incomplete

    
    def download_kzz_put(self, code_list):
        '''
        可转债回售数据
        '''
        self.lock.acquire()
        folder_name = LocalDataFolder.INFODATA.value + '/' + LocalDataFolder.Kzz.value
        path = self.local_path + folder_name + '/'
        kzz_put_local = pd.DataFrame()
    # WARNING: Decompyle incomplete

    
    def download_kzz_put_call_item(self, code_list):
        '''
        可转债回售赎回条款
        '''
        self.lock.acquire()
        folder_name = LocalDataFolder.INFODATA.value + '/' + LocalDataFolder.Kzz.value
        path = self.local_path + folder_name + '/'
        kzz_put_call_item_local = pd.DataFrame()
    # WARNING: Decompyle incomplete

    
    def download_kzz_put_explanation(self, code_list):
        '''
        可转债回售条款执行说明
        '''
        self.lock.acquire()
        folder_name = LocalDataFolder.INFODATA.value + '/' + LocalDataFolder.Kzz.value
        path = self.local_path + folder_name + '/'
        kzz_put_explanation_local = pd.DataFrame()
    # WARNING: Decompyle incomplete

    
    def download_kzz_call_explanation(self, code_list):
        '''
        可转债赎回条款执行说明
        '''
        self.lock.acquire()
        folder_name = LocalDataFolder.INFODATA.value + '/' + LocalDataFolder.Kzz.value
        path = self.local_path + folder_name + '/'
        kzz_call_explanation_local = pd.DataFrame()
    # WARNING: Decompyle incomplete

    
    def download_kzz_suspend(self, code_list):
        '''
        可转债停复牌信息
        '''
        self.lock.acquire()
        folder_name = LocalDataFolder.INFODATA.value + '/' + LocalDataFolder.Kzz.value
        path = self.local_path + folder_name + '/'
        kzz_suspend_local = pd.DataFrame()
    # WARNING: Decompyle incomplete

    
    def download_announcement_stock(self, code_list, **kwargs):
        '''
        上市公司公告列表
        '''
        if 'begin_date' in kwargs and 'end_date' in kwargs:
            begin_date = str(kwargs['begin_date'])
            end_date = str(kwargs['end_date'])
        else:
            begin_date = None
            end_date = '20980101'
        self.lock.acquire()
        folder_name = LocalDataFolder.INFODATA.value + '/' + LocalDataFolder.Announcement.value
        path = self.local_path + folder_name + '/'
        announcement_stock_local = pd.DataFrame()
    # WARNING: Decompyle incomplete

    
    def download_announcement_fund(self, code_list, **kwargs):
        '''
        基金公告列表
        '''
        if 'begin_date' in kwargs and 'end_date' in kwargs:
            begin_date = str(kwargs['begin_date'])
            end_date = str(kwargs['end_date'])
        else:
            begin_date = None
            end_date = '20980101'
        self.lock.acquire()
        folder_name = LocalDataFolder.INFODATA.value + '/' + LocalDataFolder.Announcement.value
        path = self.local_path + folder_name + '/'
        announcement_fund_local = pd.DataFrame()
    # WARNING: Decompyle incomplete

    
    def download_announcement_bond(self, code_list, **kwargs):
        '''
        债券公告列表
        '''
        if 'begin_date' in kwargs and 'end_date' in kwargs:
            begin_date = str(kwargs['begin_date'])
            end_date = str(kwargs['end_date'])
        else:
            begin_date = None
            end_date = '20980101'
        self.lock.acquire()
        folder_name = LocalDataFolder.INFODATA.value + '/' + LocalDataFolder.Announcement.value
        path = self.local_path + folder_name + '/'
        announcement_bond_local = pd.DataFrame()
    # WARNING: Decompyle incomplete


if __name__ == '__main__':
    return None
