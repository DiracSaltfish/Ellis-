"""历史行情示例：K线 / 快照，返回 pandas DataFrame。

时间参数约定（与原生一致）：
- 日期：8位整数，如 20240530
- K线时分：3~4位整数，如 930=09:30、1725=17:25
- 快照时分秒毫秒：8~9位整数，如 93000000=09:30:00.000
"""
import os

from galaxy_bridge import GalaxyBridgeClient
from galaxy_bridge.realtime import RealtimeSubscriber  # noqa: F401

URL = os.environ.get("BRIDGE_URL", "http://192.168.1.50:8900")
KEY = os.environ.get("BRIDGE_API_KEY")

c = GalaxyBridgeClient(URL, api_key=KEY)

codes = ["600519.SH", "000001.SZ", "510300.SH"]

print("== 日线 K 线 ==")
klines = c.market_data.query_kline(
    code_list=codes, begin_date=20240102, end_date=20240131, period="day")
for code, df in klines.items():
    print(f"--- {code}  shape={getattr(df, 'shape', None)}")
    print(getattr(df, "tail", lambda n: df)(3))
    break   # 打印一只示意

print("\n== 1分钟K线（单日）==")
m1 = c.query_kline(codes[:1], begin_date=20240530, end_date=20240530,
                   period="min1", begin_time=930, end_time=1130)
df = list(m1.values())[0]
print(type(df).__name__, getattr(df, "shape", ""))
print(getattr(df, "head", lambda n: df)())

print("\n== 历史快照（L1，指定时段）==")
snaps = c.query_snapshot(codes[:1], begin_date=20240530, end_date=20240530,
                         begin_time=93000000, end_time=100000000)
for code, obj in snaps.items():
    print(code, type(obj).__name__,
          (obj.shape if hasattr(obj, "shape") else len(obj)))

print("\n== 财务数据示例：利润表（本地缓存在 Windows 机上）==")
income = c.info_data.get_income(code_list=["600519.SH"])   # local_path 由服务端托管
print(income.tail(2) if hasattr(income, "tail") else type(income))
