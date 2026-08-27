# amazingdata_re —— AmazingData 核心行为级伪源码重建
# 依据: pycdc 反编译输出(analysis/decompiled/) + 开发手册行为描述
from . import tgw_login
from .base_data import BaseData

__all__ = ["tgw_login", "BaseData", "login", "logout"]

login = tgw_login.login
logout = tgw_login.logout
