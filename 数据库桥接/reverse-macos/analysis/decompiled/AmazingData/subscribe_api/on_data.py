# Source Generated with Decompyle++
# File: on_data.pyc (Python 3.12)

import time
from concurrent.futures import ThreadPoolExecutor
from typing import Union
import tgw
from AmazingData.utils.constant import Period, Snapshot, SnapshotIndex, Kline
from AmazingData.utils.convert import get_code, get_tgw_type_code, convert_realtime_tick_index, convert_realtime_tick_stock, convert_realtime_kline, convert_realtime_snapshotL2, convert_realtime_order, convert_realtime_execution, convert_realtime_order_queue, convert_realtime_snapshotHKT, convert_realtime_snapshotfuture, convert_realtime_snapshotoption
from AmazingData.login.tgw_login import login
from AmazingData.utils.security_type import is_security_type
from AmazingData.query_api.base_data import BaseData

class SubscribeData(tgw.IPushSpi):
    pass
# WARNING: Decompyle incomplete

if __name__ == '__main__':
    login(username = TgwConfig.username, password = TgwConfig.password, host = TgwConfig.host, port = TgwConfig.port)
    base_data_object = BaseData()
    code_list = base_data_object.get_code_list(all_code = True)
    print()
    sub_data = SubscribeData()
    onSnapshot = (lambda data = None, period = None: print('600000: ', period, data))()
    onKline = (lambda data = None, period = None: print('600007: ', period, data))()
    onKline = (lambda data = None, period = None: print('600008: ', period, data))()
    sub_data.run()
    return None
