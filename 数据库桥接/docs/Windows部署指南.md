# Windows 桥接机部署指南

> 适用：一台与 Mac 同处局域网的 **Windows 10/11 x64** 机器。
> 全程约 10 分钟（不含 Python 安装）。

## 0. 前置条件

| 项 | 要求 |
|---|---|
| 系统 | Windows 10 / 11 **64 位** |
| Python | **3.12 或 3.13 x64**（官网 python.org 安装包，勾选 *Add python.exe to PATH*） |
| 银河账号 | 联系开户营业部开通"星耀数智"权限，拿到 `tgw_` 开头的账号、密码、服务器 IP、端口 |
| 网络 | 该机能访问银河服务器；与 Mac 同一局域网网段 |

## 1. 拷贝部署包

把整个 `数据库桥接` 文件夹拷贝到 Windows 机，例如 `D:\数据库桥接\`。
后续命令都在 `D:\数据库桥接\bridge-server\` 下执行。

## 2. 一键安装

双击 `install.bat`（或在 CMD 中运行）。脚本会：
1. 校验 Python 版本；
2. 安装 fastapi/uvicorn/pandas 等依赖；
3. 安装 `AmazingData-1.1.9-cpXY.whl` 与 `tgw-1.0.9.2` wheel；
4. 运行 `test_smoke.py` 自检。

全部 `[ OK ]` 即通过。若失败，按提示截图报错排查。

## 3. ★ 填写默认登录信息

用记事本编辑 `bridge-server\config.ini` 的 `[galaxy]` 节：

```ini
username = tgw_123456          ; 营业部给的账号
password = 你的密码
host     = xxx.xxx.xxx.xxx     ; 银河数据服务器 IP
port     = xxxx                ; 端口
api_mode = kInternetMode       ; 个人/办公网络固定填这个
force_logout = true
```

同时建议把 `[bridge] api_key` 改成一段随机字符串（32 位左右），
Mac 客户端请求时需要携带它。

> 安全：此文件含密码，请确认目录仅本人账户可读（右键→属性→安全），
> 且不要把该文件夹提交到 git 或网盘。

可选：设置开机自动订阅的行情（取消注释并改成自己的代码）：

```ini
subscribe = [{"period":"snapshot","code_list":["510300.SH","510500.SH"]}]
```

## 4. 启动服务

双击 `run_server.bat`。看到类似日志即成功：

```
galaxy-bridge 启动
目标服务器 x.x.x.x:xxxx（账号 tgw_****）
局域网客户端请连接：http://192.168.x.x:8900
INFO ... 银河 SDK 登录成功（tgw_****@x.x.x.x:xxxx）
```

验证（本机浏览器打开）：`http://127.0.0.1:8900/health`
应返回 `"logged_in": true`。

## 5. 放行防火墙（允许 Mac 访问）

管理员 PowerShell 执行一次即可：

```powershell
New-NetFirewallRule -DisplayName "galaxy-bridge 8900" -Direction Inbound `
    -Protocol TCP -LocalPort 8900 -RemoteAddress LocalSubnet -Action Allow
```

> `-RemoteAddress LocalSubnet` 已把访问限制在本局域网内，切勿开放到公网，
> 也不要在路由器上做端口映射。

## 6. 开机自启（可选）

右键 `register_task.ps1` → *使用 PowerShell 运行*。
之后该机每次登录都会自动拉起桥接服务。
删除自启：`schtasks /Delete /TN GalaxyBridgeServer /F`

## 7. Mac 侧连接测试

```bash
cd bridge-client && pip install -e .
export BRIDGE_URL="http://<Windows机IP>:8900"
export BRIDGE_API_KEY="你在 config.ini 设置的 api_key"
python examples/example_basic.py
python examples/example_history.py
python examples/example_realtime.py     # 交易时段才有实时推送
```

## 8. 故障排查

| 现象 | 处理 |
|---|---|
| `install.bat` 报 Python 版本不对 | 装 3.12/3.13 **x64**，重开终端再试 |
| 自检 `tgw 导入 [FAIL] this system is not supported` | 用了 32 位 Python 或 ARM 版 Windows → 必须 x64 |
| 日志反复出现登录失败 | 核对账号前缀 `tgw_`、密码、IP、端口；确认营业部已开通权限且未到期 |
| 提示同账号被顶号 | 桥接机独占该账号；`force_logout=true` 已配置时仍冲突则联系营业部 |
| `/health` 正常但 Mac 连不上 | 检查第 5 步防火墙规则；确认两机在同一网段、无 AP 隔离 |
| 实时行情无推送 | 仅交易时段有数据；先调 `sub_add` 登记订阅或检查 `config.ini` subscribe 配置 |
| 服务卡死/异常 | 查看 `logs\bridge.log`；重启 `run_server.bat`；必要时 `POST /admin/relogin` |

管理接口：
- `GET  /health` —— 探活（无需令牌）
- `POST /admin/login` —— 手动立即重登（带 X-API-Key）
- `GET  /api/v1/sub/list` —— 查看当前上游订阅
