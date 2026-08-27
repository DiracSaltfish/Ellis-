# Source Generated with Decompyle++
# File: market_spi.pyc (Python 3.12)

import tgw
from AmazingData.utils.convert import get_code
from AmazingData.environment import EnvSnapshot, EnvKline

class SnapshotSpi(object):
    '''
    功能描述：查询快照spi，实现同步查询接口，回调函数中可取到入参
    '''
    
    def __init__(self, req):
        self._req = req

    
    def OnResponse(self, data, status):
        get_code(self._req.market_type, self._req.security_code) = EnvSnapshot, EnvSnapshot.req_list_len -= 1, .req_list_len
    # WARNING: Decompyle incomplete



class KlineSpi(object):
    '''
    功能描述：查询K线spi，实现同步查询接口，回调函数中可取到入参
    '''
    
    def __init__(self, req):
        self._req = req

    
    def OnResponse(self, data, status):
        get_code(self._req.market_type, self._req.security_code) = EnvKline, EnvKline.req_list_len -= 1, .req_list_len
    # WARNING: Decompyle incomplete


