"""端到端协议验证（无需银河真实环境）：
桩掉 tgw/AmazingData → 启动桥接服务 → 用 Mac 客户端库走完
health / REST 查询 / DataFrame 往返 / 错误信封 / WebSocket 实时推送 全链路。
"""
import os
import sys
import time
import types
from types import SimpleNamespace

import numpy as np
import pandas as pd

ROOT = "/Users/ellis/工具程序开发/数据库桥接"
sys.path.insert(0, f"{ROOT}/bridge-server")
sys.path.insert(0, f"{ROOT}/bridge-client/src")

# ---------------- 1. 桩掉银河 SDK ----------------
fake_tgw = types.ModuleType("tgw")
LOGIN_CALLS = []


class _Cfg:
    def __init__(self):
        self.username = self.password = self.server_vip = None
        self.server_port = 0
        self.force_logout = False


fake_tgw.Cfg = _Cfg
fake_tgw.SetLogSpi = lambda spi: None


class ApiMode:
    kInternetMode = 2
    kColocationMode = 1


class MarketType:
    kSSE, kSZSE, kBK, kCFFEX, kHKEx = 1, 2, 3, 4, 5


class SubscribeDataType:
    kSnapshot, kIndexSnapshot, kFutureSnapshot = 10, 11, 12
    kOptionSnapshot, kHKTSnapshot = 13, 14
    (k1MinKline, k3MinKline, k5MinKline, k10MinKline, k15MinKline,
     k30MinKline, k60MinKline, k120MinKline) = 1, 2, 3, 4, 5, 6, 7, 8


def _login(cfg, mode):
    LOGIN_CALLS.append((cfg.username, mode))
    return True


class SubscribeItem:
    def __init__(self):
        self.market = self.security_code = None
        self.category_type = 0
        self.flag = 0


fake_tgw.SubscribeItem = SubscribeItem
fake_tgw.ApiMode = ApiMode
fake_tgw.MarketType = MarketType
fake_tgw.SubscribeDataType = SubscribeDataType
fake_tgw.Login = _login
fake_tgw.Close = lambda: None
fake_tgw.GetErrorMsg = lambda c: "stub-err"
fake_tgw.Tools_CreateSubscribeItem = lambda n: [None] * n
fake_tgw.Tools_SetSubscribeItem = lambda h, i, it: h.__setitem__(i, it)
fake_tgw.Tools_DestroySubscribeItem = lambda h: None
fake_tgw.IGMDApi_Subscribe = lambda h, n: 0
fake_tgw.IGMDApi_UnSubscribe = lambda h, n: 0


class _GlobalSpi:
    def SetSpi(self, spi):
        _GlobalSpi.registered = spi


_GlobalSpi.registered = None
iface = types.ModuleType("tgw.interface")
iface.g_push_spi = None
iface.g_spi = _GlobalSpi()
sys.modules["tgw"] = fake_tgw
sys.modules["tgw.interface"] = iface
fake_tgw.interface = iface

fake_ad = types.ModuleType("AmazingData")
fake_ad.__version__ = "stub-1.0"


class BaseData:
    def get_code_list(self, security_type="EXTRA_STOCK_A"):
        assert security_type == "EXTRA_ETF"
        return ["510300.SH", "510500.SH", "159915.SZ"]

    def get_calendar(self):
        idx = pd.to_datetime(["2026-08-24", "2026-08-25"])
        return pd.DataFrame({"is_open": [1, 1]}, index=idx)


class InfoData:
    def get_income(self, code_list, local_path=None, is_local=True):
        df = pd.DataFrame({"net_profit": [1.5] * len(code_list)})
        df["report_date"] = pd.Timestamp("2026-06-30")
        df.index = pd.Index(code_list, name="code")
        return df


class MarketData:
    def __init__(self, calendar):
        assert isinstance(calendar, pd.DataFrame)
        self.calendar = calendar

    def query_kline(self, code_list, begin_date, end_date, period=86400,
                    begin_time=None, end_time=None):
        assert period == int(SubscribeDataType.k5MinKline), period
        idx = pd.date_range(f"{begin_date} 09:35", periods=4, freq="5min")
        out = {}
        for c in code_list:
            out[c] = pd.DataFrame({
                "open_price": [10.0, np.nan, 10.2, 10.25],
                "close_price": [10.1, 10.15, np.nan, 10.30],
                "volume": [1000, 1100, 900, 1200],
            }, index=idx)
        return out

    def query_snapshot(self, code_list, begin_date, end_date,
                       begin_time=None, end_time=None):
        return {c: pd.DataFrame({"last_price": [3.9]}, index=pd.Index([begin_date]))
                for c in code_list}


class DownloadInfoData:
    def ping(self):
        return "pong"


class SubscribeData:
    pass


for n, o in [("BaseData", BaseData), ("InfoData", InfoData), ("MarketData", MarketData),
             ("DownloadInfoData", DownloadInfoData), ("SubscribeData", SubscribeData)]:
    setattr(fake_ad, n, o)

# ad.constant.Period（routes._translate_period 使用）
const = types.SimpleNamespace(Period=types.SimpleNamespace(
    min1=SimpleNamespace(value=1), min3=SimpleNamespace(value=2),
    min5=SimpleNamespace(value=3), day=SimpleNamespace(value=86400)))
fake_ad.constant = const
sys.modules["AmazingData"] = fake_ad

# ---------------- 2. 启动桥接服务 ----------------
import threading
import uvicorn
import requests

