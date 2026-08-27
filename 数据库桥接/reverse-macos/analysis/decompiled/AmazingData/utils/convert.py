# Source Generated with Decompyle++
# File: convert.pyc (Python 3.12)

from datetime import datetime
from typing import Any
import pandas as pd
import tgw
from AmazingData.utils.constant import Period, Snapshot, SnapshotL2, Order, Execution, OrderQueue, SnapshotIndex, Kline, SnapshotHKT, SnapshotFuture, SnapshotOption

def get_market(_type = None):
    market = None
    if _type == tgw.MarketType.kNEEQ:
        market = 'BJ'
        return market
    if None == tgw.MarketType.kSSE:
        market = 'SH'
        return market
    if None == tgw.MarketType.kSZSE:
        market = 'SZ'
        return market
    if None == tgw.MarketType.kSHFE:
        market = 'SHF'
        return market
    if None == tgw.MarketType.kCFFEX:
        market = 'CFE'
        return market
    if None == tgw.MarketType.kDCE:
        market = 'DCE'
        return market
    if None == tgw.MarketType.kCZCE:
        market = 'CZC'
        return market
    if None == tgw.MarketType.kINE:
        market = 'INE'
        return market
    if None == tgw.MarketType.kHKEx:
        market = 'HK'
    return market


def get_code(_type = None, _code = None):
    code = None
    if _type == tgw.MarketType.kNEEQ:
        code = f'''{_code}.BJ'''
        return code
    if None == tgw.MarketType.kSSE:
        code = f'''{_code}.SH'''
        return code
    if None == tgw.MarketType.kSZSE:
        code = f'''{_code}.SZ'''
        return code
    if None == tgw.MarketType.kSHFE:
        code = f'''{_code}.SHF'''
        return code
    if None == tgw.MarketType.kCFFEX:
        code = f'''{_code}.CFE'''
        return code
    if None == tgw.MarketType.kDCE:
        code = f'''{_code}.DCE'''
        return code
    if None == tgw.MarketType.kCZCE:
        code = f'''{_code}.CZC'''
        return code
    if None == tgw.MarketType.kINE:
        code = f'''{_code}.INE'''
        return code
    if None == tgw.MarketType.kHKEx:
        code = f'''{_code}.HK'''
    return code


def get_tgw_type_code(_code = None):
    (_code, _ext) = _code.split('.', 1)
    _ext = _ext.upper()
    if _ext == 'BJ':
        return (tgw.MarketType.kNEEQ, _code)
    if None == 'SH':
        return (tgw.MarketType.kSSE, _code)
    if None == 'SZ':
        return (tgw.MarketType.kSZSE, _code)
    if None == 'SHF':
        return (tgw.MarketType.kSHFE, _code)
    if None == 'CFE':
        return (tgw.MarketType.kCFFEX, _code)
    if None == 'DCE':
        return (tgw.MarketType.kDCE, _code)
    if None == 'CZC':
        return (tgw.MarketType.kCZCE, _code)
    if None == 'INE':
        return (tgw.MarketType.kINE, _code)
    raise None(f'''未知类型--{_ext}''')


def convert_realtime_snapshotfuture(tgw_data = None):
    extra = { }
    for i in range(1, 6):
        extra[f'''ask_price{i}'''] = tgw_data.offer_price[i - 1] / 1000000
        extra[f'''ask_volume{i}'''] = tgw_data.offer_volume[i - 1] / 100
        extra[f'''bid_price{i}'''] = tgw_data.bid_price[i - 1] / 1000000
        extra[f'''bid_volume{i}'''] = tgw_data.bid_volume[i - 1] / 100
# WARNING: Decompyle incomplete


def convert_realtime_snapshotoption(tgw_data = None):
    extra = { }
    for i in range(1, 6):
        extra[f'''ask_price{i}'''] = tgw_data.offer_price[i - 1] / 1000000
        extra[f'''ask_volume{i}'''] = tgw_data.offer_volume[i - 1] / 100
        extra[f'''bid_price{i}'''] = tgw_data.bid_price[i - 1] / 1000000
        extra[f'''bid_volume{i}'''] = tgw_data.bid_volume[i - 1] / 100
