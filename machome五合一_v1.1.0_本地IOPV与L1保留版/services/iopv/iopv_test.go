package iopv

import (
	"encoding/json"
	"fmt"
	"math"
	"os"
	"strings"
	"testing"
	"time"
)

func near(t *testing.T, got, want float64) {
	t.Helper()
	if math.Abs(got-want) > 1e-11 {
		t.Fatalf("got %.14f want %.14f", got, want)
	}
}
func mustRead(t *testing.T, p string) []byte {
	t.Helper()
	b, e := os.ReadFile(p)
	if e != nil {
		t.Fatal(e)
	}
	return b
}
func TestCoreCashAndFX(t *testing.T) {
	rows := []CoreRow{{Quantity: 100, Price: 20, Mode: 0}, {Quantity: 10, Price: 5, Mode: 1}, {Cash: 30, Mode: 2}}
	v, e := Calculate(rows, 1000, -10, .86, .85)
	if e != nil {
		t.Fatal(e)
	}
	near(t, v.Midpoint, 1.79)
	near(t, v.Settlement, 1.77)
	near(t, v.HKDAssets, 2000)
	near(t, v.CNYAssets, 70)
	p := PremiumAt(1.8, &v)
	near(t, *p.MidpointPct, (1.8/1.79-1)*100)
}
func TestCoreRejects(t *testing.T) {
	for name, rows := range map[string][]CoreRow{"unknown_mode": {{Mode: 3}}, "nan_price": {{Price: math.NaN(), Quantity: 1}}, "zero_price": {{Quantity: 1}}, "negative_quantity": {{Price: 1, Quantity: -1}}, "negative_cash": {{Mode: 2, Cash: -1}}, "overflow": {{Quantity: 1e308, Price: 1e308}}, "empty": nil} {
		t.Run(name, func(t *testing.T) {
			if _, e := Calculate(rows, 100, 0, .86, .85); e == nil {
				t.Fatal("accepted invalid input")
			}
		})
	}
	for _, x := range []float64{0, -1, math.NaN(), math.Inf(1)} {
		if _, e := Calculate([]CoreRow{{Price: 1, Quantity: 1}}, x, 0, .86, .85); e == nil {
			t.Fatal("bad unit")
		}
		if _, e := Calculate([]CoreRow{{Price: 1, Quantity: 1}}, 1, 0, x, .85); e == nil {
			t.Fatal("bad FX")
		}
	}
	if PremiumAt(0, &Values{Midpoint: 1, Settlement: 1}).MidpointPct != nil {
		t.Fatal("zero price")
	}
}

type evidenceRow struct {
	Symbol   string
	Date     string
	Expected float64
	SHA256   string `json:"sha256"`
}
type closePrice struct {
	Price float64
	Date  string
}

