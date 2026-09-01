# ETF 溢价率拉升监控（原生 C++ TGW 分支）

本目录是本机改造分支。A 端 TGW 登录、鉴权、订阅、退订、收包和 Zstandard 解压均由
`etf-premium-tgw` 与 `tgw_cpp` 原生 C++ 实现；A-core、8421、19195 以及 B 端协议保持不变。

当前版本只在本机完成离线与仿真验证，不代表已经部署到 machome，也不代表已经通过盘中
实时订阅验收。构建、接口、数据类型门禁和周一验收流程见 `docs/21_原生C++TGW改造设计与周一验收.md`
与 `docs/22_原生TGW数据类型强化审查.md`。

项目同时提供 `etf-premium-tgw-audit`，可用与生产收包边界同一套 C++
simdjson 规则扫描脱敏 JSONL，用于盘中发现字段、类型或 full/delta 结构漂移；它支持
直接 event、A-core 持久化包裹，以及 `zstd -dc ... | etf-premium-tgw-audit -` 流式输入。