# WARNING: Decompyle incomplete


def convert_realtime_snapshotHKT(tgw_data = None):
    pass
# WARNING: Decompyle incomplete


def convert_realtime_snapshotL2(tgw_data = None):
    """
    tick_demo ={
    'market_type': 102,
    'security_code': '300750',
    'orig_time': 20250521145006000,
    'trading_phase_code': 'T0',
    'pre_close_price': 263000000,
    'open_price': 266990000,
    'high_price': 279990000,
    'low_price': 265800000,
    'last_price': 273970000,
    'close_price': 0,
    'bid_price1': 273960000,
    'bid_price2': 273950000,
    'bid_price3': 273940000,
    'bid_price4': 273930000,
    'bid_price5': 273920000,
    'bid_price6': 273910000,
    'bid_price7': 273900000,
    'bid_price8': 273890000,
    'bid_price9': 273880000,
    'bid_price10': 273870000,
    'bid_volume1': 300000,
    'bid_volume2': 130000,
    'bid_volume3': 100000,
    'bid_volume4': 480000,
    'bid_volume5': 120000,
    'bid_volume6': 190000,
    'bid_volume7': 280000,
    'bid_volume8': 80000,
    'bid_volume9': 120000,
    'bid_volume10': 20000,
    'offer_price1': 274000000,
    'offer_price2': 274010000,
    'offer_price3': 274020000,
    'offer_price4': 274030000,
    'offer_price5': 274040000,
    'offer_price6': 274050000,
    'offer_price7': 274060000,
    'offer_price8': 274070000,
    'offer_price9': 274080000,
    'offer_price10': 274100000,
    'offer_volume1': 1105600,
    'offer_volume2': 380000,
    'offer_volume3': 254000,
    'offer_volume4': 260000,
    'offer_volume5': 130000,
    'offer_volume6': 180000,
    'offer_volume7': 110000,
    'offer_volume8': 10000,
    'offer_volume9': 10000,
    'offer_volume10': 560000,
    'num_trades': 211237,
    'total_volume_trade': 5436134400,
    'total_value_trade': 1487768933055000,
    'total_bid_volume': 127656200,
    'total_offer_volume': 428383200,
    'weighted_avg_bid_price': 265920000,
    'weighted_avg_offer_price': 286640000,
    'IOPV': 0,
    'high_limited': 315600000,
    'low_limited': 210400000,
    'change1': 10970000,升跌1（对比昨收价）
    'change2': 0,升跌2（对比上一笔）
    }
    """
    extra = { }
    for i in range(1, 11):
        extra[f'''ask_price{i}'''] = tgw_data.offer_price[i - 1] / 1000000
        extra[f'''ask_volume{i}'''] = tgw_data.offer_volume[i - 1] / 100
        extra[f'''bid_price{i}'''] = tgw_data.bid_price[i - 1] / 1000000
        extra[f'''bid_volume{i}'''] = tgw_data.bid_volume[i - 1] / 100
# WARNING: Decompyle incomplete


def convert_realtime_order(tgw_data = None):
    item = Order(code = get_code(tgw_data.market_type, tgw_data.security_code), order_time = datetime.strptime(str(tgw_data.order_time), '%Y%m%d%H%M%S%f'), order_price = tgw_data.order_price / 1000000, order_volume = tgw_data.order_volume / 100, appl_seq_num = tgw_data.appl_seq_num, channel_no = tgw_data.channel_no, side = tgw_data.side, order_type = tgw_data.order_type, md_stream_id = tgw_data.md_stream_id, product_status = tgw_data.product_status, orig_order_no = tgw_data.orig_order_no, biz_index = tgw_data.biz_index)
    return item


def convert_realtime_execution(tgw_data = None):
    item = Execution(code = get_code(tgw_data.market_type, tgw_data.security_code), exec_time = datetime.strptime(str(tgw_data.exec_time), '%Y%m%d%H%M%S%f'), channel_no = tgw_data.channel_no, appl_seq_num = tgw_data.appl_seq_num, exec_price = tgw_data.exec_price / 1000000, exec_volume = tgw_data.exec_volume / 100, value_trade = tgw_data.value_trade / 100000, bid_appl_seq_num = tgw_data.bid_appl_seq_num, offer_appl_seq_num = tgw_data.offer_appl_seq_num, side = tgw_data.side, exec_type = tgw_data.exec_type, md_stream_id = tgw_data.md_stream_id, biz_index = tgw_data.biz_index)
    return item


