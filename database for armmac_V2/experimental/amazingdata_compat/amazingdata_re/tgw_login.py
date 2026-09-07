# 登录流程重建 —— 行为对齐 decompiled/AmazingData/login/tgw_login.py
#
# 原版关键行为(反编译确认):
#   1) 用户名必须以 'tgw_' 开头(前缀校验在 Python 层, C++ 层无)
#   2) 组装 Cfg{username,password,server_vip,server_port,force_logout}
#   3) Login 失败且日志SPI报 max_limitation(顶号) → force_logout=True 重试≤5次/间隔2s
#   4) 全部失败 → print('login fail') 后 exit(0) 杀进程 ← 桥接服务必须绕开
import time


class _LogSpi:
    def __init__(self):
        self.max_limitation = False
        self.lines = []

    def on_log(self, level, msg):
        self.lines.append((level, msg))
        print(f"[AmazingData][log{level}] {msg}")


def set_cfg(username, password, host, port, api_mode="kInternetMode",
            force_logout=False):
    """组装登录配置。返回 (cfg_dict, api_mode枚举值, log_spi)。"""
    spi = _LogSpi()
    cfg = {
        "username": username,
        "password": password,
        "server_vip": host,
        "server_port": int(port),
        "force_logout": bool(force_logout),
    }
    mode_enum = {"kInternetMode": 2, "kColocationMode": 1}.get(api_mode)
    if mode_enum is None:
        raise ValueError(f"未知 api_mode: {api_mode}")
    return cfg, mode_enum, spi


def login(username, password, host, port, api_mode="kInternetMode",
          allow_exit=False):
    """自管重试版登录(不 exit), 返回 (ok: bool, detail: str)。

    与原版差异:
      - 不做 tgw_ 前缀硬校验(交由服务端裁决, 便于携带真实账号实测)
      - 失败不 exit() —— 由调用方决定重试策略
      - max_limitation 场景保留 force_logout 顶号重试语义
    """
    if not username or not password:
        return False, "username/password 为空"

    force_tried = False
    last_err = ""
    # 第一轮: 正常登录; 若顶号 → 第二轮 force_logout 重试 ≤5 次
    for attempt in range(6):
        force = force_tried and attempt > 1
        cfg, mode, spi = set_cfg(username, password, host, port,
                                 api_mode=api_mode, force_logout=force)
        try:
            from tgw_macos import interface as tgw_i
            ok = tgw_i.Login(_WrapCfg(cfg), mode)
        except Exception as e:                       # 网络层异常也计入重试
            ok, last_err = False, repr(e)
            time.sleep(1.0)
            continue

        if ok:
            return True, f"login success (attempt={attempt}, force={force})"
        if spi.max_limitation and not force:
            force_tried = True
            print("[AmazingData] 检测到顶号上限, 切换 force_logout 重试")
        last_err = "login rejected by server/state-machine"
        time.sleep(2)

    msg = f"login fail: {last_err}"
    print(f"[AmazingData] {msg}")
    if allow_exit:            # 仅显式允许时才复刻原版的 exit 行为
        exit(0)
    return False, msg


class _WrapCfg:
    """把 dict 适配成 interface.Login 期望的属性访问。"""
    def __init__(self, d):
        self.__dict__.update(d)
        self.force_logout = bool(d.get("force_logout"))


def logout(username=None):
    """原版语义: logout(username) → tgw.Close()。"""
    from tgw_macos import interface as tgw_i
    tgw_i.Close()
    return True
