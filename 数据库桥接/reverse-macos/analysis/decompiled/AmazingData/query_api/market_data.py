# Source Generated with Decompyle++
# File: market_data.pyc (Python 3.12)

import copy
import time
import pandas as pd
import tgw
from AmazingData.download_data.market_spi import SnapshotSpi, KlineSpi
from AmazingData.environment import QueryLock, QueryPara, EnvSnapshot, EnvKline
from AmazingData.login.tgw_login import login
from AmazingData.utils.constant import Period
from AmazingData.utils.convert import get_code, get_tgw_type_code, convert_history_tick_index, convert_history_tick_stock, convert_history_kline, convert_history_tick_stock_HKT, convert_history_tick_future, convert_history_tick_option
from AmazingData.utils.security_type import is_security_type

class MarketData(object):
    
    def __init__(self, calendar):
        self.calendar = calendar
        self.lock = QueryLock.query_lock

    
    def query_snapshot(self, code_list, begin_date, end_date, **kwargs):
        '''
        查询快照数据，仅支持level-1快照
        :param code_list:  支持沪深北京交易所所有品种，包含股票、指数、债券、期货等
        :param begin_date:  int 例如20230202
        :param end_date:  int 例如20230202，开始时间结束时间可以一致，只返回一个标的数据
        :return: [dict, err], {date: {code: dataframe}}
        '''
        if 'begin_time' in kwargs and 'end_time' in kwargs:
            begin_time = kwargs['begin_time']
            end_time = kwargs['end_time']
        else:
            begin_time = None
            end_time = None
        self.lock.acquire()
    # WARNING: Decompyle incomplete

    
    def query_kline(self, code_list, begin_date, end_date, period = (20240101, 20991231, Period.min1.value), **kwargs):
        self.lock.acquire()
        if 'begin_time' in kwargs and 'end_time' in kwargs:
            begin_time = kwargs['begin_time']
            end_time = kwargs['end_time']
        else:
            begin_time = None
            end_time = None
    # WARNING: Decompyle incomplete


if __name__ == '__main__':
    from AmazingData.query_api.base_data import BaseData
    login(username = TgwConfig.username, password = TgwConfig.password, host = TgwConfig.host, port = TgwConfig.port)
    base_data_object = BaseData()
    calendar = base_data_object.get_calendar()
    code_list = base_data_object.get_code_list(all_code = True)
    market_data_object = MarketData(calendar)
    time1 = time.time()
    snapshot_dict = market_data_object.query_snapshot(code_list[:100], begin_date = 20240530, end_date = 20240530)
    kline_dict = market_data_object.query_kline(code_list, begin_date = 20240530, end_date = 20240530, period = Period.day.value)
    time2 = time.time()
    print(time2 - time1)
    return None
