# Source Generated with Decompyle++
# File: info_data.pyc (Python 3.12)

import os
import pandas as pd
import tgw
from AmazingData.download_data.download_info_data import DownloadInfoData
from AmazingData.config.local_data_folder import LocalDataFolder
from AmazingData.utils.save_get_data import get_data_from_hdf5
from AmazingData.utils.data_transfer import date_to_datetime
from AmazingData.utils.security_type import is_security_type

class InfoData(object):
    
    def __init__(self):
        self.block_trading = { }
        self.long_hu_bang = { }
        self.share_holder = { }
        self.holder_num = { }
        self.equity_structure = { }
        self.equity_restricted = { }
        self.stock_basic = pd.DataFrame()
        self.equity_pledge_freeze = { }
        self.profit_notice = { }
        self.profit_express = { }
        self.history_stock_status = { }
        self.balance_sheet = { }
        self.income = { }
        self.cash_flow = { }
        self.margin_detail = { }
        self.margin_summary = { }
        self.bj_code_mapping = { }
        self.right_issue = { }
        self.dividend = { }
        self.option_basic_info = { }
        self.option_std_ctr_specs = { }
        self.option_mon_ctr_specs = { }
        self.fund_share = { }
        self.fund_nav = { }
        self.fund_iopv = { }
        self.index_weight = { }
        self.index_constituent = { }
        self.industry_base_info = { }
        self.industry_weight = { }
        self.industry_constituent = { }
        self.industry_daily = { }
        self.treasury_yield = { }
        self.kzz_issuance = pd.DataFrame()
        self.kzz_share = pd.DataFrame()
        self.kzz_conv = pd.DataFrame()
        self.kzz_conv_change = pd.DataFrame()
        self.kzz_corr = pd.DataFrame()
        self.kzz_put_call_item = pd.DataFrame()
        self.kzz_call = pd.DataFrame()
        self.kzz_put = pd.DataFrame()
        self.kzz_put_explanation = pd.DataFrame()
        self.kzz_call_explanation = pd.DataFrame()
        self.kzz_suspend = pd.DataFrame()
        self.announcement_stock_list_df = pd.DataFrame()
        self.announcement_stock_pdf_path = { }
        self.announcement_stock_list_tag_df = pd.DataFrame()
        self.announcement_fund_list_df = pd.DataFrame()
        self.announcement_fund_pdf_path = { }
        self.announcement_fund_list_tag_df = pd.DataFrame()
        self.announcement_bond_list_df = pd.DataFrame()
        self.announcement_bond_pdf_path = { }
        self.announcement_bond_list_tag_df = pd.DataFrame()

    
    def get_block_trading(self, code_list, local_path, is_local = ('D://AmazingData_local_data//', True), **kwargs):
        download_info_data_object = DownloadInfoData(local_path)
    # WARNING: Decompyle incomplete

    
    def get_long_hu_bang(self, code_list, local_path, is_local = ('D://AmazingData_local_data//', True), **kwargs):
        download_info_data_object = DownloadInfoData(local_path)
    # WARNING: Decompyle incomplete

    
    def get_share_holder(self, code_list, local_path, is_local = ('D://AmazingData_local_data//', True), **kwargs):
        download_info_data_object = DownloadInfoData(local_path)
    # WARNING: Decompyle incomplete

    
    def get_option_basic_info(self, code_list, local_path, is_local = ('D://AmazingData_local_data//', True)):
        download_info_data_object = DownloadInfoData(local_path)
        
        try:
            if is_local:
                folder_name = LocalDataFolder.INFODATA.value + '/' + LocalDataFolder.Option_Basic_Info.value
                path = local_path + folder_name + '/'
                self.option_basic_info = get_data_from_hdf5(path, LocalDataFolder.Option_Basic_Info.value)
                self.option_basic_info = self.option_basic_info[[
                    'MARKET_CODE',
                    'CONTRACT_FULL_NAME',
                    'CONTRACT_TYPE',
                    'DELIVERY_MONTH',
                    'EXPIRY_DATE',
                    'EXERCISE_PRICE',
                    'EXERCISE_END_DATE',
                    'START_TRADE_DATE',
                    'LISTING_REF_PRICE',
                    'LAST_TRADE_DATE',
                    'EXCHANGE_CODE',
                    'DELIVERY_DATE',
                    'CONTRACT_UNIT',
                    'IS_TRADE',
                    'EXCHANGE_SHORT_NAME',
                    'CONTRACT_ADJUST_FLAG']]
                self.option_basic_info = self.option_basic_info[self.option_basic_info['MARKET_CODE'].isin(code_list)]
                return self.option_basic_info
            self.option_basic_info = None.download_option_basic_info(code_list)
            return self.option_basic_info
        except FileNotFoundError:
            self.option_basic_info = download_info_data_object.download_option_basic_info(code_list)
            return self.option_basic_info


    
    def get_option_std_ctr_specs(self, code_list, local_path, is_local = ('D://AmazingData_local_data//', True)):
        download_info_data_object = DownloadInfoData(local_path)
        
        try:
            if is_local:
                folder_name = LocalDataFolder.INFODATA.value + '/' + LocalDataFolder.Option_Std_Ctr_Specs.value
                path = local_path + folder_name + '/'
                self.option_std_ctr_specs = get_data_from_hdf5(path, LocalDataFolder.Option_Std_Ctr_Specs.value)
                columns_list = [
                    'MARKET_CODE',
                    'EXERCISE_DATE',
                    'CONTRACT_UNIT',
                    'POSITION_DECLARE_MIN',
                    'QUOTE_CURRENCY_UNIT',
                    'LAST_TRADING_DATE',
                    'POSITION_LIMIT',
                    'DELIST_DATE',
                    'NOTIONAL_VALUE',
                    'EXERCISE_METHOD',
                    'DELIVERY_METHOD',
                    'SETTLEMENT_MONTH',
                    'TRADING_FEE',
                    'EXCHANGE_NAME',
                    'OPTION_EN_NAME',
                    'CONTRACT_VALUE',
                    'IS_SIMULATION',
                    'CONTRACT_UNIT_DIMENSION',
                    'OPTION_STRIKE_PRICE',
                    'IS_SIMULATION_TRADE',
                    'LISTED_DATE',
                    'OPTION_NAME',
                    'PREMIUM',
                    'OPTION_TYPE',
                    'TRADING_HOURS_DESC',
                    'FINAL_SETTLEMENT_DATE',
                    'FINAL_SETTLEMENT_PRICE',
                    'MIN_PRICE_UNIT',
                    'CONTRACT_MULTIPLIER']
                self.option_std_ctr_specs = self.option_std_ctr_specs[columns_list]
                self.option_std_ctr_specs = self.option_std_ctr_specs[self.option_std_ctr_specs['MARKET_CODE'].isin(code_list)]
                return self.option_std_ctr_specs
            self.option_std_ctr_specs = None.download_option_std_ctr_specs(code_list)
            return self.option_std_ctr_specs
        except FileNotFoundError:
            self.option_std_ctr_specs = download_info_data_object.download_option_std_ctr_specs(code_list)
            return self.option_std_ctr_specs


    
    def get_option_mon_ctr_specs(self, code_list, local_path, is_local = ('D://AmazingData_local_data//', True)):
        download_info_data_object = DownloadInfoData(local_path)
        
        try:
            if is_local:
                folder_name = LocalDataFolder.INFODATA.value + '/' + LocalDataFolder.Option_Mon_Ctr_Specs.value
                path = local_path + folder_name + '/'
                self.option_mon_ctr_specs = get_data_from_hdf5(path, LocalDataFolder.Option_Mon_Ctr_Specs.value)
                self.option_mon_ctr_specs = self.option_mon_ctr_specs[[
                    'CODE_OLD',
                    'CHANGE_DATE',
                    'MARKET_CODE',
                    'NAME_NEW',
                    'EXERCISE_PRICE_NEW',
                    'NAME_OLD',
                    'CODE_NEW',
                    'EXERCISE_PRICE_OLD',
                    'UNIT_OLD',
                    'UNIT_NEW',
                    'CHANGE_REASON']]
                self.option_mon_ctr_specs = self.option_mon_ctr_specs[self.option_mon_ctr_specs['MARKET_CODE'].isin(code_list)]
                return self.option_mon_ctr_specs
            self.option_mon_ctr_specs = None.download_option_mon_ctr_specs_change(code_list)
            return self.option_mon_ctr_specs
        except FileNotFoundError:
            self.option_mon_ctr_specs = download_info_data_object.download_option_mon_ctr_specs_change(code_list)
            return self.option_mon_ctr_specs


    
    def get_holder_num(self, code_list, local_path, is_local = ('D://AmazingData_local_data//', True), **kwargs):
        download_info_data_object = DownloadInfoData(local_path)
    # WARNING: Decompyle incomplete

    
    def get_dividend(self, code_list, local_path, is_local = ('D://AmazingData_local_data//', True), **kwargs):
        download_info_data_object = DownloadInfoData(local_path)
    # WARNING: Decompyle incomplete

    
    def get_right_issue(self, code_list, local_path, is_local = ('D://AmazingData_local_data//', True), **kwargs):
        download_info_data_object = DownloadInfoData(local_path)
    # WARNING: Decompyle incomplete

    
    def get_equity_structure(self, code_list, local_path, is_local = ('D://AmazingData_local_data//', True), **kwargs):
        download_info_data_object = DownloadInfoData(local_path)
    # WARNING: Decompyle incomplete

    
    def get_equity_pledge_freeze(self, code_list, local_path, is_local = ('D://AmazingData_local_data//', True), **kwargs):
        download_info_data_object = DownloadInfoData(local_path)
    # WARNING: Decompyle incomplete

    
    def get_equity_restricted(self, code_list, local_path, is_local = ('D://AmazingData_local_data//', True), **kwargs):
        download_info_data_object = DownloadInfoData(local_path)
    # WARNING: Decompyle incomplete

    
    def get_stock_basic(self, code_list):
        download_info_data_object = DownloadInfoData('')
        self.stock_basic = download_info_data_object.download_stock_basic(code_list)
        return self.stock_basic

    
    def get_profit_notice(self, code_list, local_path, is_local = ('D://AmazingData_local_data//', True), **kwargs):
        download_info_data_object = DownloadInfoData(local_path)
    # WARNING: Decompyle incomplete

    
    def get_profit_express(self, code_list, local_path, is_local = ('D://AmazingData_local_data//', True), **kwargs):
        download_info_data_object = DownloadInfoData(local_path)
    # WARNING: Decompyle incomplete

    
    def get_history_stock_status(self, code_list, local_path, is_local = ('D://AmazingData_local_data//', True), **kwargs):
        download_info_data_object = DownloadInfoData(local_path)
    # WARNING: Decompyle incomplete

    
    def get_balance_sheet(self, code_list, local_path, is_local = ('D://AmazingData_local_data//', True), **kwargs):
        download_info_data_object = DownloadInfoData(local_path)
    # WARNING: Decompyle incomplete

    
    def get_income(self, code_list, local_path, is_local = ('D://AmazingData_local_data//', True), **kwargs):
        download_info_data_object = DownloadInfoData(local_path)
    # WARNING: Decompyle incomplete

    
    def get_cash_flow(self, code_list, local_path, is_local = ('D://AmazingData_local_data//', True), **kwargs):
        download_info_data_object = DownloadInfoData(local_path)
    # WARNING: Decompyle incomplete

    
    def get_margin_detail(self, code_list, local_path, is_local = ('D://AmazingData_local_data//', True), **kwargs):
        download_info_data_object = DownloadInfoData(local_path)
    # WARNING: Decompyle incomplete

    
    def get_margin_summary(self, local_path, is_local = ('D://AmazingData_local_data//', True), **kwargs):
        download_info_data_object = DownloadInfoData(local_path)
    # WARNING: Decompyle incomplete

    
    def get_bj_code_mapping(self, local_path, is_local = ('D://AmazingData_local_data//', True)):
        
        try:
            if is_local:
                folder_name = LocalDataFolder.BASEDATA.value + '/' + LocalDataFolder.BJ_Code_Mapping.value
                path = local_path + folder_name + '/'
                self.bj_code_mapping = get_data_from_hdf5(path, LocalDataFolder.BJ_Code_Mapping.value)
                return self.bj_code_mapping
            download_info_data_object = None(local_path)
            self.bj_code_mapping = download_info_data_object.download_bj_code_mapping()
            return self.bj_code_mapping
        except FileNotFoundError:
            download_info_data_object = DownloadInfoData(local_path)
            self.bj_code_mapping = download_info_data_object.download_bj_code_mapping()
            return self.bj_code_mapping


    
    def get_fund_share(self, code_list, local_path, is_local = ('D://AmazingData_local_data//', True), **kwargs):
        download_info_data_object = DownloadInfoData(local_path)
    # WARNING: Decompyle incomplete

    
    def get_fund_nav(self, code_list, local_path, is_local = ('D://AmazingData_local_data//', True), **kwargs):
        download_info_data_object = DownloadInfoData(local_path)
    # WARNING: Decompyle incomplete

    
    def get_fund_iopv(self, code_list, local_path, is_local = ('D://AmazingData_local_data//', True), **kwargs):
        download_info_data_object = DownloadInfoData(local_path)
    # WARNING: Decompyle incomplete

    
    def get_index_weight(self, code_list, local_path, is_local = ('D://AmazingData_local_data//', True), **kwargs):
        download_info_data_object = DownloadInfoData(local_path)
    # WARNING: Decompyle incomplete

    
    def get_index_constituent(self, code_list, local_path, is_local = ('D://AmazingData_local_data//', True)):
        download_info_data_object = DownloadInfoData(local_path)
        if is_local:
            folder_name = LocalDataFolder.INFODATA.value + '/' + LocalDataFolder.Index_Constituent.value
            path = local_path + folder_name + '/'
            for code in code_list:
                self.index_constituent[code] = get_data_from_hdf5(path, code)
            return self.index_constituent
        self.index_constituent = None.download_index_constituent(code_list)
        return self.index_constituent
        except FileNotFoundError:
            continue

    
    def get_industry_base_info(self, local_path, is_local = ('D://AmazingData_local_data//', True)):
        
        try:
            if is_local:
                folder_name = LocalDataFolder.BASEDATA.value + '/' + LocalDataFolder.Industry_Base_Info.value
                path = local_path + folder_name + '/'
                self.industry_base_info = get_data_from_hdf5(path, LocalDataFolder.Industry_Base_Info.value)
                return self.industry_base_info
            download_info_data_object = None(local_path)
            self.industry_base_info = download_info_data_object.download_industry_base_info()
            return self.industry_base_info
        except FileNotFoundError:
            download_info_data_object = DownloadInfoData(local_path)
            self.industry_base_info = download_info_data_object.download_industry_base_info()
            return self.industry_base_info


    
    def get_industry_weight(self, code_list, local_path, is_local = ('D://AmazingData_local_data//', True), **kwargs):
        download_info_data_object = DownloadInfoData(local_path)
    # WARNING: Decompyle incomplete

    
    def get_industry_constituent(self, code_list, local_path, is_local = ('D://AmazingData_local_data//', True)):
        download_info_data_object = DownloadInfoData(local_path)
        if is_local:
            folder_name = LocalDataFolder.INFODATA.value + '/' + LocalDataFolder.Industry_Constituent.value
            path = local_path + folder_name + '/'
            for code in code_list:
                self.industry_constituent[code] = get_data_from_hdf5(path, code)
            return self.industry_constituent
        self.industry_constituent = None.download_industry_constituent(code_list)
        return self.industry_constituent
        except FileNotFoundError:
            continue

    
    def get_industry_daily(self, code_list, local_path, is_local = ('D://AmazingData_local_data//', True), **kwargs):
        download_info_data_object = DownloadInfoData(local_path)
    # WARNING: Decompyle incomplete

    
    def get_treasury_yield(self, code_list, local_path, is_local = ('D://AmazingData_local_data//', True), **kwargs):
        download_info_data_object = DownloadInfoData(local_path)
    # WARNING: Decompyle incomplete

    
    def get_kzz_issuance(self, code_list, local_path, is_local = ('D://AmazingData_local_data//', True)):
        download_info_data_object = DownloadInfoData(local_path)
        
        try:
            if is_local:
                folder_name = LocalDataFolder.INFODATA.value + '/' + LocalDataFolder.Kzz.value
                path = local_path + folder_name + '/'
                self.kzz_issuance = get_data_from_hdf5(path, LocalDataFolder.Kzz_Issuance.value)
                self.kzz_issuance = self.kzz_issuance[[
                    'MARKET_CODE',
                    'STOCK_CODE',
                    'CRNCY_CODE',
                    'ANN_DT',
                    'PRE_PLAN_DATE',
                    'SMTG_ANN_DATE',
                    'LISTED_ANN_DATE',
                    'LISTED_DATE',
                    'PLAN_SCHEDULE',
                    'IS_SEPARATION',
                    'RECOMMENDER',
                    'CLAUSE_IS_INT_CHA_DEPO_RATE',
                    'CLAUSE_IS_COM_INT',
                    'CLAUSE_COM_INT_RATE',
                    'CLAUSE_COM_INT_DESC',
                    'CLAUSE_INIT_CONV_PRICE_ITEM',
                    'CLAUSE_CONV_ADJ_ITEM',
                    'CLAUSE_CONV_PERIOD_ITEM',
                    'CLAUSE_INI_CONV_PRICE',
                    'CLAUSE_INI_CONV_PREMIUM_RATIO',
                    'CLAUSE_PUT_ITEM',
                    'CLAUSE_CALL_ITEM',
                    'CLAUSE_SPEC_DOWN_ADJ',
                    'CLAUSE_ORIG_RATION_ARR_ITEM',
                    'LIST_PASS_DATE',
                    'LIST_PERMIT_DATE',
                    'LIST_ANN_DATE',
                    'LIST_RESULT_ANN_DATE',
                    'LIST_TYPE',
                    'LIST_FEE',
                    'LIST_RATION_DATE',
                    'LIST_RATION_REG_DATE',
                    'LIST_RATION_PAYMT_DATE',
                    'LIST_RATION_CODE',
                    'LIST_RATION_NAME',
                    'LIST_RATION_PRICE',
                    'LIST_RATION_RATIO_DE',
                    'LIST_RATION_RATIO_MO',
                    'LIST_RATION_VOL',
                    'LIST_HOUSEHOLD',
                    'LIST_ONL_DATE',
                    'LIST_PCHASE_CODE_ONL',
                    'LIST_PCH_NAME_ONL',
                    'LIST_PCH_PRICE_ONL',
                    'LIST_ISSUE_VOL_ONL',
                    'LIST_CODE_ONL',
                    'LIST_EXCESS_PCH_ONL',
                    'RESULT_EF_SUBSCR_P_OFF',
                    'RESULT_SUC_RATE_OFF',
                    'LIST_DATE_INST_OFF',
                    'LIST_VOL_INST_OFF',
                    'RESULT_SUC_RATE_ON',
                    'LIST_EFFECT_PC_HVOL_OFF',
                    'LIST_EFF_PC_H_OF',
                    'LIST_SUC_RATE_OFF',
                    'PRE_RATION_VOL',
                    'LIST_ISSUE_SIZE',
                    'LIST_ISSUE_QUANTITY',
                    'MIN_OFF_INST_SUBSCR_QTY',
                    'OFF_INST_DEP_RATIO',
                    'MAX_OFF_INST_SUBSCR_QTY',
                    'OFF_SUBSCR_UNIT_INC_DESC',
                    'IS_CONV_BONDS',
                    'MIN_UNLINE_PUBLIC',
                    'MAX_UNLINE_PUBLIC',
                    'TERM_YEAR',
                    'INTEREST_TYPE',
                    'COUPON_RATE',
                    'INTEREST_FRE_QUENCY',
                    'RESULT_SUC_RATE_ON2',
                    'COUPON_TXT',
                    'RATIO_ANNCE_DATE',
                    'RATIO_DATE']]
                self.kzz_issuance = self.kzz_issuance[self.kzz_issuance['MARKET_CODE'].isin(code_list)]
                return self.kzz_issuance
            self.kzz_issuance = None.download_kzz_issuance(code_list)
            return self.kzz_issuance
        except FileNotFoundError:
            self.kzz_issuance = download_info_data_object.download_kzz_issuance(code_list)
            return self.kzz_issuance


    
    def get_kzz_share(self, code_list, local_path, is_local = ('D://AmazingData_local_data//', True)):
        download_info_data_object = DownloadInfoData(local_path)
        
        try:
            if is_local:
                folder_name = LocalDataFolder.INFODATA.value + '/' + LocalDataFolder.Kzz.value
                path = local_path + folder_name + '/'
                self.kzz_share = get_data_from_hdf5(path, LocalDataFolder.Kzz_Share.value)
                self.kzz_share = self.kzz_share[[
                    'CHANGE_DATE',
                    'ANN_DATE',
                    'MARKET_CODE',
                    'BOND_SHARE',
                    'CONV_SHARE',
                    'CHANGE_REASON']]
                self.kzz_share = self.kzz_share[self.kzz_share['MARKET_CODE'].isin(code_list)]
                return self.kzz_share
            self.kzz_share = None.download_kzz_share(code_list)
            return self.kzz_share
        except FileNotFoundError:
            self.kzz_share = download_info_data_object.download_kzz_share(code_list)
            return self.kzz_share


    
    def get_kzz_conv(self, code_list, local_path, is_local = ('D://AmazingData_local_data//', True)):
        download_info_data_object = DownloadInfoData(local_path)
        
        try:
            if is_local:
                folder_name = LocalDataFolder.INFODATA.value + '/' + LocalDataFolder.Kzz.value
                path = local_path + folder_name + '/'
                self.kzz_conv = get_data_from_hdf5(path, LocalDataFolder.Kzz_Conv.value)
                self.kzz_conv = self.kzz_conv[[
                    'MARKET_CODE',
                    'ANN_DATE',
                    'CONV_CODE',
                    'CONV_NAME',
                    'CONV_PRICE',
                    'CURRENCY_CODE',
                    'CONV_START_DATE',
                    'CONV_END_DATE',
                    'TRADE_DATE_LAST',
                    'FORCED_CONV_DATE',
                    'FORCED_CONV_PRICE',
                    'REL_CONV_MONTH',
                    'IS_FORCED',
                    'FORCED_CONV_REASON']]
                self.kzz_conv = self.kzz_conv[self.kzz_conv['MARKET_CODE'].isin(code_list)]
                return self.kzz_conv
            self.kzz_conv = None.download_kzz_conv(code_list)
            return self.kzz_conv
        except FileNotFoundError:
            self.kzz_conv = download_info_data_object.download_kzz_conv(code_list)
            return self.kzz_conv


    
    def get_kzz_conv_change(self, code_list, local_path, is_local = ('D://AmazingData_local_data//', True)):
        download_info_data_object = DownloadInfoData(local_path)
        
        try:
            if is_local:
                folder_name = LocalDataFolder.INFODATA.value + '/' + LocalDataFolder.Kzz.value
                path = local_path + folder_name + '/'
                self.kzz_conv_change = get_data_from_hdf5(path, LocalDataFolder.Kzz_Conv_change.value)
                self.kzz_conv_change = self.kzz_conv_change[[
                    'MARKET_CODE',
                    'CHANGE_DATE',
                    'ANN_DATE',
                    'CONV_PRICE',
                    'CHANGE_REASON']]
                self.kzz_conv_change = self.kzz_conv_change[self.kzz_conv_change['MARKET_CODE'].isin(code_list)]
                return self.kzz_conv_change
            self.kzz_conv_change = None.download_kzz_conv_change(code_list)
            return self.kzz_conv_change
        except FileNotFoundError:
            self.kzz_conv_change = download_info_data_object.download_kzz_conv_change(code_list)
            return self.kzz_conv_change


    
    def get_kzz_corr(self, code_list, local_path, is_local = ('D://AmazingData_local_data//', True)):
        download_info_data_object = DownloadInfoData(local_path)
        
        try:
            if is_local:
                folder_name = LocalDataFolder.INFODATA.value + '/' + LocalDataFolder.Kzz.value
                path = local_path + folder_name + '/'
                self.kzz_corr = get_data_from_hdf5(path, LocalDataFolder.Kzz_Corr.value)
                self.kzz_corr = self.kzz_corr[[
                    'MARKET_CODE',
                    'START_DATE',
                    'END_DATE',
                    'CORR_TRIG_CALC_MAX_PERIOD',
                    'CORR_TRIG_CALC_PERIOD',
                    'SPEC_CORR_TRIG_RATIO',
                    'CORR_CONV_PRICE_FLOOR_DESC',
                    'REF_PRICE_IS_AVG_PRICE',
                    'CORR_TIMES_LIMIT',
                    'IS_TIMEPOINT_CORR_CLAUSE_FLAG',
                    'TIMEPOINT_COUNT',
                    'TIMEPOINT_CORR_TEXT_CLAUSE',
                    'SPEC_CORR_RANGE',
                    'IS_SPEC_DOWN_CORR_CLAUSE_FLAG']]
                self.kzz_corr = self.kzz_corr[self.kzz_corr['MARKET_CODE'].isin(code_list)]
                return self.kzz_corr
            self.kzz_corr = None.download_kzz_corr(code_list)
            return self.kzz_corr
        except FileNotFoundError:
            self.kzz_corr = download_info_data_object.download_kzz_corr(code_list)
            return self.kzz_corr


    
    def get_kzz_call(self, code_list, local_path, is_local = ('D://AmazingData_local_data//', True)):
        download_info_data_object = DownloadInfoData(local_path)
        
        try:
            if is_local:
                folder_name = LocalDataFolder.INFODATA.value + '/' + LocalDataFolder.Kzz.value
                path = local_path + folder_name + '/'
                self.kzz_call = get_data_from_hdf5(path, LocalDataFolder.Kzz_Call.value)
                self.kzz_call = self.kzz_call[[
                    'MARKET_CODE',
                    'CALL_PRICE',
                    'BEGIN_DATE',
                    'END_DATE',
                    'TRI_RATIO']]
                self.kzz_call = self.kzz_call[self.kzz_call['MARKET_CODE'].isin(code_list)]
                return self.kzz_call
            self.kzz_call = None.download_kzz_call(code_list)
            return self.kzz_call
        except FileNotFoundError:
            self.kzz_call = download_info_data_object.download_kzz_call(code_list)
            return self.kzz_call


    
    def get_kzz_put(self, code_list, local_path, is_local = ('D://AmazingData_local_data//', True)):
        download_info_data_object = DownloadInfoData(local_path)
        
        try:
            if is_local:
                folder_name = LocalDataFolder.INFODATA.value + '/' + LocalDataFolder.Kzz.value
                path = local_path + folder_name + '/'
                self.kzz_put = get_data_from_hdf5(path, LocalDataFolder.Kzz_Put.value)
                self.kzz_put = self.kzz_put[[
                    'MARKET_CODE',
                    'PUT_PRICE',
                    'BEGIN_DATE',
                    'END_DATE',
                    'TRI_RATIO']]
                self.kzz_put = self.kzz_put[self.kzz_put['MARKET_CODE'].isin(code_list)]
                return self.kzz_put
            self.kzz_put = None.download_kzz_put(code_list)
            return self.kzz_put
        except FileNotFoundError:
            self.kzz_put = download_info_data_object.download_kzz_put(code_list)
            return self.kzz_put


    
    def get_kzz_put_call_item(self, code_list, local_path, is_local = ('D://AmazingData_local_data//', True)):
        download_info_data_object = DownloadInfoData(local_path)
        
        try:
            if is_local:
                folder_name = LocalDataFolder.INFODATA.value + '/' + LocalDataFolder.Kzz.value
                path = local_path + folder_name + '/'
                self.kzz_put_call_item = get_data_from_hdf5(path, LocalDataFolder.Kzz_Put_call_item.value)
                self.kzz_put_call_item = self.kzz_put_call_item[[
                    'MARKET_CODE',
                    'MAND_PUT_PERIOD',
                    'MAND_PUT_PRICE',
                    'MAND_PUT_START_DATE',
                    'MAND_PUT_END_DATE',
                    'MAND_PUT_TEXT',
                    'IS_MAND_PUT_CONTAIN_CURRENT',
                    'CON_PUT_START_DATE',
                    'CON_PUT_END_DATE',
                    'MAX_PUT_TRI_PER',
                    'PUT_TRI_PERIOD',
                    'ADD_PUT_CON',
                    'ADD_PUT_PRICE_INS',
                    'PUT_NUM_INS',
                    'PUT_PRO_PERIOD',
                    'PUT_NO_PERY',
                    'IS_PUT_ITEM',
                    'IS_TERM_PUT_ITEM',
                    'IS_MAND_PUT_ITEM',
                    'IS_TIME_PUT_ITEM',
                    'TIME_PUT_NO',
                    'TIME_PUT_ITEM',
                    'TERM_PUT_PRICE',
                    'CON_CALL_START_DATE',
                    'CON_CALL_END_DATE',
                    'CALL_TRI_CON_INS',
                    'MAX_CALL_TRI_PER',
                    'CALL_TRI_PER',
                    'CALL_NUM_BER_INS',
                    'IS_CALL_ITEM',
                    'CALL_PRO_PERIOD',
                    'CALL_NO_PERY',
                    'IS_TIME_CALL_ITEM',
                    'TIME_CALL_NO',
                    'TIME_CALL_TEXT',
                    'TERM_CALL_PRICE',
                    'PUT_TRI_CON_DESC']]
                self.kzz_put_call_item = self.kzz_put_call_item[self.kzz_put_call_item['MARKET_CODE'].isin(code_list)]
                return self.kzz_put_call_item
            self.kzz_put_call_item = None.download_kzz_put_call_item(code_list)
            return self.kzz_put_call_item
        except FileNotFoundError:
            self.kzz_put_call_item = download_info_data_object.download_kzz_put_call_item(code_list)
            return self.kzz_put_call_item


    
    def get_kzz_put_explanation(self, code_list, local_path, is_local = ('D://AmazingData_local_data//', True)):
        download_info_data_object = DownloadInfoData(local_path)
        
        try:
            if is_local:
                folder_name = LocalDataFolder.INFODATA.value + '/' + LocalDataFolder.Kzz.value
                path = local_path + folder_name + '/'
                self.kzz_put_explanation = get_data_from_hdf5(path, LocalDataFolder.Kzz_Put_explanation.value)
                self.kzz_put_explanation = self.kzz_put_explanation[[
                    'MARKET_CODE',
                    'PUT_FUND_ARRIVAL_DATE',
                    'PUT_PRICE',
                    'PUT_ANNOUNCEMENT_DATE',
                    'PUT_FUL_RES_ANN_DATE',
                    'PUT_AMOUNT',
                    'CALL_OUTSTANDING_AMOUNT',
                    'PUT_EXER_START_DATE',
                    'PUT_EXER_END_DATE',
                    'RESALE_START_DATE',
                    'PUT_DATE',
                    'PUT_CODE',
                    'RESALE_AMOUNT',
                    'RESALE_IMP_AMOUNT',
                    'RESALE_END_DATE']]
                self.kzz_put_explanation = self.kzz_put_explanation[self.kzz_put_explanation['MARKET_CODE'].isin(code_list)]
                return self.kzz_put_explanation
            self.kzz_put_explanation = None.download_kzz_put_explanation(code_list)
            return self.kzz_put_explanation
        except FileNotFoundError:
            self.kzz_put_explanation = download_info_data_object.download_kzz_put_explanation(code_list)
            return self.kzz_put_explanation


    
    def get_kzz_call_explanation(self, code_list, local_path, is_local = ('D://AmazingData_local_data//', True)):
        download_info_data_object = DownloadInfoData(local_path)
        
        try:
            if is_local:
                folder_name = LocalDataFolder.INFODATA.value + '/' + LocalDataFolder.Kzz.value
                path = local_path + folder_name + '/'
                self.kzz_call_explanation = get_data_from_hdf5(path, LocalDataFolder.Kzz_Call_explanation.value)
                self.kzz_call_explanation = self.kzz_call_explanation[[
                    'MARKET_CODE',
                    'CALL_DATE',
                    'CALL_PRICE',
                    'CALL_ANNOUNCEMENT_DATE',
                    'CALL_FUL_RES_ANN_DATE',
                    'CALL_AMOUNT',
                    'CALL_OUTSTANDING_AMOUNT',
                    'CALL_DATE_PUB',
                    'CALL_FUND_ARRIVAL_DATE',
                    'CALL_RECORD_DAY',
                    'CALL_REASON']]
                self.kzz_call_explanation = self.kzz_call_explanation[self.kzz_call_explanation['MARKET_CODE'].isin(code_list)]
                return self.kzz_call_explanation
            self.kzz_call_explanation = None.download_kzz_call_explanation(code_list)
            return self.kzz_call_explanation
        except FileNotFoundError:
            self.kzz_call_explanation = download_info_data_object.download_kzz_call_explanation(code_list)
            return self.kzz_call_explanation


    
    def get_kzz_suspend(self, code_list, local_path, is_local = ('D://AmazingData_local_data//', True)):
        download_info_data_object = DownloadInfoData(local_path)
        
        try:
            if is_local:
                folder_name = LocalDataFolder.INFODATA.value + '/' + LocalDataFolder.Kzz.value
                path = local_path + folder_name + '/'
                self.kzz_suspend = get_data_from_hdf5(path, LocalDataFolder.Kzz_Suspend.value)
                self.kzz_suspend = self.kzz_suspend[[
                    'MARKET_CODE',
                    'SUSPEND_DATE',
                    'SUSPEND_TYPE',
                    'RESUMP_DATE',
                    'SUSPEND_REASON',
                    'RESUMP_TIME']]
                self.kzz_suspend = self.kzz_suspend[self.kzz_suspend['MARKET_CODE'].isin(code_list)]
                return self.kzz_suspend
            self.kzz_suspend = None.download_kzz_suspend(code_list)
            return self.kzz_suspend
        except FileNotFoundError:
            self.kzz_suspend = download_info_data_object.download_kzz_suspend(code_list)
            return self.kzz_suspend


    
    def get_announcement_stock_list(self, code_list, local_path, is_local = ('D://AmazingData_local_data//', True), **kwargs):
        pass
    # WARNING: Decompyle incomplete

    
    def get_announcement_stock(self, announcement_stock_list_df, tag_id_list, begin_date, end_date, local_path = (None, 19900101, 20980101, 'D://AmazingData_local_data//')):
        if announcement_stock_list_df.empty:
            return (self.announcement_stock_pdf_path, self.announcement_bond_stock_tag_df)
    # WARNING: Decompyle incomplete

    
    def get_announcement_fund_list(self, code_list, local_path, is_local = ('D://AmazingData_local_data//', True), **kwargs):
        pass
    # WARNING: Decompyle incomplete

    
    def get_announcement_fund(self, announcement_fund_list_df, tag_id_list, begin_date, end_date, local_path = (None, 19900101, 20980101, 'D://AmazingData_local_data//')):
        if announcement_fund_list_df.empty:
            return (self.announcement_fund_pdf_path, self.announcement_fund_list_tag_df)
    # WARNING: Decompyle incomplete

    
    def get_announcement_bond_list(self, code_list, local_path, is_local = ('D://AmazingData_local_data//', True), **kwargs):
        pass
    # WARNING: Decompyle incomplete

    
    def get_announcement_bond(self, announcement_bond_list_df, tag_id_list, begin_date, end_date, local_path = (None, 19900101, 20980101, 'D://AmazingData_local_data//')):
        if announcement_bond_list_df.empty:
            return (self.announcement_bond_pdf_path, self.announcement_bond_list_tag_df)
    # WARNING: Decompyle incomplete