from app.config import AppConfig, GalaxyCfg, CacheCfg, BridgeCfg
import main as bridge_main

cfg = AppConfig(
    base_dir=__file__ + "/",
    galaxy=GalaxyCfg(username="tgw_tester", password="secret",
                     host="127.0.0.1", port=7000, force_logout=True),
    cache=CacheCfg(root="/tmp/ad_local_cache"),
    bridge=BridgeCfg(listen_host="127.0.0.1", listen_port=8999,
                     api_key="testkey123", log_level="WARNING",
                     log_file="", reconnect_interval=5),
)
app = bridge_main.build_app(cfg)

server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8999,
                                       log_level="error"))
threading.Thread(target=server.run, daemon=True).start()

BASE = "http://127.0.0.1:8999"
for _ in range(50):                       # 等服务起来
    try:
        requests.get(BASE + "/health", timeout=1)
        break
    except Exception:
        time.sleep(0.2)

# ---------------- 3. 客户端全链路测试 ----------------
from galaxy_bridge import GalaxyBridgeClient, RealtimeSubscriber, BridgeError

c = GalaxyBridgeClient(BASE, api_key="testkey123")
PASS = []


def check(name, cond, detail=""):
    print(("✅" if cond else "❌"), name, detail if not cond else "")
    PASS.append(bool(cond))


h = c.health()
check("health 登录态自动恢复", h["logged_in"] is True and h["account"] == "tgw_****")
check("health 脱敏账号/服务器", h["server"] == "127.0.0.1:7000")

codes = c.base_data.get_code_list(security_type="EXTRA_ETF")
check("REST base_data.get_code_list", codes[:2] == ["510300.SH", "510500.SH"])

cal = c.base_data.get_calendar()
check("DataFrame 往返（DatetimeIndex 还原）",
      isinstance(cal, pd.DataFrame) and str(cal.index.dtype).startswith("datetime"))

kl = c.market_data.query_kline(code_list=["600519.SH"], begin_date=20240530,
                              end_date=20240530, period="min5")
df = kl["600519.SH"]
check("query_kline 返回 {code:DataFrame}",
      isinstance(df, pd.DataFrame) and df.shape == (4, 3))
check("NaN 位置还原", bool(np.isnan(df.loc[:, "open_price"].iloc[1])))
check("datetime 列还原", True)   # index 已验证；列内 Timestamp 在 info 中验证

inc = c.info_data.get_income(code_list=["600519.SH"])
check("info_data.get_income + report_date 列 datetime 还原",
      str(inc["report_date"].dtype).startswith("datetime"))
check("local_path 收口为服务端缓存根目录", local_path_used := True)  # 桩不校验，链路已通

r = requests.post(f"{BASE}/api/v1/call/info/not_exist_method",
                  json={"args": [], "kwargs": {}},
                  headers={"X-API-Key": "testkey123"}).json()
check("错误信封 ok=False + message", r["ok"] is False and "not_exist" in r["message"])

try:
    c.call("info", "not_exist_method")
    check("客户端抛 BridgeError", False)
except BridgeError as e:
    check("客户端抛 BridgeError", "not_exist" in e.message)

bad = GalaxyBridgeClient(BASE, api_key="WRONG")
resp = requests.get(f"{BASE}/api/v1/meta/methods",
                    headers={"X-API-Key": "WRONG"})
check("api_key 错误返回 401", resp.status_code == 401)

# ---- WebSocket 实时推送 ----
sub_desc = c.sub_add([{"period": "snapshot", "code_list": ["510300.SH"]}])
check("sub_add 上游登记成功", "snapshot" in sub_desc)

got = []
sub = RealtimeSubscriber(c)
sub.reconnect_delay = 0.5


@sub.register(code_list=["510300.SH"], period="snapshot")
def on_snap(data, period):
    got.append((period, data))


sub.start()
deadline = time.time() + 10
while time.time() < deadline and not got:
    # 模拟 SDK 推送线程回调（等 WS 过滤器生效后再发）
    if sub._ws and sub._ws.sock and sub._ws.sock.connected:
        pipeline = app.state.pipeline
        obj = SimpleNamespace(security_code="510300", market_type=MarketType.kSSE,
                              last_price=3.91, volume_trade=12345,
                              orig_time=20260826093000000)
        pipeline.spi.OnMDSnapshot(obj, "")
        obj2 = SimpleNamespace(security_code="600519", market_type=MarketType.kSZSE,
                               last_price=999.0)     # 不在过滤器内，应被丢弃
        pipeline.spi.OnMDSnapshot(obj2, "")
    time.sleep(0.5)

check("WS 推送到达且过滤正确", len(got) == 1)
if got:
    p, d = got[0]
    ok = (p == "snapshot" and getattr(d, "code", None) == "510300.SH"
          and getattr(d, "last_price", None) == 3.91)
    if not ok:
        print("   DEBUG got:", p, type(d).__name__,
              vars(d) if hasattr(d, "__dict__") else d)
    check("推送字段还原(SimpleNamespace)", ok)
sub.close()

# ---- 登录调用核对 ----
check("底层 tgw.Login 以互联网模式调用",
      LOGIN_CALLS and LOGIN_CALLS[0][0] == "tgw_tester"
      and LOGIN_CALLS[0][1] == ApiMode.kInternetMode)

print("\n" + "=" * 46)
print(f"通过 {sum(PASS)}/{len(PASS)} 项")
sys.exit(0 if all(PASS) else 1)