def convert_realtime_order_queue(tgw_data = None):
    extra = { }
    for i in range(1, 51):
        extra[f'''volume{i}'''] = tgw_data.volume[i - 1] / 100
# WARNING: Decompyle incomplete


def convert_realtime_tick_index(tgw_data = None):
    """
    index_demo = {
        'market_type': 101,
        'security_code': '000300',
        'orig_time': 20240227133145220, # 交易所行情数据时间
        'trading_phase_code': '',
        'pre_close_index': 3453358500,
        'open_index': 3440051600,
        'high_index': 3483385500,
        'low_index': 3437442200,
        'last_index': 3474362400,
        'close_index': 0,
        'total_volume_trade': 8934304600,
        'total_value_trade': 15930131603140000,
        'variety_category': 5
    }
    """
    item = SnapshotIndex(code = get_code(tgw_data.market_type, tgw_data.security_code), trade_time = datetime.strptime(str(tgw_data.orig_time)[:-3], '%Y%m%d%H%M%S'), last = tgw_data.last_index / 1000000, pre_close = tgw_data.pre_close_index / 1000000, open = tgw_data.open_index / 1000000, high = tgw_data.high_index / 1000000, low = tgw_data.low_index / 1000000, close = tgw_data.close_index / 1000000, volume = tgw_data.total_volume_trade / 100, amount = tgw_data.total_value_trade / 100000, trading_phase_code = tgw_data.trading_phase_code)
    return item


def convert_realtime_tick_stock(tgw_data = None):
    """
    tick_demo = {
        'market_type': 101,
        'security_code': '600000',
        'variety_category': 1,
        'orig_time': 20240227133145832,             # 交易所行情数据时间
        'trading_phase_code': 'T111',
        'pre_close_price': 7100000,
        'open_price': 7070000,
        'high_price': 7150000,
        'low_price': 7060000,
        'last_price': 7110000,
        'close_price': 0,
        'total_volume_trade': 1621371900,
        'total_value_trade': 11528767100000,
        'bid_price1': 7110000,
        'bid_price2': 7100000,
        'bid_price3': 7090000,
        'bid_price4': 7080000,
        'bid_price5': 7070000,

        'bid_volume1': 20000,
        'bid_volume2': 26810000,
        'bid_volume3': 38450000,
        'bid_volume4': 33100000,
        'bid_volume5': 35720000,

        'offer_price1': 7120000,
        'offer_price2': 7130000,
        'offer_price3': 7140000,
        'offer_price4': 7150000,
        'offer_price5': 7160000,

        'offer_volume1': 25400000,
        'offer_volume2': 28180000,
        'offer_volume3': 24750000,
        'offer_volume4': 63481800,
        'offer_volume5': 15723900,

        'num_trades': 12339,
        'IOPV': 0,
        'high_limited': 7810000,
        'low_limited': 6390000
    }
    """
    extra = { }
    for i in range(1, 6):
        extra[f'''ask_price{i}'''] = tgw_data.offer_price[i - 1] / 1000000
        extra[f'''ask_volume{i}'''] = tgw_data.offer_volume[i - 1] / 100
        extra[f'''bid_price{i}'''] = tgw_data.bid_price[i - 1] / 1000000
        extra[f'''bid_volume{i}'''] = tgw_data.bid_volume[i - 1] / 100
# WARNING: Decompyle incomplete


def convert_realtime_kline(tgw_data = None, period = None):
    kline_time_format = '%Y%m%d%H%M'
    if period in (Period.day.value, Period.week.value, Period.season.value, Period.year.value):
        kline_time_format = '%Y%m%d'
    item = Kline(code = get_code(tgw_data.market_type, tgw_data.security_code), kline_time = datetime.strptime(str(tgw_data.kline_time), kline_time_format), open = tgw_data.open_price / 1000000, high = tgw_data.high_price / 1000000, low = tgw_data.low_price / 1000000, close = tgw_data.close_price / 1000000, volume = int(tgw_data.volume_trade / 100), amount = tgw_data.value_trade / 100000)
    return item


