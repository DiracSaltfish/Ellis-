# BaseData 重建 —— 行为对齐 decompiled/query_api/base_data.py
# 核心: get_calendar 走三方资讯通道(function_id=A010061003)
MARKET_TO_EXCHANGE = {
    'SH': 'SSE', 'SZ': 'SZSE', 'BJ': 'NEEQ',
    'SHF': 'SHFE', 'CFE': 'CFFEX', 'DCE': 'DCE',
    'CZC': 'CZCE', 'INE': 'INE', 'SHN': 'SHHK', 'SZN': 'SZHK',
}


class BaseData:
    def __init__(self):
        self.calendar = []          # 升序 int8 交易日列表, 如 20260826
        self.stock_list = []
        self.code_list_hist = []
        self.block_trading = {}

    def get_calendar(self, data_type='str', market='SH', date=None):
        """交易日历。
        data_type: 'datetime'|'str'; market: SH/SZ/BJ; date: 截止日(int8)"""
        import tgw_macos.interface as tgw_i

        if date is None:
            import datetime
            date = int(datetime.date.today().strftime("%Y%m%d"))

        task_id = tgw_i.GetTaskID()
        tgw_i.SetThirdInfoParam(task_id, 'function_id', 'A010061003')
        tgw_i.SetThirdInfoParam(task_id, 'start_date', '19900101')
        tgw_i.SetThirdInfoParam(task_id, 'end_date', str(date))
        tgw_i.SetThirdInfoParam(task_id, 'market', MARKET_TO_EXCHANGE[market])

        rows = tgw_i._backend().query('third_info', task_id)
        days = sorted(int(r['TRADE_DAYS']) for r in rows) if rows else []
        if market == 'BJ':                     # 反编译确认: 北交所自 20211115
            days = [d for d in days if d >= 20211115]
        self.calendar = days
        return days
