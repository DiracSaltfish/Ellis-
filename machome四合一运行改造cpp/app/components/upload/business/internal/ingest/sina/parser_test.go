package sina

import (
	"math"
	"testing"
	"time"
)

func TestToSinaSymbolExtendedPrefixes(t *testing.T) {
	cases := map[string]string{
		"hf_cl":      "hf_CL",
		"hf_nq":      "hf_NQ",
		"nf_au0":     "nf_AU0",
		"znb_nky":    "znb_NKY",
		"b_hsi":      "b_HSI",
		"fx_susdcnh": "fx_susdcnh",
		"QQQ":        "gb_qqq",
		"HD":         "gb_hd",
		"F":          "gb_f",
		"00700":      "rt_hk00700",
		"^HSI":       "b_HSI",
		"SHOP":       "gb_shop",
	}
	for input, want := range cases {
		if got := ToSinaSymbol(input); got != want {
			t.Fatalf("ToSinaSymbol(%q) = %q, want %q", input, got, want)
		}
	}
}

func TestParseAStockPayloadOrderBook(t *testing.T) {
	q := parsePayload("sh513210", "SH513210", "Fund,1.500,1.506,1.531,1.534,1.500,1.531,1.532,4445500,6782842.000,183400,1.531,480800,1.530,100300,1.528,100300,1.527,211300,1.526,150000,1.532,692600,1.533,243000,1.534,15400,1.535,6700,1.537,2026-06-02,14:09:23,00,")

	assertClose(t, q.LimitUp, 1.657)
	assertClose(t, q.LimitDown, 1.355)
	if len(q.BidLevels) != 5 || len(q.AskLevels) != 5 {
		t.Fatalf("expected 5 bid and ask levels, got %d/%d", len(q.BidLevels), len(q.AskLevels))
	}
	assertClose(t, q.BidLevels[0].Price, 1.531)
	assertClose(t, q.BidLevels[0].Volume, 183400)
	assertClose(t, q.AskLevels[4].Price, 1.537)
	assertClose(t, q.AskLevels[4].Volume, 6700)
}

func TestParseHFFuturePayload(t *testing.T) {
	q := parsePayload("hf_CL", "hf_CL", "91.398,,91.410,91.430,92.650,91.260,12:05:44,92.160,92.450,0,2,2,2026-06-02,纽约原油,0")
	now := mustShanghaiTime(t, "2026-06-02 12:06:00")
	finalizeQuote(&q, "hf_CL", now)

	assertClose(t, q.Price, 91.398)
	assertClose(t, q.High, 92.650)
	if q.QuoteDate != "2026-06-02" || q.QuoteTime != "12:05:44" {
		t.Fatalf("unexpected quote time: %s %s", q.QuoteDate, q.QuoteTime)
	}
	if !q.IsRealtime || q.RealtimeStatus != "realtime" {
		t.Fatalf("expected realtime hf quote, got %q/%v: %s", q.RealtimeStatus, q.IsRealtime, q.StaleReason)
	}
}

func TestParseNQFuturePayload(t *testing.T) {
	q := parsePayload("hf_NQ", "HF_NQ", "30707.195,,30696.750,30697.500,30807.750,30496.000,22:47:53,30712.750,30741.750,0,4,2,2026-06-03,纳斯达克指数期货,0")
	now := mustShanghaiTime(t, "2026-06-03 22:48:10")
	finalizeQuote(&q, "hf_NQ", now)

	assertClose(t, q.Price, 30707.195)
	assertClose(t, q.PrevClose, 30712.750)
	if q.QuoteDate != "2026-06-03" || q.QuoteTime != "22:47:53" {
		t.Fatalf("unexpected quote time: %s %s", q.QuoteDate, q.QuoteTime)
	}
	if !q.IsRealtime || q.RealtimeStatus != "realtime" {
		t.Fatalf("expected realtime NQ quote, got %q/%v: %s", q.RealtimeStatus, q.IsRealtime, q.StaleReason)
	}
}