def convert_history_snapshotl2(code, snapshotL2_df):
    snapshotL2_convert_df = pd.DataFrame(columns = SnapshotL2.model_fields.keys())
    snapshotL2_convert_df['code'] = [
        code] * snapshotL2_df.shape[0]
    snapshotL2_convert_df['trade_time'] = pd.to_datetime(snapshotL2_df['orig_time'].astype(str), format = '%Y%m%d%H%M%S%f').dt.floor(freq = 's')
    for i in range(1, 11):
        snapshotL2_convert_df[f'''ask_price{i}'''] = snapshotL2_df[f'''offer_price{i}'''].div(1000000)
        snapshotL2_convert_df[f'''ask_volume{i}'''] = snapshotL2_df[f'''offer_volume{i}'''].div(100)
        snapshotL2_convert_df[f'''bid_price{i}'''] = snapshotL2_df[f'''bid_price{i}'''].div(1000000)
        snapshotL2_convert_df[f'''bid_volume{i}'''] = snapshotL2_df[f'''bid_volume{i}'''].div(100)
    snapshotL2_convert_df['pre_close'] = snapshotL2_df['pre_close_price'].div(1000000)
    snapshotL2_convert_df['last'] = snapshotL2_df['last_price'].div(1000000)
    snapshotL2_convert_df['open'] = snapshotL2_df['open_price'].div(1000000)
    snapshotL2_convert_df['high'] = snapshotL2_df['high_price'].div(1000000)
    snapshotL2_convert_df['low'] = snapshotL2_df['low_price'].div(1000000)
    snapshotL2_convert_df['close'] = snapshotL2_df['close_price'].div(1000000)
    snapshotL2_convert_df['volume'] = snapshotL2_df['total_volume_trade'].div(100)
    snapshotL2_convert_df['amount'] = snapshotL2_df['total_value_trade'].div(100000)
    snapshotL2_convert_df['num_trades'] = snapshotL2_df['num_trades']
    snapshotL2_convert_df['high_limited'] = snapshotL2_df['high_limited'].div(1000000)
    snapshotL2_convert_df['low_limited'] = snapshotL2_df['low_limited'].div(1000000)
    snapshotL2_convert_df['weighted_avg_bid_price'] = snapshotL2_df['weighted_avg_bid_price'].div(1000000)
    snapshotL2_convert_df['weighted_avg_offer_price'] = snapshotL2_df['weighted_avg_offer_price'].div(1000000)
    snapshotL2_convert_df['change1'] = snapshotL2_df['change1'].div(1000000)
    snapshotL2_convert_df['change2'] = snapshotL2_df['change2'].div(1000000)
    snapshotL2_convert_df['iopv'] = snapshotL2_df['IOPV'].div(1000000)
    snapshotL2_convert_df['trading_phase_code'] = snapshotL2_df['trading_phase_code']
    return snapshotL2_convert_df


def convert_history_order(code, order_df):
    order_convert_df = pd.DataFrame(columns = Order.model_fields.keys())
    order_convert_df['code'] = [
        code] * order_df.shape[0]
    order_convert_df['order_time'] = pd.to_datetime(order_df['order_time'].astype(str), format = '%Y%m%d%H%M%S%f').dt.floor(freq = 's')
    order_convert_df['order_price'] = order_df['order_price'].div(1000000)
    order_convert_df['order_volume'] = order_df['order_volume'].div(100)
    order_convert_df['appl_seq_num'] = order_df['appl_seq_num']
    order_convert_df['channel_no'] = order_df['channel_no']
    order_convert_df['side'] = order_df['side']
    order_convert_df['md_stream_id'] = order_df['md_stream_id']
    order_convert_df['orig_order_no'] = order_df['orig_order_no']
    order_convert_df['biz_index'] = order_df['biz_index']
    return order_convert_df


