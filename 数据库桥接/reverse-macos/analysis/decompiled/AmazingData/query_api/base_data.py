# Source Generated with Decompyle++
# File: base_data.pyc (Python 3.12)

import os
import tgw
import pandas as pd
import numpy as np
from AmazingData.utils.data_transfer import date_to_datetime, datetime_to_int, is_time_interval
from AmazingData.utils.convert import get_code, get_tgw_type_code, get_market
from AmazingData.utils.security_type import is_security_type
from AmazingData.utils.save_get_data import get_data_from_pkl, get_data_from_hdf5, save_data_to_hdf5
from AmazingData.login.tgw_login import login
from AmazingData.config.local_data_folder import LocalDataFolder
from AmazingData.download_data.download_info_data import DownloadInfoData
from AmazingData.query_api.market_data import MarketData
from AmazingData.utils.constant import Period

class BaseData(object):
    
    def __init__(self):
        self.calendar = []
        self.stock_list = []
        self.code_list_hist = []
        self.block_trading = { }

    
    def get_calendar(self, data_type, market, date = ('str', 'SH', datetime_to_int())):
        """
        :param date:
        :param market: SH, SZ, BJ
        :param data_type: data_type == 'datetime' or 'str'
        :return:
        """
        task_id = tgw.GetTaskID()
        tgw.SetThirdInfoParam(task_id, 'function_id', 'A010061003')
        tgw.SetThirdInfoParam(task_id, 'start_date', '19900101')
        tgw.SetThirdInfoParam(task_id, 'end_date', str(date))
        market_dict = {
            'SZ': 'SZSE',
            'SH': 'SSE',
            'BJ': 'NEEQ',
            'SHF': 'SHFE',
            'CFE': 'CFFEX',
            'DCE': 'DCE',
            'CZC': 'CZCE',
            'INE': 'INE',
            'SHN': 'SHHK',
            'SZN': 'SZHK' }
        tgw.SetThirdInfoParam(task_id, 'market', market_dict[market])
        (trade_days_df, _) = tgw.QueryThirdInfo(task_id)
        trade_days_df['TRADE_DAYS'] = trade_days_df['TRADE_DAYS'].astype(int)
        if market == 'BJ':
            trade_days_df = trade_days_df[trade_days_df['TRADE_DAYS'] >= 20211115]
        self.calendar = list(trade_days_df['TRADE_DAYS'].sort_values(ascending = True))
    # WARNING: Decompyle incomplete

    
    def get_code_list(self, security_type = ('EXTRA_STOCK_A_SH_SZ',)):
        '''
        :param security_type: ,默认："EXTRA_STOCK_A_SH_SZ"  # 沪深A股
        可选
        "EXTRA_STOCK_A" # 沪深北A股
        "EXTRA_INDEX_A" # 沪深北指数
        "EXTRA_ETF" # 沪深ETF
        "EXTRA_KZZ" # 沪深可转债
        "EXTRA_STOCK_A_SH_SZ"  # 沪深A股
        "EXTRA_INDEX_A_SH_SZ"  # 沪深指数
        "EXTRA_INDEX_A_SH_SZ"  # 沪深指数
        "SH_A", # 沪A股
        "SZ_A", # 深A股
        "BJ_A",  # 北A股
        "SH_INDEX",  # 沪指数
        "SZ_INDEX",   # 深指数
        "BJ_INDEX",  # 北指数
        "SH_ETF",  # 沪ETF
        "SZ_ETF",  # 深ETF
        "SH_KZZ",  # 沪可转债
        "SZ_KZZ",  # 深可转债
        :param all_code:
        :return:
        '''
        if security_type in ('EXTRA_HKT', 'SH_HKT', 'SZ_HKT'):
            task_id = tgw.GetTaskID()
            tgw.SetThirdInfoParam(task_id, 'function_id', 'A010010011')
            (result, _) = tgw.QueryThirdInfo(task_id)
            if security_type == 'SZ_HKT':
                result = result[result['TYPE_CODE_INT'].isin([
                    1,
                    3]) & (result['CURRENT_SIGN'] == 1)]
            elif security_type == 'SH_HKT':
                result = result[result['TYPE_CODE_INT'].isin([
                    1,
                    3]) & (result['CURRENT_SIGN'] == 1)]
            else:
                result = result[result['CURRENT_SIGN'] == 1]
            for index, row in result.iterrows():
                if row['TYPE_CODE_INT'] == 1:
                    self.stock_list.append(row['SECURITY_CODE'][:-3] + '.SZ')
                    continue
                if row['TYPE_CODE_INT'] == 2:
                    self.stock_list.append(row['SECURITY_CODE'][:-3] + '.SH')
                    continue
                if not row['TYPE_CODE_INT'] == 3:
                    continue
                self.stock_list.append(row['SECURITY_CODE'][:-3] + '.SZ')
                self.stock_list.append(row['SECURITY_CODE'][:-3] + '.SH')
            return self.stock_list
    # WARNING: Decompyle incomplete

    
    def get_option_code_list(self, security_type = ('EXTRA_ETF_OP',)):
        '''
        :param security_type: ,默认："EXTRA_ETF_OP"  # ETF期权
        可选
        :param all_code:
        :return:
        '''
        code_list_all = []
        market_list = []
        if security_type in ('EXTRA_ETF_OP', 'SH_OPTION', 'SZ_OPTION'):
            market_list = [
                tgw.MarketType.kSZSE,
                tgw.MarketType.kSSE]
        elif security_type in ('EXTRA_CF_OP', 'SQ_OPTION', 'DS_OPTION', 'ZS_OPTION', 'SN_OPTION'):
            market_list = [
                tgw.MarketType.kSHFE,
                tgw.MarketType.kDCE,
                tgw.MarketType.kCZCE,
                tgw.MarketType.kINE]
        elif security_type in ('ZJ_OPTION',):
            market_list = [
                tgw.MarketType.kCFFEX]
        elif security_type in ('EXTRA_INDEX_OP',):
            market_list = [
                tgw.MarketType.kCFFEX,
                tgw.MarketType.kSZSE,
                tgw.MarketType.kSSE]
        elif security_type in ('EXTRA_OP',):
            market_list = [
                tgw.MarketType.kCFFEX,
                tgw.MarketType.kSZSE,
                tgw.MarketType.kSSE,
                tgw.MarketType.kSHFE,
                tgw.MarketType.kDCE,
                tgw.MarketType.kCZCE,
                tgw.MarketType.kINE]
    # WARNING: Decompyle incomplete

    
    def get_future_code_list(self, security_type = ('EXTRA_FUTURE',)):
        '''
        :param security_type: ,默认："EXTRA_FUTURE"  # 期货, 中金所/上期所/大商所/郑商所/上期上海国际能源交易中心所
        可选
        "ZJ_FUTURE":  中金所期货
        "SQ_FUTURE":  上期所期货
        "DS_FUTURE":  大商所期货
        "ZS_FUTURE":  郑商所期货
        "SN_FUTURE":  上期上海国际能源交易中心所期货

        :return:
        '''
        pass
    # WARNING: Decompyle incomplete

    
    def get_code_info(self, security_type = ('EXTRA_STOCK_A',)):
        '''
        :param security_type: ,默认："EXTRA_STOCK_A"  # 沪深北A股
        可选
        "EXTRA_STOCK_A" # 沪深北A股
        "EXTRA_IDNEX_A" # 沪深北指数
        "EXTRA_ETF" # 沪深ETF
        "EXTRA_STOCK_A_SH_SZ"  # 沪深A股
        "EXTRA_IDNEX_A_SH_SZ"  # 沪深指数
        "SH_A", # 沪A股
        "SZ_A", # 深A股
        "BJ_A",  # 北A股
        "SH_INDEX",  # 沪指数
        "SZ_INDEX",   # 深指数
        "BJ_INDEX",  # 北指数
        "SH_ETF",  # 沪ETF
        "SZ_ETF",  # 深ETF
        :param all_code:
        :return:
        symbol : 证券简称
        pre_close : 昨收价
        high_limited : 涨停价
        low_limited : 跌停价
        price_tick : 最小价格变动单位
        '''
        pass
    # WARNING: Decompyle incomplete

    
    def get_future_code_info(self, security_type = ('EXTRA_FUTURE',)):
        '''
        :param security_type: ,默认："EXTRA_FUTURE"  # 期货, 中金所/上期所/大商所/郑商所/上期上海国际能源交易中心所
        可选
        "ZJ_FUTURE":  中金所期货
        "SQ_FUTURE":  上期所期货
        "DS_FUTURE":  大商所期货
        "ZS_FUTURE":  郑商所期货
        "SN_FUTURE":  上期上海国际能源交易中心所期货
        :return:
        symbol : 证券简称
        pre_close : 昨收价
        high_limited : 涨停价
        low_limited : 跌停价
        price_tick : 最小价格变动单位
        '''
        pass
    # WARNING: Decompyle incomplete

    
    def get_hist_code_list(self, security_type, start_date, end_date, local_path = ('EXTRA_STOCK_A_SH_SZ', 20240101, 20240701, 'D://AmazingData_local_data//')):
        '''
        :param security_type: ,默认："EXTRA_STOCK_A_SH_SZ"  # 沪深A股
        可选
        "EXTRA_STOCK_A" # 沪深北A股
        "EXTRA_IDNEX_A" # 沪深北指数
        "EXTRA_ETF" # 沪深ETF
        "EXTRA_STOCK_A_SH_SZ"  # 沪深A股
        "EXTRA_IDNEX_A_SH_SZ"  # 沪深指数
        "SH_A", # 沪A股
        "SZ_A", # 深A股
        "BJ_A",  # 北A股
        "SH_INDEX",  # 沪指数
        "SZ_INDEX",   # 深指数
        "BJ_INDEX",  # 北指数
        "SH_ETF",  # 沪ETF
        "SZ_ETF",  # 深ETF
        :param start_date: int 开始时间，闭区间，
        :param end_date: int 结束时间，闭区间，
        :param local_path: str 本地存储数据的地址
        :return: hist_code_list: list ,代码列表
        '''
        if security_type in ('EXTRA_HKT', 'SH_HKT', 'SZ_HKT'):
            task_id = tgw.GetTaskID()
            tgw.SetThirdInfoParam(task_id, 'function_id', 'A010010011')
            (result, _) = tgw.QueryThirdInfo(task_id)
            result = result.fillna('20990101')
            result_i = result[(result['ENTRY_DATE'] < str(end_date)) & (result['REMOVE_DATE'] > str(start_date))]
            if security_type == 'SZ_HKT':
                result_i = result_i[result_i['TYPE_CODE_INT'].isin([
                    1,
                    3]) & (result_i['CURRENT_SIGN'] == 1)]
            elif security_type == 'SH_HKT':
                result_i = result_i[result_i['TYPE_CODE_INT'].isin([
                    1,
                    3]) & (result_i['CURRENT_SIGN'] == 1)]
            else:
                result_i = result_i[result_i['CURRENT_SIGN'] == 1]
            for index, row in result_i.iterrows():
                if row['TYPE_CODE_INT'] == 1:
                    self.code_list_hist.append(row['SECURITY_CODE'][:-3] + '.SZ')
                    continue
                if row['TYPE_CODE_INT'] == 2:
                    self.code_list_hist.append(row['SECURITY_CODE'][:-3] + '.SH')
                    continue
                if not row['TYPE_CODE_INT'] == 3:
                    continue
                self.code_list_hist.append(row['SECURITY_CODE'][:-3] + '.SZ')
                self.code_list_hist.append(row['SECURITY_CODE'][:-3] + '.SH')
            self.code_list_hist = list(set(self.code_list_hist))
            return self.code_list_hist
        folder_name = None.BASEDATA.value + '/' + LocalDataFolder.HIST_CODE_LIST.value
        path = local_path + folder_name + '/'
        calendar = self.get_calendar(market = 'SH')
    # WARNING: Decompyle incomplete

    
    def get_backward_factor(self, code_list, local_path, is_local = ('D://AmazingData_local_data//', True)):
        calendar_index = self.get_calendar(market = 'SH')
        folder_name = LocalDataFolder.BASEDATA.value + '/' + LocalDataFolder.BACKWARD_FACTOR.value
        path = local_path + folder_name + '/'
        
        try:
            if is_local:
                backward_factor = get_data_from_hdf5(path, 'backward_factor')
            else:
                download_info_data_object = DownloadInfoData(local_path)
                backward_factor = download_info_data_object.download_backward_factor(code_list, calendar_index)
            
            try:
                backward_factor = backward_factor[code_list]
                if not is_local:
                    calendar_last = calendar_index[-1]
                    time_interval = is_time_interval()
                    today = datetime_to_int()
                    today_datetime = date_to_datetime(str(today))
                    market_data_object = MarketData(calendar_index)
                    if calendar_last == today:
                        if time_interval:
                            code_info = self.get_code_info()
                            code_info = code_info[code_info['security_status'] != '1']
                            code_info_list = list(code_info.index)
                            code_info_list = list(set(code_info_list) & set(code_list))
                            code_info = code_info.loc[code_info_list]
                            code_info.loc[('302132.SZ', 'list_day')] = 20250217
                            code_info = code_info.loc[code_info_list][(code_info['pre_close'] > 0) & (code_info['list_day'] < today)]
                            code_info_list_input = list(code_info.index)
                            pre_data = { }
                            for i in range(len(calendar_index)):
                                pre_data_fail = True
                                k_data = market_data_object.query_kline(code_info_list_input, begin_date = begin_date, end_date = calendar_index[-2 - i], period = Period.day.value)
                                for i_code in k_data:
                                    pre_k_close = k_data[i_code]['close'].values[-1]
                                    if pre_k_close > 0:
                                        pre_data[i_code] = pre_k_close
                                        code_info_list_input.remove(i_code)
                            if not pre_data_fail and len(calendar_index[-2 - i:]) > 5:
                                continue
                        
                        for code in pre_data:
                            backward_factor.loc[(today_datetime, code)] = backward_factor.loc[(today_datetime, code)] * pre_data[code] / code_info.loc[(code, 'pre_close')]
                    else:
                        backward_factor = backward_factor.iloc[(:-1, :)]
                save_data_to_hdf5(path, 'backward_factor', backward_factor)
                return backward_factor
                except FileNotFoundError:
                    download_info_data_object = DownloadInfoData(local_path)
                    backward_factor = download_info_data_object.download_backward_factor(code_list, calendar_index)
                    continue
            except:
                print(code_list, '  not in local backward_factor')
                continue
                pre_data_fail = False
                continue



    
    def get_adj_factor(self, code_list, local_path, is_local = ('D://AmazingData_local_data//', True)):
        calendar_index = self.get_calendar(market = 'SH')
        folder_name = LocalDataFolder.BASEDATA.value + '/' + LocalDataFolder.ADJ_FACTOR.value
        path = local_path + folder_name + '/'
        
        try:
            if is_local:
                adj_factor = get_data_from_hdf5(path, 'adj_factor')
            else:
                download_info_data_object = DownloadInfoData(local_path)
                adj_factor = download_info_data_object.download_adj_factor(code_list, calendar_index, is_local = is_local)
            
            try:
                adj_factor = adj_factor[code_list]
                calendar_last = calendar_index[-1]
                today = datetime_to_int()
                time_interval = is_time_interval()
                today_datetime = date_to_datetime(str(today))
                market_data_object = MarketData(calendar_index)
                if calendar_last == today:
                    if time_interval:
                        code_info = self.get_code_info()
                        code_info_list = list(code_info.index)
                        code_info_list = list(set(code_info_list) & set(code_list))
                        code_info = code_info.loc[code_info_list]
                        code_info.loc[('302132.SZ', 'list_day')] = 20250217
                        code_info = code_info.loc[code_info_list][(code_info['pre_close'] > 0) & (code_info['list_day'] < today)]
                        code_info_list_input = list(code_info.index)
                        pre_data = { }
                        for i in range(len(calendar_index)):
                            pre_data_fail = True
                            k_data = market_data_object.query_kline(code_info_list_input, begin_date = calendar_index[-2 - i], end_date = calendar_index[-2 - i], period = Period.day.value)
                            for i_code in k_data:
                                pre_k_close = k_data[i_code]['close'].values[0]
                                if pre_k_close > 0:
                                    pre_data[i_code] = pre_k_close
                                    code_info_list_input.remove(i_code)
                        if not pre_data_fail:
                            continue
                    
                    for code in pre_data:
                        adj_factor.loc[(today_datetime, code)] = pre_data[code] / code_info.loc[(code, 'pre_close')]
                else:
                    adj_factor = adj_factor.iloc[(:-1, :)]
                save_data_to_hdf5(path, 'adj_factor', adj_factor)
                return adj_factor
                except FileNotFoundError:
                    download_info_data_object = DownloadInfoData(local_path)
                    adj_factor = download_info_data_object.download_adj_factor(code_list, calendar_index, is_local = is_local)
                    continue
            except:
                print(code_list, '  not in local adj_factor')
                continue
                pre_data_fail = False
                continue



    
    def get_etf_pcf(self, code_list):
        security_cfg_list = []
        for code in code_list:
            (market_type, security_code) = get_tgw_type_code(code)
            security_cfg = tgw.SubCodeTableItem()
            security_cfg.market = market_type
            security_cfg.security_code = security_code
            security_cfg_list.append(security_cfg)
        (etf_pcf_list, _) = tgw.QueryETFInfo(security_cfg_list)
        etf_pcf_info_columns_list = [
            'creation_redemption_unit',
            'max_cash_ratio',
            'publish',
            'creation',
            'redemption',
            'creation_redemption_switch',
            'record_num',
            'total_record_num',
            'estimate_cash_component',
            'trading_day',
            'pre_trading_day',
            'cash_component',
            'nav_per_cu',
            'nav',
            'symbol',
            'fund_management_company',
            'underlying_security_id',
            'underlying_security_id_source',
            'dividend_per_cu',
            'creation_limit',
            'redemption_limit',
            'creation_limit_per_user',
            'redemption_limit_per_user',
            'net_creation_limit',
            'net_redemption_limit']
        etf_pcf_constituent_list = [
            'underlying_symbol',
            'component_share',
            'substitute_flag',
            'premium_ratio',
            'discount_ratio',
            'creation_cash_substitute',
            'redemption_cash_substitute',
            'substitution_cash_amount',
            'underlying_security_id']
        etf_pcf_info_list = []
        etf_pcf_constituent_dict = { }
    # WARNING: Decompyle incomplete


if __name__ == '__main__':
    login(username = TgwConfig.username, password = TgwConfig.password, host = TgwConfig.host, port = TgwConfig.port)
    base_data_object = BaseData()
    calendar = base_data_object.get_calendar()
    block_trading = base_data_object.get_block_trading()
    return None
