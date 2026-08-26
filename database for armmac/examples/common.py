from __future__ import annotations

import configparser
from pathlib import Path

import tgw_macos as tgw


def login_from_config(path: str | Path = "config/galaxy_account.ini") -> bool:
    parser = configparser.ConfigParser()
    with Path(path).open(encoding="utf-8") as stream:
        parser.read_file(stream)
    section = parser["galaxy"]
    host = section["host"].split("#", 1)[0].replace("，", " ").split()[0]
    mode = getattr(tgw.ApiMode, section.get("api_mode", "kInternetMode").strip())
    cfg = tgw.Cfg().set(
        server_vip=host,
        server_port=section.getint("port"),
        username=section["username"].split("#", 1)[0].strip(),
        password=section["password"].strip(),
        force_logout=False,
    )
    return bool(tgw.Login(cfg, mode))
