# Source Generated with Decompyle++
# File: data_transfer.pyc (Python 3.12)

import time
import datetime

def millisecond_to_date(millisecond, format):
    return time.strftime(format, time.localtime(millisecond / 1000))


def date_to_millisecond(date, format = ('20100101', '%Y%m%d')):
    return int(time.mktime(time.strptime(date, format)) * 1000)


def date_str_to_int(date = ('2010-01-01',)):
    return int(date.replace('-', ''))


def datetime_to_millisecond(datetime_obj = (datetime.datetime.now(),)):
    return int(time.mktime(datetime_obj.timetuple()) * 1000 + datetime_obj.microsecond / 1000)


def millisecond_to_datetime(millisecond):
    return datetime.datetime.fromtimestamp(millisecond / 1000)


def date_to_datetime(date = ('20090101',)):
    return datetime.datetime.strptime(date, '%Y%m%d')


def date_minute_to_datetime(date = ('200901011212',)):
    return datetime.datetime.strptime(date, '%Y%m%d%H%M')


def datetime_to_int(date = (datetime.datetime.now(),)):
    return int(date.strftime('%Y%m%d'))


def is_time_interval(start_time, end_time = ('084500000000', '235959999999')):
    start_time = start_time
    end_time = end_time
    now = datetime.datetime.now()
    morning_time = now.replace(hour = int(start_time[:2]), minute = int(start_time[2:4]), second = int(start_time[4:6]), microsecond = int(start_time[-6:]))
    if  < morning_time, now or morning_time, now <= now.replace(hour = int(end_time[:2]), minute = int(end_time[2:4]), second = int(end_time[4:6]), microsecond = int(end_time[-6:])):
        return True
        return False
    return False


def date_millisecond_to_datetime(date = (0x475FD72CD4FDA0,)):
    return datetime.datetime.strptime(str(date), '%Y%m%d%H%M%S%f')


def datetime_to_int_millisecond(date = (datetime.datetime.now(),)):
    return int(int(date.strftime('%Y%m%d%H%M%S%f')) / 1000)


def date_split(start_date, end_date, split_num):
    date_format = '%Y%m%d'
    start_date = datetime.datetime.strptime(str(start_date), date_format)
    end_date = datetime.datetime.strptime(str(end_date), date_format)
    result = []
    temp_list = []
    current_date = start_date
    if current_date <= end_date:
        temp_list.append(current_date.strftime(date_format))
        if len(temp_list) == split_num:
            result.append([
                temp_list[0],
                temp_list[-1]])
            temp_list = []
        current_date += datetime.timedelta(days = 1)
        if current_date <= end_date:
            continue
    if temp_list:
        result.append([
            temp_list[0],
            temp_list[-1]])
    return result

if __name__ == '__main__':
    a = date_to_datetime(date = '20090101')
    print(str(a))
    return None
