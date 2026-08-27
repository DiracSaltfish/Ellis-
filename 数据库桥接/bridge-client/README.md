# galaxy-bridge-client（Mac 侧）

纯 Python 客户端，通过局域网访问 Windows 桥接机上的银河数据。
API 形态尽量对齐原生 `AmazingData`，返回值还原为 `pandas.DataFrame`。

## 安装（Mac）

```bash
cd bridge-client
pip install -e .
```

## 快速使用

```python
from galaxy_bridge import GalaxyBridgeClient

c = GalaxyBridgeClient("http://<Windows机IP>:8900", api_key="你的令牌")

# 与原生一致的分组调用（自动还原 DataFrame）
codes  = c.base_data.get_code_list(security_type="EXTRA_ETF")
klines = c.market_data.query_kline(code_list=["600519.SH"],
                                   begin_date=20240101, end_date=20240228,
                                   period="min5")
income = c.info_data.get_income(code_list=["600519.SH"])

# 或通用分发：任意服务端方法
res = c.call("info", "get_dividend", kwargs={"code_list": ["600519.SH"]})
```

## 实时行情

```python
from galaxy_bridge import GalaxyBridgeClient, RealtimeSubscriber

c   = GalaxyBridgeClient("http://<Windows机IP>:8900", api_key="...")
sub = RealtimeSubscriber(c)

@sub.register(code_list=["510300.SH"], period="snapshot")   # snapshot/min1/min5/...
def on_snap(data, period):
    print(vars(data))

sub.run(block=True)
```

- 服务端订阅登记：`c.sub_add([{"period":"snapshot","code_list":[...]}])`；
  客户端连接后也会自动补发所需订阅（幂等）。
- 支持断线自动重连；心跳由库内置。

## 注意事项

- `local_path` 参数无需传：hdf5 缓存目录由 Windows 服务端统一管理。
- 日线及以上周期无实时推送（SDK 仅支持分钟级推送），请用 `query_kline` 轮询。
- 遵守银河账号合规要求：仅本人局域网内使用。
