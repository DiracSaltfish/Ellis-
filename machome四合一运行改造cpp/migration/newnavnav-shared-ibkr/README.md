# newnavnav 单一 IBKR 行情会话迁移覆盖层

这是一份可独立审核、测试和回滚的迁移覆盖层。它不会被构建脚本自动写入
`/Users/ellis/newnavnav`，也不会启动任何网站 WebSocket/HTTP 上传。

## 目标架构

```text
IBKR TWS 127.0.0.1:7496
          │ 唯一长连接，官方 C++ EClientSocket/EReader/EWrapper
machome-ibkr-bridge
          │ owner-only Unix socket，动态订阅租约 + 批量快照
          ├─ XOP SMART / OVERNIGHT 上传器进程
          ├─ 159605 三十成分进程
          ├─ 中概 ETF US 成分进程
          ├─ INDA / NIFTY 进程
          ├─ NQ / ES / N225M / FDXM 进程
          └─ Sina WS 中的美股持仓行情
```

C++ bridge 是唯一长期持有 TWS 行情会话的进程。八个 Python 业务入口仍是独立
进程，只通过本机 Unix socket 取行情：它们不共享一个 GIL，单个业务进程计算慢不会
阻塞其他上传器。bridge 的 TWS EReader 线程、Qt 控制线程和每客户端有界写缓冲再次
隔离了慢消费者。

## 已覆盖的八个入口

| 覆盖脚本 | 长连行情 | 保留的短连能力 |
|---|---|---|
| `private_valuation_uploader.py` | XOP SMART | 无 |
| `private_513350_valuation_uploader.py` | XOP OVERNIGHT | 已完成 RTH 历史参考 |
| `private_xop_family_uploader.py` | XOP SMART + OVERNIGHT | 已完成 RTH 历史参考 |
| `private_159605_valuation_uploader.py` | 30 个 PCF 成分，单次批量 poll | 无 |
| `private_china_internet_valuation_uploader.py` | US OVERNIGHT 成分，单次批量 poll | CN/HK 继续使用 Sina |
| `private_164824_valuation_uploader.py` | INDA + 已解析 NIFTY 合约 | NIFTY 合约解析及历史 bars |
| `private_nasdaq_valuation_uploader.py` | NQ/ES/N225M/FDXM 已解析近月合约 | 近月解析及历史 close |
| `ib_us_uploader_support.py`（Sina WS） | 美股持仓 SMART 批量行情 | 分钟补齐、收盘价和估值锚点，均按请求断开 |

保留的 `ib_insync` 只做低频合约解析或历史查询，请求完成后立即断开；它不再持有
实时 `reqMktData` 长连。后续可以在不改变上传器业务公式的前提下，给 bridge 增加
`resolve_contract` / `historical_bars` 本机协议，再彻底删除 Python 中的 `ib_insync`。

bridge 示例配置使用 `market_data_type=3`。对有实时权限的合约，TWS 仍会回传 live；
无权限时允许回传 delayed，上层仍根据 `market_data_type` 做严格的可操作性判定。

## 本地测试

不启动上传器 `main()`，只运行单元/桥接测试：

```bash
/Users/ellis/miniconda3/bin/python3 \
  migration/newnavnav-shared-ibkr/run_regression_suite.py
```

要包含 native bridge Unix-socket 租约与慢客户端集成测试：

```bash
/Users/ellis/miniconda3/bin/python3 \
  migration/newnavnav-shared-ibkr/run_regression_suite.py \
  --bridge-binary build-cpp-native-334/machome-ibkr-bridge
```

实机 TWS 烟雾测试只应启动 `machome-ibkr-bridge` 和本地客户端探针，不应运行任何
uploader `run()`，以确保网站上传为零。

## 安装与回滚

`install-overlay.sh` 默认只打印计划。安装前必须先启动 native bridge，并在与 uploader
相同账户下确认 socket 权限为 `0600`。

```bash
# 只读预览
./migration/newnavnav-shared-ibkr/install-overlay.sh \
  --target-root /Users/ellis/newnavnav

# 在维护窗口中手动确认后才执行
./migration/newnavnav-shared-ibkr/install-overlay.sh --apply \
  --target-root /Users/ellis/newnavnav \
  --python /Users/ellis/miniconda3/bin/python3
```

apply 会先将目标脚本复制到
`scripts/.machome-ibkr-overlay-backups/<UTC timestamp>/`，然后原子级替换九个 Python 文件并运行
`py_compile`。回滚时停止对应 uploader，将该备份目录内的八个旧脚本复制回
`scripts/`，再启动旧 uploader；新增的本地客户文件可保留，它不会自行运行。

### 切换验收门禁

- bridge `status.ready=true` 且只有一个 TWS client ID；
- 相同合约被多 uploader 租用时 `created=false`，TWS 订阅数不增加；
- 任一 uploader 被暂停/卡住时，其他 uploader 的 poll 延迟和 sequence 仍持续正常；
- 八组 payload 在 shadow 对比中价格、时间戳、市场数据类型和精度完全等价；
- 重连 TWS 后 pinned + dynamic 租约全部自动重放；
- 无网站 token 时也能独立完成本地行情验收，且网站收到的测试上传数为零。
