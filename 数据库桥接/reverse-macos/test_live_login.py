#!/usr/bin/env python3
# test_live_login.py —— 真实账号登录实测（逆向重建成果验证）
#
# 测试矩阵:
#   A. TCP 可达性: 主/备两个 IP 逐一探测
#   B. C++ 骨架 arm64 二进制: tgw_demo 直连测试
#   C. Python 重建层完整链路: amazingdata_re.login → tgw_macos → libtgw_core.dylib
#   D. 数据拉取: 仅在登录状态机成功后尝试 QueryKline(骨架无私有协议, 预期空结果)
import configparser
import os
import socket
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "src_reconstructed", "python"))


def _strip_inline(v: str) -> str:
    return v.split("#", 1)[0].strip()


def load_account():
    cp = configparser.ConfigParser()
    cp.read(os.path.join(HERE, "config", "galaxy_account.ini"))
    raw_host = _strip_inline(cp["galaxy"]["host"])
    hosts = [h.strip() for h in raw_host.replace("，", " ").split() if h.strip()]
    return {
        "hosts": hosts,
        "port": cp.getint("galaxy", "port"),
        "username": _strip_inline(cp["galaxy"]["username"]),
        "password": cp["galaxy"]["password"].strip(),
        "api_mode": _strip_inline(cp["galaxy"]["api_mode"]),
    }


def tcp_probe(host, port, timeout=6.0):
    try:
        s = socket.create_connection((host, port), timeout=timeout)
        # 尝试读取服务端 banner(TLS 握手通常由客户端先发, 这里只做非阻塞嗅探)
        s.settimeout(2.0)
        try:
            banner = s.recv(64)
        except socket.timeout:
            banner = b""
        s.close()
        return True, f"TCP 连通 (server_banner={banner.hex() if banner else '<无响应数据>'})"
    except OSError as e:
        return False, repr(e)


def main():
    acc = load_account()
    hosts = acc["hosts"]
    print("=" * 62)
    print("A. TCP 可达性探测")
    alive = []
    for h in hosts:
        ok, detail = tcp_probe(h, acc["port"])
        print(f"   {h}:{acc['port']}  {'✓' if ok else '✗'}  {detail}")
        if ok:
            alive.append(h)

    target = alive[0] if alive else hosts[0]
    print(f"\nB. C++ arm64 骨架直连 ({target})")
    demo = os.path.join(HERE, "macos_build", "build", "tgw_demo")
    r = subprocess.run([demo, target, str(acc["port"]), acc["username"], acc["password"]],
                       capture_output=True, text=True, timeout=60)
    print("   " + r.stdout.replace("\n", "\n   ").rstrip())
    if r.returncode != 0:
        print(f"   [demo exit={r.returncode}]")

    print("\nC/D. Python 重建层完整链路 + 登录后数据拉取")
    from amazingdata_re.tgw_login import login
    ok, msg = login(acc["username"], acc["password"], target, acc["port"],
                    api_mode=acc["api_mode"])
    print(f"   login -> {ok} | {msg}")
    if ok:
        import tgw_macos.interface as tgw_i
        req = tgw_i._structures.ReqKline() if hasattr(tgw_i, "_structures") else None
        rows = tgw_i.QueryKline(None, {"codes": ["510300.SH"]})
        print(f"   query_kline -> {len(rows) if hasattr(rows, '__len__') else rows} 行")
    print("=" * 62)
    print("结论: 见上方逐项输出。骨架无厂商私有线上协议，应用层鉴权预期无法通过。")


if __name__ == "__main__":
    main()