def convert_history_execution(code, execution_df):
    execution_convert_df = pd.DataFrame(columns = Execution.model_fields.keys())
    execution_convert_df['code'] = [
        code] * execution_df.shape[0]
    execution_convert_df['exec_time'] = pd.to_datetime(execution_df['exec_time'].astype(str), format = '%Y%m%d%H%M%S%f').dt.floor(freq = 's')
    execution_convert_df['channel_no'] = execution_df['channel_no']
    execution_convert_df['appl_seq_num'] = execution_df['appl_seq_num']
    execution_convert_df['exec_price'] = execution_df['exec_price'].div(1000000)
    execution_convert_df['exec_volume'] = execution_df['exec_volume'].div(100)
    execution_convert_df['value_trade'] = execution_df['value_trade'].div(100000)
    execution_convert_df['bid_appl_seq_num'] = execution_df['bid_appl_seq_num']
    execution_convert_df['offer_appl_seq_num'] = execution_df['offer_appl_seq_num']
    execution_convert_df['side'] = execution_df['side']
    execution_convert_df['exec_type'] = execution_df['exec_type']
    execution_convert_df['md_stream_id'] = execution_df['md_stream_id']
    execution_convert_df['biz_index'] = execution_df['biz_index']
    execution_convert_df['variety_category'] = execution_df['variety_category']
    return execution_convert_df


def convert_history_order_queue(code, snapshot_df):
    snapshot_convert_df = pd.DataFrame(columns = Snapshot.model_fields.keys())
    snapshot_convert_df['code'] = [
        code] * snapshot_df.shape[0]
    snapshot_convert_df['trade_time'] = pd.to_datetime(snapshot_df['orig_time'].astype(str), format = '%Y%m%d%H%M%S%f').dt.floor(freq = 's')
    for i in range(1, 6):
        snapshot_convert_df[f'''ask_price{i}'''] = snapshot_df[f'''offer_price{i}'''].div(1000000)
        snapshot_convert_df[f'''ask_volume{i}'''] = snapshot_df[f'''offer_volume{i}'''].div(100)
        snapshot_convert_df[f'''bid_price{i}'''] = snapshot_df[f'''bid_price{i}'''].div(1000000)
        snapshot_convert_df[f'''bid_volume{i}'''] = snapshot_df[f'''bid_volume{i}'''].div(100)
    snapshot_convert_df['pre_close'] = snapshot_df['pre_close_price'].div(1000000)
    snapshot_convert_df['last'] = snapshot_df['last_price'].div(1000000)
    snapshot_convert_df['open'] = snapshot_df['open_price'].div(1000000)
    snapshot_convert_df['high'] = snapshot_df['high_price'].div(1000000)
    snapshot_convert_df['low'] = snapshot_df['low_price'].div(1000000)
    snapshot_convert_df['close'] = snapshot_df['close_price'].div(1000000)
    snapshot_convert_df['volume'] = snapshot_df['total_volume_trade'].div(100)
    snapshot_convert_df['amount'] = snapshot_df['total_value_trade'].div(100000)
    snapshot_convert_df['num_trades'] = snapshot_df['num_trades']
    snapshot_convert_df['high_limited'] = snapshot_df['high_limited'].div(1000000)
    snapshot_convert_df['low_limited'] = snapshot_df['low_limited'].div(1000000)
    snapshot_convert_df['iopv'] = snapshot_df['IOPV'].div(1000000)
    snapshot_convert_df['trading_phase_code'] = snapshot_df['trading_phase_code']
    return snapshot_convert_df


