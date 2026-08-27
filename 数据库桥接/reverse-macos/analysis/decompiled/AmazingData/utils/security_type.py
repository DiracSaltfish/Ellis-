# Source Generated with Decompyle++
# File: security_type.pyc (Python 3.12)

import re
from AmazingData.config.security_type_config import security_type_info

def is_security_type(stock_code, security_type):
    type_list = []
    
    try:
        type_list = security_type_info['base_type'][security_type]
        for i in type_list:
            if not re.match(i, stock_code):
                continue
            type_list
            return True
        return False
    except KeyError:
        extra_type_list = security_type_info['extra_type'][security_type]
        for i in extra_type_list:
            type_list += security_type_info['base_type'][i]
    except KeyError:
        return False

    continue

if __name__ == '__main__':
    print(is_security_type('90006000.SZ', 'SZ_OPTION'))
    print(is_security_type('jd2601_k_4000.DCE', 'DS_OPTION'))
    return None
