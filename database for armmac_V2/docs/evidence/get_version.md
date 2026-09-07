# GetVersion 对齐证据

- Scope: `GetVersion()` 纯本地同步调用；不包含登录、coloc 或服务端协商版本。
- PDF: 《中国银河证券格物金融服务平台(TGW)开发手册(C++版)》PDF 第 24 页（正文第 16 页）；公开签名 `const char* IGMDApi::GetVersion()`，互联网与托管模式共用。
- Header delta: V1.0.8 Linux/Windows 头文件与 PDF 的无入参、字符串返回契约一致；Python 官方 wrapper 返回 `str`。
- Linux oracle: 2026-08-30 在 `bj` 的官方 wheel 上、未登录状态调用；返回类型 `str`，值为 `V4.3.0.260626-rc2.0-YHZQ`。调用前 `galaxy-relay` 为 `inactive`，调用不启动服务或网络会话。
- Wire: 无；该接口为纯本地版本查询。该版本字符串同时是当前兼容层 `ReqLogon.Version` 的默认值。
- Arm: `src/python/tgw_macos/_backend.py` 提供单一版本来源；`src/python/tgw_macos/interface.py` 的 `GetVersion()` 不再初始化 backend 或返回兼容层/机器架构字符串。
- Tests: 覆盖官方格式、精确默认值、无 backend 初始化，以及 `TGW_CLIENT_VERSION` 覆盖时查询值与登录值同源。
- Live diff: Linux 官方与 Mac 默认值均为 `str`，默认值精确相同；接口均可在登录前调用。
- Cleanup: 未创建远端临时文件；未启动 `galaxy-relay`，服务保持 `inactive`。
- Proposed status: `LIVE_ALIGNED(local version string)`。
- Open risks: 后续厂商 wheel 升级时必须同步更新默认版本字符串；环境变量覆盖属于本兼容层运维能力，不代表官方 SDK 可修改编译版本。
