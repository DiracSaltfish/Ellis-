import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from sina_l1 import normalize_symbol, parse_payload  # noqa: E402


class SinaL1Test(unittest.TestCase):
    def test_symbols(self):
        self.assertEqual(normalize_symbol("159866.SZ"), ("159866.SZ", "sz159866"))
        self.assertEqual(normalize_symbol("SH520740"), ("520740.SH", "sh520740"))
        with self.assertRaises(ValueError):
            normalize_symbol("bad")

    def test_parse_five_levels_and_units(self):
        payload = (
            'var hq_str_sz159866="测试ETF,1.675,1.680,1.686,1.689,1.674,1.685,1.686,'
            '27107200,45587702.500,229500,1.685,613300,1.684,475400,1.683,950800,1.682,'
            '132500,1.681,460900,1.686,843600,1.687,176200,1.688,413300,1.689,1023100,'
            '1.690,2026-08-27,09:40:03,00";'
        ).encode("gb18030")
        books = parse_payload(payload, ["159866.SZ"], received_ms=1_777_777_777_777)
        self.assertEqual(len(books), 1)
        book = books[0]
        self.assertEqual(book["s"], "159866.SZ")
        self.assertEqual(book["lp"], 1.686)
        self.assertEqual(book["bp"], [1.685, 1.684, 1.683, 1.682, 1.681])
        self.assertEqual(book["ap"], [1.686, 1.687, 1.688, 1.689, 1.690])
        self.assertEqual(book["bv"][0], 229500)
        self.assertEqual(book["av"][0], 460900)
        self.assertEqual(book["vol"], 27107200)
        self.assertEqual(book["rt"], 1_777_777_777_777)
        self.assertEqual(book["qt"], 1_787_794_803_000)

    def test_ignores_unknown_and_short_statements(self):
        payload = b'var hq_str_sz000001="";var hq_str_sz159866="short";'
        self.assertEqual(parse_payload(payload, ["159866.SZ"]), [])


if __name__ == "__main__":
    unittest.main()
