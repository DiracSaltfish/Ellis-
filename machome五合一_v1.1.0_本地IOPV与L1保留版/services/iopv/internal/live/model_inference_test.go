package live

import (
	"encoding/json"
	iopv "intranet-iopv"
	"math"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestFlowModelPythonParity(t *testing.T) {
	for _, key := range []string{"1430", "1445"} {
		raw, e := os.ReadFile("models/golden_" + key + ".json")
		if e != nil {
			t.Fatal(e)
		}
		var cases []struct {
			X     []*float64 `json:"x"`
			Score float64    `json:"score"`
		}
		if e = json.Unmarshal(raw, &cases); e != nil {
			t.Fatal(e)
		}
		for i, c := range cases {
			x := make([]float64, len(c.X))
			for j, p := range c.X {
				x[j] = math.NaN()
				if p != nil {
					x[j] = *p
				}
			}
			got := flowModels[key].score(x)
			if math.Abs(got-c.Score) > 1e-12 {
				t.Fatalf("%s sample %d got %.16f want %.16f", key, i, got, c.Score)
			}
		}
	}
}
func TestModelFeatureQuality(t *testing.T) {
	st, e := OpenStore(filepath.Join(t.TempDir(), "x.sqlite"))
	if e != nil {
		t.Fatal(e)
	}
	defer st.DB.Close()
	s := &Service{store: st}
	for _, d := range []string{"2026-09-10", "2026-09-09", "2026-09-08", "2026-09-07", "2026-09-04"} {
		st.DB.Exec("INSERT INTO daily_shares VALUES(?,?,?,?,?,?)", "520600.SH", d, 100., 1., "test", "")
	}
	yes := true
	b := iopv.Basket{Symbol: "520600.SH", Date: "2026-09-11", PrevDate: "2026-09-10", PrevNAV: ptr(1), Unit: 1000000, Hash: "h", CreationAllowed: &yes, RedemptionAllowed: &yes}
	ps := []Point{}
	for i := 0; i < 30; i++ {
		p := rp("09:30")
		p.At = p.At.Add(time.Duration(i) * time.Minute)
		p.Minute = p.At.Format("15:04")
		p.Hash = "h"
		p.HKDAssets = ptr(1000000)
		p.CNYAssets = ptr(100000)
		p.CumulativeAmount = ptr(float64(i+1) * 1000)
		ps = append(ps, p)
	}
	fx := iopv.FX{Midpoint: .87, Settlement: .85}
	f, e := s.modelFeatures(ps, b, b.Date, "10:00", fx)
	if e != nil {
		t.Fatal(e)
	}
	if math.Abs(f["turnover_pct"]-3) > 1e-12 || math.Abs(f["lag_flow_pct"]-100./99) > 1e-12 || math.Abs(f["settlement_mean_bp"]-(1.01/.95-1)*10000) > 1e-9 {
		t.Fatal(f)
	}
	if !math.IsNaN(f["v2_creation_limit_pct"]) || f["v2_creation_limit_known"] != 0 {
		t.Fatal("unknown cap changed into zero")
	}
	b.PrevDate = "2026-09-09"
	if _, e = s.modelFeatures(ps, b, b.Date, "10:00", fx); e == nil {
		t.Fatal("stale shares accepted")
	}
	b.PrevDate = "2026-09-10"
	ps[20].CumulativeAmount = ptr(0)
	if _, e = s.modelFeatures(ps, b, b.Date, "10:00", fx); e == nil {
		t.Fatal("amount reset accepted")
	}
	ps[20].CumulativeAmount = ptr(21000)
	ps[0].CNYAssets = nil
	if _, e = s.modelFeatures(ps, b, b.Date, "10:00", fx); e == nil {
		t.Fatal("missing first bar accepted")
	}
}
func TestL1AmountMetadata(t *testing.T) {
	now := time.Now()
	b := l1Book{Symbol: "520600.SH", QT: now.UnixMilli(), RT: now.UnixMilli(), Price: 1, Amount: ptr(123456.78), BP: make([]float64, 5), AP: make([]float64, 5), BV: make([]float64, 5), AV: make([]float64, 5)}
	q, e := decodeL1(b, now, 1)
	if e != nil || q.Amount == nil || *q.Amount != 123456.78 {
		t.Fatal(q, e)
	}
	b.Amount = nil
	q, e = decodeL1(b, now, 1)
	if e != nil || q.Amount != nil {
		t.Fatal("missing amount became zero")
	}
}

func TestOpeningFXGapRevaluation(t *testing.T) {
	st, e := OpenStore(filepath.Join(t.TempDir(), "opening.sqlite"))
	if e != nil {
		t.Fatal(e)
	}
	defer st.DB.Close()
	s := &Service{store: st}
	for _, d := range []string{"2026-09-10", "2026-09-09", "2026-09-08", "2026-09-07", "2026-09-04"} {
		st.DB.Exec("INSERT INTO daily_shares VALUES(?,?,?,?,?,?)", "520600.SH", d, 100., 1., "test", "")
	}
	yes := true
	b := iopv.Basket{Symbol: "520600.SH", Date: "2026-09-11", PrevDate: "2026-09-10", PrevNAV: ptr(1), Unit: 1000000, Hash: "h", CreationAllowed: &yes, RedemptionAllowed: &yes}
	ps := []Point{}
	for i := 0; i < 60; i++ {
		p := rp("09:30")
		p.At = p.At.Add(time.Duration(i) * time.Minute)
		p.Minute = p.At.Format("15:04")
		p.Hash = "h"
		p.CNYAssets = ptr(100000)
		p.CumulativeAmount = ptr(float64(i+1) * 1000)
		ps = append(ps, p)
	}
	for i := 0; i < 2; i++ {
		ps[i].Reasons = []string{"SETTLEMENT_FX_UNAVAILABLE"}
		ps[i].Buy = nil
		ps[i].BuyFX = nil
	}
	fx := iopv.FX{Midpoint: .87, Settlement: .85}
	f, e := s.modelFeatures(ps, b, b.Date, "10:30", fx)
	if e != nil || f["model_minutes"] != 60 {
		t.Fatal(f, e)
	}
	ps[0].Reasons = []string{"QUOTE_MISSING"}
	ps[0].HKDAssets = nil
	f, e = s.modelFeatures(ps, b, b.Date, "10:30", fx)
	if e != nil || f["model_minutes"] != 59 || f["first_minute_id"] != 571 {
		t.Fatal(f, e)
	}
	for i := 0; i < 4; i++ {
		ps[i].Reasons = []string{"QUOTE_MISSING"}
		ps[i].HKDAssets = nil
	}
	if _, e = s.modelFeatures(ps, b, b.Date, "10:30", fx); e == nil {
		t.Fatal("accepted coverage below 95%")
	}
	if _, e = s.modelFeaturesCoverage(ps, b, b.Date, "10:30", fx, .8); e != nil {
		t.Fatal("reference coverage rejected", e)
	}
	for i := 0; i < 13; i++ {
		ps[i].HKDAssets = nil
	}
	if _, e = s.modelFeaturesCoverage(ps, b, b.Date, "10:30", fx, .8); e == nil {
		t.Fatal("accepted reference below 80%")
	}
}
