# 银河数据库 · 局域网桥接方案

> 解决：Mac（Apple Silicon）无法运行银河「星耀数智」x86 SDK（tgw/AmazingData 仅提供
> Windows/Linux x64 二进制）→ 在 **Windows 机器部署桥接服务**，Mac 通过局域网直接取数。

## 三分钟上手

**① Windows 机（桥接服务端）**
```bat
cd bridge-server
install.bat          REM 一键装依赖+银河wheel+自检
notepad config.ini   REM ★ 填写 [galaxy] 账号/密码/IP/端口
run_server.bat       REM 启动，看到“登录成功”即可
```

**② Mac（客户端）**
```bash
cd bridge-client && pip install -e .
export BRIDGE_URL=http://<Windows机IP>:8900
export BRIDGE_API_KEY=<config.ini里设置的api_key>
python examples/example_basic.py
```

Mac 上写策略的体验与原生 AmazingData 几乎一致：

```python
from galaxy_bridge import GalaxyBridgeClient
c = GalaxyBridgeClient("http://192.168.1.50:8900", api_key="...")
etf    = c.base_data.get_code_list(security_type="EXTRA_ETF")   # DataFrame/list 原样还原
kline5 = c.market_data.query_kline(code_list=["600519.SH"],
                                   begin_date=20240101, end_date=20240201,
                                   period="min5")
```

## 目录结构

| 路径 | 说明 |
|---|---|
| `docs/SDK调研报告.md` | 148 页开发手册完整梳理：登录机制、~60 个接口、枚举、缓存方案、坑 |
| `docs/方案设计.md` | 架构、协议、选型对比、安全与合规 |
| `docs/Windows部署指南.md` | Windows 侧逐步部署 + 防火墙 + 自启 + 排错 |
| `bridge-server/` | Windows 桥接服务（FastAPI）：`config.ini` 为默认登录信息 |
| `bridge-client/` | Mac 客户端库（pip 安装）+ 示例 |
| `tests/test_e2e_stub.py` | 端到端回归测试（桩替代银河 SDK，**无需真实账号**即可验证全链路）|
| `tools/check_wheels.py` | wheel 与 Python 版本匹配校验 |

## 核心特性

- **全接口覆盖**：通用 `/call/{group}/{method}` 分发，BaseData/InfoData/MarketData/Download 约 60 个方法全部可用，DataFrame 在 Mac 端原样还原（含 datetime 列类型）
- **实时行情**：WebSocket 广播 + 每客户端独立过滤；断线自动重连；动态增删订阅无需重启
- **断线自愈**：银河连接异常自动标记离线并后台重连；请求不挂死，返回结构化错误
- **本地缓存托管**：hdf5 缓存路径由 Windows 服务端统一管理，享受增量加速，Mac 无感知
- **安全**：X-API-Key 鉴权、账号脱敏展示、防火墙限局域网、密码仅存桥接机本地

## 重要提醒（合规）

1. 账号**仅供本人使用**——本桥接仅用于打通本人名下的 Mac 与 Windows 设备；
   不得对外提供服务、不得向第三方转发数据。
2. 同一账号多人同时登录可能被银河停止服务 → 桥接机应独占账号（已内置 `force_logout`）。
3. `config.ini` 含密码，注意文件权限，勿提交 git / 上传网盘。
