# Login / Close 同解释器复登入对齐证据

- Scope: internet mode 的 `Login -> Close -> Login -> Close` 生命周期；不包含查询、订阅、自动重连或 coloc。
- PDF: 《中国银河证券格物金融服务平台(TGW)开发手册(C++版)》PDF 第 24-25 页（正文第 16-17 页）；`Init/Login` 建立 API 会话，`Release/Close` 释放连接与回调生命周期。
- Header delta: 当前发行头文件的 `IGMDApi::Release()` 释放 API 对象；Python 官方 wrapper 暴露全局 `Login`/`Close`。本范围不涉及结构 ABI 差异。
- Linux oracle: 2026-08-30 使用独立 Linux 测试账号和远端受保护密码串行运行两次。官方
  第一轮登录成功并 `Close()`；同进程第二轮登录返回 `False`。加入 5 秒冷却后重新执行，
  结果仍为 `[True, False]`。未发查询/订阅；这证明当前官方 Python wrapper 本身没有给出
  成功的同进程复登入基线。
- Wire: 复登入不引入新业务 wire；每次 `Login` 应创建独立 transport，`Close` 应关闭并释放前一 transport。
- Arm: 修复前 `interface.Close()` 只把 backend 置为 `CLOSED`，全局仍引用它，下一次 `Login()` 会在 `init()` 必然失败。修复后 `Close()` 在 `finally` 中释放全局 backend；backend 清空内存中的密码和登录响应，下一次登录创建新 transport。
- Tests: 覆盖同解释器两轮成功的模拟状态机、新旧 backend 身份不同、密码与登录响应清理、底层 close 抛错时全局仍释放，以及重复 `Close()` 无副作用。
- Live diff: Mac 使用另一独立账号和本地 0600 受保护密码、相同 5 秒冷却，结果为
  `[True, True]`；两轮 backend identity 不同，每次 `Close()` 后全局 backend 均为 `None`。
  因 Linux 官方为 `[True, False]`，Mac 虽已实际修复为可复登入，但不能标为 Linux
  `LIVE_ALIGNED`，只能保留精确的 `ARM_IMPLEMENTED(re-entry; Mac live passed)`。
- Cleanup: Linux oracle 使用 `/tmp/tgw_official_acceptance.lock` 串行；未发业务请求。远端临时脚本已删除，`galaxy-relay` 保持 `inactive`；本地 oracle 不保存凭据。
- Proposed status: `ARM_IMPLEMENTED(login/close re-entry; Mac live passed, Linux official differs)`。
- Open risks: Linux 官方第二轮失败原因（wrapper 全局状态或服务端释放时序）未提供错误码；
  Mac 只验两轮和 5 秒冷却。自动重连和订阅恢复不在本任务范围。
