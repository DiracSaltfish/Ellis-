"""Synthetic PCF assessment-tool tests: no credentials or business samples."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

path=Path(__file__).resolve().parents[1]/'tools/oracle/pcf_api_eval.py'
spec=importlib.util.spec_from_file_location('pcf_eval',path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class PcfEvaluationTests(unittest.TestCase):
    def test_nested_component_list_is_not_silently_dropped(self):
        root,top,rows=m.xml_parts(b'<PCFFile><TradingDay>20260102</TradingDay><ComponentList><Component><UnderlyingSecurityID>TEST</UnderlyingSecurityID><ComponentShare>2</ComponentShare></Component></ComponentList></PCFFile>')
        self.assertEqual(root,'PCFFile');self.assertEqual(len(rows),1)
        self.assertEqual(top,{'TradingDay':'20260102'});self.assertEqual(rows[0]['ComponentShare'],'2')

    def test_scaling_uses_decimal_and_does_not_coerce_missing_to_zero(self):
        self.assertTrue(m.scalar_equal(1234567,'12.34567',100000))
        self.assertTrue(m.scalar_equal(200,'2',100))
        self.assertFalse(m.scalar_equal(200,'200',100))
        self.assertIsNone(m.scalar_equal(None,'0',100))

    def test_sse_limits_are_identified_as_unavailable(self):
        body=b'<SSEPortfolioCompositionFile><TradingDay>20260102</TradingDay><CreationLimit>10</CreationLimit><RedemptionLimit>20</RedemptionLimit><EstimatedCashComponent>1.5</EstimatedCashComponent><RecordNumber>1</RecordNumber><ComponentList><Component><InstrumentID>TEST</InstrumentID><Quantity>2</Quantity></Component></ComponentList></SSEPortfolioCompositionFile>'
        basic={'trading_day':20260102,'creation_limit':0,'redemption_limit':0,'estimate_cash_component':150000,'total_record_num':100}
        with patch.object(m,'fetch',return_value=(body,{'http_status':200})):
            out=m.exchange_compare(basic,[{'security_code':'TEST','component_share':200}],101,'510000')
        self.assertEqual(out['api_unavailable_limit_fields'],['creation_limit','redemption_limit'])
        self.assertTrue(out['numeric_matches']['estimate_cash_component'])
        self.assertTrue(out['component_identity_sets_equal'])
        self.assertEqual(out['component_matches']['component_share'],{'compared':1,'equal':1})

    def test_pair_requires_complete_identity_set_not_count_alone(self):
        basic={'security_code':'TEST','market_type':101,'trading_day':20260102,'total_record_num':0}
        self.assertFalse(m.summarize([(basic,[]),(basic,[])],[(101,'TEST'),(102,'OTHER')])['returned_exact_requested_set'])

    def test_different_trading_days_are_not_compared_as_field_mismatches(self):
        body=b'<SSEPortfolioCompositionFile><TradingDay>20260103</TradingDay><CreationLimit>10</CreationLimit></SSEPortfolioCompositionFile>'
        with patch.object(m,'fetch',return_value=(body,{'http_status':200})):
            out=m.exchange_compare({'trading_day':20260102,'creation_limit':0},[],101,'510000')
        self.assertFalse(out['same_trading_day'])
        self.assertEqual(out['comparisons_skipped'],'trading_day_mismatch')
        self.assertNotIn('numeric_matches',out)
        self.assertNotIn('api_unavailable_limit_fields',out)

if __name__=='__main__':unittest.main()