func TestParseNFFuturePayloadClosedAtLunch(t *testing.T) {
	q := parsePayload("nf_AU0", "nf_AU0", "黄金连续,113000,982.600,984.020,972.580,0.000,981.220,981.600,981.440,0.000,993.080,2,1,192200.000,168366,沪,黄金,2026-06-02,1,,,,,,,,,977.841,0.000,0,0.000")
	now := mustShanghaiTime(t, "2026-06-02 12:06:00")
	finalizeQuote(&q, "nf_AU0", now)

	assertClose(t, q.Price, 982.600)
	if q.QuoteTime != "11:30:00" {
		t.Fatalf("unexpected compact quote time: %q", q.QuoteTime)
	}
	if q.RealtimeStatus != "closed" {
		t.Fatalf("expected closed at lunch, got %q", q.RealtimeStatus)
	}
}

func TestParseZNBIndexPayload(t *testing.T) {
	q := parsePayload("znb_NKY", "znb_NKY", "日经225,66000.2400,-934.09,-1.40,2:12 AM,1759126320,2026-06-02,12:06:15,66629.6000,66934.3300,66748.0600,65551.1300,0")
	now := mustShanghaiTime(t, "2026-06-02 12:06:30")
	finalizeQuote(&q, "znb_NKY", now)

	assertClose(t, q.Price, 66000.2400)
	assertClose(t, q.PrevClose, 66934.3300)
	if !q.IsRealtime {
		t.Fatalf("expected realtime znb quote, got %q: %s", q.RealtimeStatus, q.StaleReason)
	}
}

func TestParseShortZNBIndexPayload(t *testing.T) {
	q := parsePayload("znb_TPX", "znb_TPX", "东证一部股价指数,3132.60,-54.42,-1.71,2:12 AM,1759126320")
	now := mustShanghaiTime(t, "2026-06-02 12:06:30")
	finalizeQuote(&q, "znb_TPX", now)

	assertClose(t, q.Price, 3132.60)
	if q.QuoteDate != "2025-09-29" || q.QuoteTime != "14:12:00" {
		t.Fatalf("unexpected short znb timestamp: %s %s", q.QuoteDate, q.QuoteTime)
	}
	if q.RealtimeStatus != "stale" {
		t.Fatalf("expected stale short znb quote, got %q", q.RealtimeStatus)
	}
}

func TestParseUSAfterHoursPayload(t *testing.T) {
	q := parsePayload("gb_qqq", "QQQ", "纳百ETF,742.7400,0.60,2026-06-02 09:44:51,4.4300,737.0400,745.6500,735.9900,745.6500,513.1580,33890559,39455279,0,0.00,--,0.00,0.00,0.00,0.00,0,0,740.2837,-0.33,-2.46,Jun 01 07:59PM EDT,Jun 01 04:00PM EDT,738.3100,2459920,1,2026,25123020982.0000,743.0549,607.2900,1825490746.3665,742.7200,738.3100")
	now := mustShanghaiTime(t, "2026-06-02 08:00:30")
	finalizeQuote(&q, "gb_qqq", now)

	assertClose(t, q.Price, 740.2837)
	if q.Source != "sina_us_after_hours" || q.QuoteSession != "extended" {
		t.Fatalf("expected after-hours source/session, got %q/%q", q.Source, q.QuoteSession)
	}
	if q.QuoteDate != "2026-06-02" || q.QuoteTime != "07:59:00" {
		t.Fatalf("unexpected after-hours quote time: %s %s", q.QuoteDate, q.QuoteTime)
	}
	if !q.IsRealtime {
		t.Fatalf("expected after-hours quote to be realtime near timestamp, got %q: %s", q.RealtimeStatus, q.StaleReason)
	}
}

func mustShanghaiTime(t *testing.T, value string) time.Time {
	t.Helper()
	parsed, err := time.ParseInLocation("2006-01-02 15:04:05", value, shanghaiLocation())
	if err != nil {
		t.Fatal(err)
	}
	return parsed
}

func assertClose(t *testing.T, got float64, want float64) {
	t.Helper()
	if math.Abs(got-want) > 0.0001 {
		t.Fatalf("got %.6f, want %.6f", got, want)
	}
}
