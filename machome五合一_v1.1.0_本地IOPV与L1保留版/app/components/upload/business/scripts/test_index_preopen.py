"""Offline regression for the complete index uploader morning state machine."""
import io
import json
import tempfile
import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import private_valuation_uploader as common
import private_nasdaq_valuation_uploader as index

NOW = datetime(2026,9,7,9,25,tzinfo=common.SHANGHAI)
PRIOR = datetime(2026,9,4,14,58,tzinfo=common.SHANGHAI)

def fx(source=common.CFETS_PREOPEN_FALLBACK_SOURCE):
    return common.CFETSSpotQuote('JPY/CNY',.042,PRIOR.date(),'14:58',PRIOR,source,PRIOR,'CURRENT_DAY_CFETS_UNAVAILABLE')

def pcf(fund=None):
    return index.PCF(fund or index.NIKKEI225_FUNDS[0],NOW.date(),PRIOR.date(),'Y','Y',100,100,1000000,
                     (index.Component('1321','Nikkei',100,'JP','JPY'),),'https://example.test/pcf','a'*64)

class FrozenClock(datetime):
    current=NOW
    @classmethod
    def now(cls,tz=None): return cls.current.astimezone(tz) if tz else cls.current

class PreopenTests(unittest.TestCase):
    def test_gate_all_22_symbols_and_bad_market_data(self):
        count=0
        q=index.NQQuote('N225M',40000,40005,None,NOW,'Live',NOW)
        for family in index.FAMILIES.values():
            for fund in family.funds:
                count+=1
                self.assertTrue(index.can_publish_index_input(pcf(fund),fx(),q,NOW))
                for bad in (replace(q,market_data_type='Delayed'),replace(q,observed_at=NOW-timedelta(seconds=16)),replace(q,observed_at=PRIOR),replace(q,observed_at=NOW+timedelta(seconds=1))):
                    self.assertFalse(index.can_publish_index_input(pcf(fund),fx(),bad,NOW))
                self.assertFalse(index.can_publish_index_input(replace(pcf(fund),trading_day=PRIOR.date()),fx(),q,NOW))
                self.assertFalse(index.can_publish_index_input(replace(pcf(fund),pre_trading_day=date(2026,9,5)),fx(),q,NOW))
                self.assertTrue(index.can_publish_index_input(replace(pcf(fund),pre_trading_day=date(2026,9,3)),fx(),q,NOW))
        self.assertEqual(count,22)

    def test_cache_expires_at_boundary_and_realtime_recovers(self):
        for minute,expected in ((554,False),(555,True),(574,True),(575,False),(600,False)):
            at=NOW.replace(hour=minute//60,minute=minute%60)
            q=index.NQQuote('N225M',40000,40005,None,at,'Live',at)
            self.assertEqual(index.can_publish_index_input(pcf(),fx(),q,at),expected)
        real=replace(fx(),source=common.CFETS_SPOT_SOURCE,trading_day=NOW.date(),source_observed_at=NOW,fetched_at=NOW)
        q=index.NQQuote('N225M',40000,40005,None,NOW,'Live',NOW)
        self.assertTrue(index.can_publish_index_input(pcf(),real,q,NOW))
        self.assertFalse(index.can_publish_index_input(pcf(),replace(real,source_observed_at=NOW-timedelta(seconds=181)),q,NOW))
        # Long holidays are checked against the actual PCF prior date, not a 7-day guess.
        old=PRIOR-timedelta(days=10)
        self.assertTrue(index.can_publish_index_input(replace(pcf(),pre_trading_day=old.date()),replace(fx(),trading_day=old.date(),source_observed_at=old),q,NOW))

    def test_fallback_preserves_source_time_and_jpy_units(self):
        row={'pair':'100JPY/CNY','bid':4.2,'ask':4.22,'healthy':True,'source':'CFETS_CHINAMONEY',
             'observed_at':PRIOR.isoformat(),'received_at':PRIOR.isoformat()}
        response={'last_healthy_fx_quotes':{'100JPY/CNY':row},'fx_quotes':{'USD/CNY':{'healthy':False}}}
        client=common.PrivateCFETSSpotClient('https://example.test','fixture')
        with patch.object(common,'open_server_request',return_value=io.BytesIO(json.dumps(response).encode())):
            got=client.fetch_preopen_fallback('JPY/CNY',NOW.date(),NOW)
        self.assertAlmostEqual(got.rate,.0421)
        self.assertEqual(got.source_observed_at,PRIOR)
        self.assertEqual(got.fetched_at,PRIOR)
        self.assertEqual(got.to_payload()['source'],'CFETS_PREOPEN_FALLBACK')
        self.assertIn('source_observed_at',got.to_payload())
        with self.assertRaises(common.SourceUnavailableError):client.fetch_preopen_fallback('JPY/CNY',NOW.date(),NOW.replace(minute=35))

    def test_stream_warms_when_all_pcfs_fail_without_upload(self):
        stop=SimpleNamespace(is_set=lambda:False,wait=lambda seconds:(_ for _ in ()).throw(InterruptedError('end fixture')))
        with tempfile.TemporaryDirectory() as tmp, patch.object(index,'datetime',FrozenClock), patch.object(common,'STOP_EVENT',stop), \
             patch.object(index,'NQMarket') as market, patch.object(index.PCFStore,'fetch',side_effect=RuntimeError('PCF pending')), \
             patch.object(index,'post_batch') as post:
            args=index.parser().parse_args(['--family','nikkei225','--token','fixture','--runtime-dir',tmp])
            with self.assertRaises(InterruptedError):index.run(args)
            self.assertGreater(market.return_value.quote.call_count,0)
            post.assert_not_called()

    def test_full_loop_publishes_fallback(self):
        class Stop:
            calls=0
            def is_set(self):
                self.calls+=1
                if self.calls>6:raise AssertionError('uploader did not publish')
                return False
            def wait(self,seconds):pass
        with tempfile.TemporaryDirectory() as tmp, patch.object(index,'datetime',FrozenClock),patch.object(common,'STOP_EVENT',Stop()), \
             patch.object(index,'NQMarket') as market,patch.object(index,'PCFStore') as store, \
             patch.object(index.CoefficientStore,'load',return_value=2.0),patch.object(index,'post_batch') as post, \
             patch.object(common.PrivateCFETSSpotClient,'fetch',side_effect=common.SourceUnavailableError('not published')), \
             patch.object(common.PrivateCFETSSpotClient,'fetch_preopen_fallback',return_value=fx()):
            market.return_value.quote.return_value=index.NQQuote('N225M',40000,40005,None,NOW,'Live',NOW)
            store.return_value.fetch.side_effect=lambda fund,*args:pcf(fund)
            args=index.parser().parse_args(['--family','nikkei225','--token','fixture','--runtime-dir',tmp,'--once'])
            self.assertEqual(index.run(args),0)
            values=post.call_args.args[1]
            self.assertEqual(len(values),4)
            self.assertEqual(values[0]['fx']['source'],'CFETS_PREOPEN_FALLBACK')
            self.assertEqual(values[0]['ib']['observed_at'],common.iso_timestamp(NOW))

if __name__=='__main__':unittest.main()