def convert_history_tick_index(code, snapshot_df):
    snapshot_convert_df = pd.DataFrame(columns = SnapshotIndex.model_fields.keys())
    snapshot_convert_df['code'] = [
        code] * snapshot_df.shape[0]
    snapshot_convert_df['trade_time'] = pd.to_datetime(snapshot_df['orig_time'].astype(str), format = '%Y%m%d%H%M%S%f').dt.floor(freq = 's')
    snapshot_convert_df['last'] = snapshot_df['last_index'].div(1000000)
    snapshot_convert_df['pre_close'] = snapshot_df['pre_close_index'].div(1000000)
    snapshot_convert_df['open'] = snapshot_df['open_index'].div(1000000)
    snapshot_convert_df['high'] = snapshot_df['high_index'].div(1000000)
    snapshot_convert_df['low'] = snapshot_df['low_index'].div(1000000)
    snapshot_convert_df['close'] = snapshot_df['close_index'].div(1000000)
    snapshot_convert_df['volume'] = snapshot_df['total_volume_trade'].div(100)
    snapshot_convert_df['amount'] = snapshot_df['total_value_trade'].div(100000)
    snapshot_convert_df['trading_phase_code'] = snapshot_df['trading_phase_code']
    return snapshot_convert_df


def convert_history_tick_stock(code, snapshot_df):
    snapshot_convert_df = pd.DataFrame(columns = Snapshot.model_fields.keys())
    snapshot_convert_df['code'] = [
        code] * snapshot_df.shape[0]
    snapshot_convert_df['trade_time'] = pd.to_datetime(snapshot_df['orig_time'].astype(str), format = '%Y%m%d%H%M%S%f').dt.floor(freq = 's')
    for i in range(1, 6):
        snapshot_convert_df[f'''ask_price{i}'''] = snapshot_df[f'''offer_price{i}'''].div(1000000)
        snapshot_convert_df[f'''ask_volume{i}'''] = snapshot_df[f'''offer_volume{i}'''].div(100)
        snapshot_convert_df[f'''bid_price{i}'''] = snapshot_df[f'''bid_price{i}'''].div(1000000)
        snapshot_convert_df[f'''bid_volume{i}'''] = snapshot_df[f'''bid_volume{i}'''].div(100)
    snapshot_convert_df['pre_close'] = snapshot_df['pre_close_price'].div(1000000)
    snapshot_convert_df['last'] = snapshot_df['last_price'].div(1000000)
    snapshot_convert_df['open'] = snapshot_df['open_price'].div(1000000)
    snapshot_convert_df['high'] = snapshot_df['high_price'].div(1000000)
    snapshot_convert_df['low'] = snapshot_df['low_price'].div(1000000)
    snapshot_convert_df['close'] = snapshot_df['close_price'].div(1000000)
    snapshot_convert_df['volume'] = snapshot_df['total_volume_trade'].div(100)
    snapshot_convert_df['amount'] = snapshot_df['total_value_trade'].div(100000)
    snapshot_convert_df['num_trades'] = snapshot_df['num_trades']
    snapshot_convert_df['high_limited'] = snapshot_df['high_limited'].div(1000000)
    snapshot_convert_df['low_limited'] = snapshot_df['low_limited'].div(1000000)
    snapshot_convert_df['iopv'] = snapshot_df['IOPV'].div(1000000)
    snapshot_convert_df['trading_phase_code'] = snapshot_df['trading_phase_code']
    return snapshot_convert_df


