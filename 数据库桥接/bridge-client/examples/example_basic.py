"""基础示例：探活、代码表、证券基础信息。

运行前：
    export BRIDGE_URL="http://192.168.1.50:8900"
    export BRIDGE_API_KEY="你的令牌"        # 服务端未设置 api_key 可省略
"""
import os

from galaxy_bridge import BridgeError, GalaxyBridgeClient

URL = os.environ.get("BRIDGE_URL", "http://192.168.1.50:8900")
KEY = os.environ.get("BRIDGE_API_KEY")

c = GalaxyBridgeClient(URL, api_key=KEY)

print("== health ==")
h = c.health()
print(f"登录状态: {h.get('logged_in')}  账号: {h.get('account')}  "
      f"服务器: {h.get('server')}  订阅: {h.get('subscriptions')}")
if not h.get("logged_in"):
    print("桥接机尚未登录银河（检查 config.ini 或调用 c.relogin()）")

print("\n== 代码表（沪深ETF）==")
etfs = c.base_data.get_code_list(security_type="EXTRA_ETF")
print(f"共 {len(etfs)} 只，示例: {etfs[:5]}")

print("\n== 交易日历 ==")
cal = c.base_data.get_calendar()
print(cal.tail(3))

print("\n== 证券基础信息（前3只）==")
try:
    info = c.info_data.get_stock_basic(code_list=list(etfs[:3]))
    print(info if not hasattr(info, "head") else info.head())
except BridgeError as e:
    print("查询失败:", e)

print("\n== 服务端可用方法一览 ==")
for g, names in c.methods().items():
    print(f"[{g}] {len(names)} 个方法，如: {names[:6]}")
