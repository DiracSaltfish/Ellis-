"""galaxy-bridge-client：Mac 侧访问局域网内 Windows 桥接机的银河数据。

快速上手：
    from galaxy_bridge import GalaxyBridgeClient
    c = GalaxyBridgeClient("http://192.168.1.50:8900", api_key="...")
    codes = c.base_data.get_code_list(security_type="EXTRA_ETF")   # 同原生 API
    klines = c.market_data.query_kline(code_list=codes[:5],
                                       begin_date=20240101, end_date=20240201,
                                       period="min5")
"""
from .client import GalaxyBridgeClient, BridgeError
from .realtime import RealtimeSubscriber

__all__ = ["GalaxyBridgeClient", "BridgeError", "RealtimeSubscriber"]
__version__ = "1.0.0"
