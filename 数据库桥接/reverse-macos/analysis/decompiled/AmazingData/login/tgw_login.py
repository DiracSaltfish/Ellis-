# Source Generated with Decompyle++
# File: tgw_login.pyc (Python 3.12)

import time
import tgw

class TgwLogSpi(tgw.ILogSpi):
    pass
# WARNING: Decompyle incomplete


def set_cfg(username, password, host, port, api_mode, kColocationMode_para, force_logout = ('kInternetMode', None, False)):
    tgw.Close()
    log_spi = TgwLogSpi()
    tgw.SetLogSpi(log_spi)
    cfg = tgw.Cfg()
    cfg.username = username
    cfg.password = password
    cfg.server_vip = host
    cfg.server_port = port
    cfg.force_logout = force_logout
    if api_mode == 'kInternetMode':
        api_mode = tgw.ApiMode.kInternetMode
# WARNING: Decompyle incomplete


def login(username, password, host, port, api_mode, kColocationMode_para = ('kInternetMode', None)):
    if isinstance(username, str) and username.startswith('tgw_'):
        raise Exception('username is illegal')
    (cfg, api_mode, log_spi) = set_cfg(username, password, host, port, api_mode = api_mode, kColocationMode_para = kColocationMode_para)
    success = tgw.Login(cfg, api_mode)
    if not success:
        if log_spi.max_limitation:
            force_success = False
            for i in range(5):
                (cfg, api_mode, log_spi) = set_cfg(username, password, host, port, api_mode = api_mode, kColocationMode_para = kColocationMode_para, force_logout = True)
                force_success = tgw.Login(cfg, api_mode)
                if force_success:
                    range(5)
                else:
                    time.sleep(2)
            if not force_success:
                print('login fail')
                exit(0)
                return success
            None('login success')
            return success
        None('login fail')
        exit(0)
        return success
    None('login success')
    return success


def logout(username):
    tgw.Close()


def update_password(username, old_password, new_password):
    up_password_req = tgw.UpdatePassWordReq()
    up_password_req.username = username
    up_password_req.old_password = old_password
    up_password_req.new_password = new_password
    ec = tgw.IGMDApi_UpdatePassWord(up_password_req)
    if ec != tgw.ErrorCode.kSuccess:
        print('UpdatePassWord failed, error code is : ', ec)
        return None
    print('UpdatePassWord success')