def convert_history_tick_option(code, snapshot_df):
    snapshot_convert_df = pd.DataFrame(columns = SnapshotOption.model_fields.keys())
    snapshot_convert_df['code'] = [
        code] * snapshot_df.shape[0]
    snapshot_convert_df['trade_time'] = pd.to_datetime(snapshot_df['orig_time'].astype(str), format = '%Y%m%d%H%M%S%f')
    snapshot_convert_df['total_long_position'] = snapshot_df['total_long_position'].div(100)
    for i in range(1, 6):
        snapshot_convert_df[f'''ask_price{i}'''] = snapshot_df[f'''offer_price{i}'''].div(1000000)
        snapshot_convert_df[f'''ask_volume{i}'''] = snapshot_df[f'''offer_volume{i}'''].div(100)
        snapshot_convert_df[f'''bid_price{i}'''] = snapshot_df[f'''bid_price{i}'''].div(1000000)
        snapshot_convert_df[f'''bid_volume{i}'''] = snapshot_df[f'''bid_volume{i}'''].div(100)
    snapshot_convert_df['auction_price'] = snapshot_df['auction_price'].div(1000000)
    snapshot_convert_df['auction_volume'] = snapshot_df['auction_volume'].div(100)
    snapshot_convert_df['pre_close'] = snapshot_df['pre_close_price'].div(1000000)
    snapshot_convert_df['last'] = snapshot_df['last_price'].div(1000000)
    snapshot_convert_df['open'] = snapshot_df['open_price'].div(1000000)
    snapshot_convert_df['high'] = snapshot_df['high_price'].div(1000000)
    snapshot_convert_df['low'] = snapshot_df['low_price'].div(1000000)
    snapshot_convert_df['close'] = snapshot_df['close_price'].div(1000000)
    snapshot_convert_df['volume'] = snapshot_df['total_volume_trade'].div(100)
    snapshot_convert_df['amount'] = snapshot_df['total_value_trade'].div(100000)
    snapshot_convert_df['high_limited'] = snapshot_df['high_limited'].div(1000000)
    snapshot_convert_df['low_limited'] = snapshot_df['low_limited'].div(1000000)
    snapshot_convert_df['pre_settle'] = snapshot_df['pre_settle_price'].div(1000000)
    snapshot_convert_df['settle'] = snapshot_df['settle_price'].div(1000000)
    snapshot_convert_df['ref_price'] = snapshot_df['ref_price'].div(1000000)
    snapshot_convert_df['contract_type'] = snapshot_df['contract_type']
    snapshot_convert_df['expire_date'] = snapshot_df['expire_date']
    snapshot_convert_df['underlying_security_code'] = snapshot_df['underlying_security_code']
    snapshot_convert_df['exercise_price'] = snapshot_df['exercise_price'].div(1000000)
    snapshot_convert_df['trading_phase_code'] = snapshot_df['trading_phase_code']
    return snapshot_convert_df


def convert_history_tick_future(code, snapshot_df):
    snapshot_convert_df = pd.DataFrame(columns = SnapshotFuture.model_fields.keys())
    snapshot_convert_df['code'] = [
        code] * snapshot_df.shape[0]
    snapshot_convert_df['trade_time'] = pd.to_datetime(snapshot_df['orig_time'].astype(str), format = '%Y%m%d%H%M%S%f')
    snapshot_convert_df['action_day'] = snapshot_df['action_day']
    for i in range(1, 6):
        snapshot_convert_df[f'''ask_price{i}'''] = snapshot_df[f'''offer_price{i}'''].div(1000000)
        snapshot_convert_df[f'''ask_volume{i}'''] = snapshot_df[f'''offer_volume{i}'''].div(100)
        snapshot_convert_df[f'''bid_price{i}'''] = snapshot_df[f'''bid_price{i}'''].div(1000000)
        snapshot_convert_df[f'''bid_volume{i}'''] = snapshot_df[f'''bid_volume{i}'''].div(100)
    snapshot_convert_df['pre_close'] = snapshot_df['pre_close_price'].div(1000000)
    snapshot_convert_df['last'] = snapshot_df['last_price'].div(1000000)
    snapshot_convert_df['open'] = snapshot_df['open_price'].div(1000000)
    snapshot_convert_df['high'] = snapshot_df['high_price'].div(1000000)
    snapshot_convert_df['low'] = snapshot_df['low_price'].div(1000000)
    snapshot_convert_df['close'] = snapshot_df['close_price'].div(1000000)
    snapshot_convert_df['volume'] = snapshot_df['total_volume_trade'].div(100)
    snapshot_convert_df['amount'] = snapshot_df['total_value_trade'].div(100000)
    snapshot_convert_df['high_limited'] = snapshot_df['high_limited'].div(1000000)
    snapshot_convert_df['low_limited'] = snapshot_df['low_limited'].div(1000000)
    snapshot_convert_df['pre_settle'] = snapshot_df['pre_settle_price'].div(1000000)
    snapshot_convert_df['pre_open_interest'] = snapshot_df['pre_open_interest'].div(100)
    snapshot_convert_df['trading_day'] = snapshot_df['trading_day']
    snapshot_convert_df['open_interest'] = snapshot_df['open_interest'].div(100)
    snapshot_convert_df['settle'] = snapshot_df['settle_price'].div(1000000)
    snapshot_convert_df['average_price'] = snapshot_df['average_price'].div(1000000)
    return snapshot_convert_df