func fixtures(t *testing.T) ([]Basket, map[string]closePrice, []evidenceRow) {
	t.Helper()
	var manifest []evidenceRow
	var prices map[string]closePrice
	if e := json.Unmarshal(mustRead(t, "testdata/manifest.json"), &manifest); e != nil {
		t.Fatal(e)
	}
	if e := json.Unmarshal(mustRead(t, "testdata/close_prices.json"), &prices); e != nil {
		t.Fatal(e)
	}
	var baskets []Basket
	for _, m := range manifest {
		b, e := ParsePCF(mustRead(t, "testdata/pcf/"+strings.Split(m.Symbol, ".")[0]+".xml"), m.Symbol, m.Date)
		if e != nil {
			t.Fatal(e)
		}
		if b.Hash != m.SHA256 {
			t.Fatal("evidence hash changed")
		}
		baskets = append(baskets, b)
	}
	return baskets, prices, manifest
}
func TestHistorical120Funds(t *testing.T) {
	bs, prices, manifest := fixtures(t)
	if len(bs) != 120 {
		t.Fatal(len(bs))
	}
	for i, b := range bs {
		t.Run(b.Symbol, func(t *testing.T) {
			var rows []CoreRow
			for _, c := range b.Components {
				q := prices[c.Symbol]
				if c.Mode != 2 && !positive(q.Price) {
					t.Fatal("missing fixture price", c.Symbol)
				}
				rows = append(rows, CoreRow{Quantity: c.Quantity, Price: q.Price, Cash: c.Cash, Mode: c.Mode})
			}
			v, e := Calculate(rows, b.Unit, b.Cash, .86482, .86482)
			if e != nil {
				t.Fatal(e)
			}
			near(t, v.Midpoint, manifest[i].Expected)
			if b.Symbol == "513090.SH" {
				near(t, v.Midpoint, 1.8432886185282)
			}
		})
	}
}
func TestPCFGuards(t *testing.T) {
	raw := string(mustRead(t, "testdata/pcf/159217.xml"))
	for name, s := range map[string]string{"date": strings.Replace(raw, "<TradingDay>20260908", "<TradingDay>20260909", 1), "cash": strings.Replace(raw, "<EstimateCashComponent>-406.24</EstimateCashComponent>", "", 1), "unknown_flag": strings.Replace(raw, "<SubstituteFlag>1", "<SubstituteFlag>9", 1), "bad_count": strings.Replace(raw, "<TotalRecordNum>37", "<TotalRecordNum>36", 1), "bad_qty": strings.Replace(raw, "<ComponentShare>1240.00", "<ComponentShare>NaN", 1)} {
		t.Run(name, func(t *testing.T) {
			if _, e := ParsePCF([]byte(s), "159217.SZ", "2026-09-08"); e == nil {
				t.Fatal("accepted bad PCF")
			}
		})
	}
	b, e := ParsePCF([]byte(raw), "159217.SZ", "2026-09-08")
	if e != nil {
		t.Fatal(e)
	}
	if len(b.Components) != 36 {
		t.Fatal("cash placeholder counted")
	}
	near(t, b.Cash, -406.24)
}
func TestPlannerAndScheduler(t *testing.T) {
	bs, _, _ := fixtures(t)
	p, e := BuildPlan(bs)
	if e != nil {
		t.Fatal(e)
	}
	if len(p.Subscriptions) != 750 {
		t.Fatal(len(p.Subscriptions))
	}
	seen := map[string]bool{}
	for _, g := range p.Groups {
		for _, s := range g {
			if seen[s] {
				t.Fatal("duplicate")
			}
			seen[s] = true
		}
	}
	if len(seen) != 120 {
		t.Fatal("missing ETF")
	}
	var s Scheduler
	for _, tc := range []struct {
		ms    int
		group int
		due   bool
	}{{0, 0, true}, {2999, 0, false}, {3000, 1, true}, {6000, 2, true}, {9000, 0, true}, {19000, 0, true}, {18000, 0, false}, {21000, 1, true}} {
		g, ok := s.Due(time.Duration(tc.ms) * time.Millisecond)
		if ok != tc.due || (ok && g != tc.group) {
			t.Fatalf("tick %+v got %d %t", tc, g, ok)
		}
	}
	t.Logf("group loads %v; sizes %d/%d/%d; subscriptions %d", p.Loads, len(p.Groups[0]), len(p.Groups[1]), len(p.Groups[2]), len(p.Subscriptions))
}
func TestFXMapping(t *testing.T) {
	raw := mustRead(t, "testdata/fx_20260909.json")
	now, _ := time.Parse(time.RFC3339, "2026-09-09T10:19:40+08:00")
	for _, tc := range []struct {
		ch, dir string
		want    float64
	}{{"shanghai", "buy_hk", .8557154630097473}, {"shanghai", "sell_hk", .8556845369902525}, {"shenzhen", "buy_hk", .855673863548989}, {"shenzhen", "sell_hk", .8557261364510108}} {
		f, e := ParseFX(raw, "2026-09-09", tc.ch, tc.dir, now)
		if e != nil {
			t.Fatal(e)
		}
		near(t, f.Midpoint, .86418)
		near(t, f.Settlement, tc.want)
		if f.Actionable {
			t.Fatal("reference-only promoted")
		}
	}
	for _, tc := range []struct {
		date, ch, dir string
		at            time.Time
	}{{"2026-09-08", "shanghai", "buy_hk", now}, {"2026-09-09", "", "buy_hk", now}, {"2026-09-09", "shanghai", "", now}, {"2026-09-09", "shanghai", "buy_hk", now.Add(4 * time.Minute)}} {
		if _, e := ParseFX(raw, tc.date, tc.ch, tc.dir, tc.at); e == nil {
			t.Fatal("bad FX accepted")
		}
	}
}
func TestEvaluationQuality(t *testing.T) {
	now, _ := time.Parse(time.RFC3339, "2026-09-09T10:00:00+08:00")
	b := Basket{Symbol: "TEST.SH", Date: "2026-09-09", Unit: 100, Components: []Component{{Symbol: "00700.HK", Quantity: 10}}}
	q := map[string]Quote{"00700.HK": {Price: 20, ObservedAt: now, ReceivedAt: now, Status: "live", Source: "fixture"}}
	fx := FX{Midpoint: .86, Settlement: .85, Date: b.Date, ObservedAt: now, Channel: "shanghai", Direction: "buy_hk", Status: "reference_only"}
	out := Evaluate(b, q, fx, now, true)
	if out.Values == nil || out.Eligible {
		t.Fatal("reference-only handling")
	}
	if Evaluate(b, q, fx, now, false).Values != nil {
		t.Fatal("QC bypass")
	}
	if Evaluate(b, nil, fx, now, true).Values != nil {
		t.Fatal("missing quote bypass")
	}
	if Evaluate(b, q, fx, now.Add(31*time.Second), true).Values != nil {
		t.Fatal("stale bypass")
	}
	if Evaluate(b, q, fx, now.Add(-time.Second), true).Values != nil {
		t.Fatal("future data")
	}
}
func TestChartAndPremiumSessions(t *testing.T) {
	at := func(s string) time.Time {
		v, e := time.Parse(time.RFC3339, "2026-09-09T"+s+":00+08:00")
		if e != nil {
			t.Fatal(e)
		}
		return v
	}
	for _, tc := range []struct {
		s               string
		chart, domestic bool
	}{{"09:29", false, false}, {"09:30", true, true}, {"11:30", true, true}, {"11:31", true, false}, {"12:00", true, false}, {"12:01", false, false}, {"13:00", true, true}, {"15:00", true, true}, {"15:01", true, false}, {"16:08", true, false}, {"16:09", false, false}} {
		if ChartMinute(at(tc.s)) != tc.chart || DomesticMinute(at(tc.s)) != tc.domestic {
			t.Fatal(tc)
		}
	}
	for _, clock := range []string{"11:31", "15:01", "16:08"} {
		now := at(clock)
		e := Evaluation{Symbol: "513090.SH", Date: "2026-09-09", CalculatedAt: now, Values: &Values{Midpoint: 1.85, Settlement: 1.83}, Eligible: true}
		q := Quote{Price: 1.846, ObservedAt: now, ReceivedAt: now, Status: "live", Source: "fixture"}
		p, err := SampleMinute(now, e, &q)
		if err != nil || p.Values == nil || p.ETFPrice != nil || p.Premium.MidpointPct != nil || p.Eligible {
			t.Fatal("false domestic premium", clock, p)
		}
	}
	now := at("15:00")
	v := Values{Midpoint: 1.85, Settlement: 1.83}
	book := Book{ObservedAt: now}
	for i := 0; i < 5; i++ {
		book.Bids[i] = Level{1.846 - float64(i)*.001, 100}
		book.Asks[i] = Level{1.847 + float64(i)*.001, 100}
	}
	b, a := BookPremiums(book, &v, now)
	for i := 0; i < 5; i++ {
		near(t, *b[i].Premium.MidpointPct, (book.Bids[i].Price/1.85-1)*100)
		near(t, *a[i].Premium.SettlementPct, (book.Asks[i].Price/1.83-1)*100)
	}
	b, _ = BookPremiums(book, &v, now.Add(time.Minute))
	if b[0].Premium.MidpointPct != nil {
		t.Fatal("postclose book premium")
	}
}
func BenchmarkBasketCore(b *testing.B) {
	rows := make([]CoreRow, 100)
	for i := range rows {
		rows[i] = CoreRow{Quantity: 100, Price: 20}
	}
	for i := 0; i < b.N; i++ {
		if _, e := Calculate(rows, 100000, 0, .86, .85); e != nil {
			b.Fatal(e)
		}
	}
}
func ExampleCalculate() {
	v, _ := Calculate([]CoreRow{{Quantity: 100, Price: 20}}, 1000, 10, .86, .85)
	fmt.Printf("%.3f %.3f\n", v.Midpoint, v.Settlement)
	// Output: 1.730 1.710
}