def convert_history_tick_stock_HKT(code, snapshot_df):
    snapshot_convert_df = pd.DataFrame(columns = SnapshotHKT.model_fields.keys())
    snapshot_convert_df['code'] = [
        code] * snapshot_df.shape[0]
    snapshot_convert_df['trade_time'] = pd.to_datetime(snapshot_df['orig_time'].astype(str), format = '%Y%m%d%H%M%S%f').dt.floor(freq = 's')
    for i in range(1, 6):
        snapshot_convert_df[f'''ask_price{i}'''] = snapshot_df[f'''offer_price{i}'''].div(1000000)
        snapshot_convert_df[f'''ask_volume{i}'''] = snapshot_df[f'''offer_volume{i}'''].div(100)
        snapshot_convert_df[f'''bid_price{i}'''] = snapshot_df[f'''bid_price{i}'''].div(1000000)
        snapshot_convert_df[f'''bid_volume{i}'''] = snapshot_df[f'''bid_volume{i}'''].div(100)
    snapshot_convert_df['nominal_price'] = snapshot_df['nominal_price'].div(1000000)
    snapshot_convert_df['ref_price'] = snapshot_df['ref_price'].div(1000000)
    snapshot_convert_df['bid_price_limit_up'] = snapshot_df['bid_price_limit_up'].div(1000000)
    snapshot_convert_df['bid_price_limit_down'] = snapshot_df['bid_price_limit_down'].div(1000000)
    snapshot_convert_df['offer_price_limit_up'] = snapshot_df['offer_price_limit_up'].div(1000000)
    snapshot_convert_df['offer_price_limit_down'] = snapshot_df['offer_price_limit_down'].div(1000000)
    snapshot_convert_df['pre_close'] = snapshot_df['pre_close_price'].div(1000000)
    snapshot_convert_df['last'] = snapshot_df['last_price'].div(1000000)
    snapshot_convert_df['high'] = snapshot_df['high_price'].div(1000000)
    snapshot_convert_df['low'] = snapshot_df['low_price'].div(1000000)
    snapshot_convert_df['volume'] = snapshot_df['total_volume_trade'].div(100)
    snapshot_convert_df['amount'] = snapshot_df['total_value_trade'].div(100000)
    snapshot_convert_df['high_limited'] = snapshot_df['high_limited'].div(1000000)
    snapshot_convert_df['low_limited'] = snapshot_df['low_limited'].div(1000000)
    snapshot_convert_df['trading_phase_code'] = snapshot_df['trading_phase_code']
    return snapshot_convert_df


def convert_history_kline(code, period, kline_df):
    kline_time_format = '%Y%m%d%H%M'
    if period in (Period.day.value, Period.week.value, Period.month.value, Period.season.value, Period.year.value):
        kline_time_format = '%Y%m%d'
    kline_convert_df = pd.DataFrame(columns = Kline.model_fields.keys())
    kline_convert_df['code'] = [
        code] * kline_df.shape[0]
    kline_convert_df['kline_time'] = pd.to_datetime(kline_df['kline_time'].astype(str), format = kline_time_format)
    kline_convert_df['open'] = kline_df['open_price'].div(1000000)
    kline_convert_df['high'] = kline_df['high_price'].div(1000000)
    kline_convert_df['low'] = kline_df['low_price'].div(1000000)
    kline_convert_df['close'] = kline_df['close_price'].div(1000000)
    kline_convert_df['volume'] = kline_df['volume_trade'].div(100).astype(int)
    kline_convert_df['amount'] = kline_df['value_trade'].div(100000)
    return kline_convert_df

if __name__ == '__main__':
    tgw_data = {
        'market_type': 101,
        'security_code': '600007',
        'orig_time': 0x47E8A7E42AEF10,
        'kline_time': 0x2F204BF354,
        'open_price': 24300000,
        'high_price': 24300000,
        'low_price': 24280000,
        'close_price': 24290000,
        'volume_trade': 3500,
        'value_trade': 85015,
        'variety_category': 1 }
    a = convert_realtime_kline(tgw_data, Period.min1.value)
    return None
