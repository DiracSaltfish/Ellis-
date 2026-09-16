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

func TestHKNoTradeFreshnessBoundaries(t *testing.T) {
	now := time.Date(2026, 9, 16, 11, 29, 55, 0, Zone)
	q := Quote{Symbol: "01375.HK", Source: "internal_l1", Price: 1.62, Observed: now.Add(-144 * time.Second), Received: now.Add(-59 * time.Second)}
	if !hkLastTradeUsable(q, now) || !quoteFresh(q, now) {
		t.Fatal("recorded no-trade quote rejected")
	}
	q.Received = now.Add(-240 * time.Second)
	if !hkLastTradeUsable(q, now) {
		t.Fatal("240 second boundary rejected")
	}
	for _, source := range []string{"internal_l1", "sina_rt_hk"} {
		a := q
		a.Source = source
		a.Observed = now.Add(-time.Hour)
		if !hkLastTradeUsable(a, now) {
			t.Fatal(source)
		}
	}
	for _, change := range []func(*Quote){
		func(q *Quote) { q.Received = now.Add(-241 * time.Second) },
		func(q *Quote) { q.Received = now.Add(3 * time.Second) },
		func(q *Quote) { q.Observed = now.AddDate(0, 0, -1) },
		func(q *Quote) { q.Observed = now.Add(3 * time.Second) },
		func(q *Quote) { q.Price = math.NaN() }, func(q *Quote) { q.Price = 0 },
		func(q *Quote) { q.Symbol = "513090.SH" }, func(q *Quote) { q.Source = "unknown" },
	} {
		a := q
		change(&a)
		if hkLastTradeUsable(a, now) {
			t.Fatalf("accepted invalid quote %+v", a)
		}
	}
}
func TestHKNoTradeRecordedModel(t *testing.T) {
	dir := os.Getenv("HK_NO_TRADE_FIXTURE")
	if dir == "" {
		t.Skip("recorded fixture opt in")
	}
	var fixture struct {
		PCF    []byte  `json:"pcf"`
		Shares [][]any `json:"shares"`
	}
	raw, e := os.ReadFile(filepath.Join(dir, "fixture.json"))
	if e != nil {
		t.Fatal(e)
	}
	if e = json.Unmarshal(raw, &fixture); e != nil {
		t.Fatal(e)
	}
	var recorded struct {
		Ranking Ranking `json:"ranking"`
		Minutes []Point `json:"minutes"`
	}
	raw, e = os.ReadFile(filepath.Join(dir, "evidence.json"))
	if e != nil {
		t.Fatal(e)
	}
	if e = json.Unmarshal(raw, &recorded); e != nil {
		t.Fatal(e)
	}
	b, e := iopv.ParsePCF(fixture.PCF, "513090.SH", "2026-09-16")
	if e != nil {
		t.Fatal(e)
	}
	st, e := OpenStore(filepath.Join(t.TempDir(), "test.sqlite"))
	if e != nil {
		t.Fatal(e)
	}
	defer st.DB.Close()
	for _, row := range fixture.Shares {
		if _, e = st.DB.Exec("INSERT INTO daily_shares VALUES(?,?,?,?,?,?)", row...); e != nil {
			t.Fatal(e)
		}
	}
	s := &Service{store: st}
	var old RankingRow
	for _, r := range recorded.Ranking.Rows {
		if r.Symbol == b.Symbol {
			old = r
		}
	}
	if old.Score != nil {
		t.Fatal("fixture was already scored")
	}
	fx := iopv.FX{Midpoint: *old.MidFX, Settlement: *old.BuyFX, ObservedAt: old.FXAt}
	f, e := s.modelFeatures(recorded.Minutes, b, b.Date, "11:30", fx)
	if e != nil {
		t.Fatal(e)
	}
	r := old
	s.setFlowFeatures(&r, f, fx, "11:30")
	if r.Score == nil || r.LastMinute != "11:29" || r.Minutes != 120 {
		t.Fatalf("unexpected repaired model %+v", r)
	}
	raw, _ = json.MarshalIndent(r, "", "  ")
	os.WriteFile(filepath.Join(dir, "recomputed.json"), raw, 0600)
	t.Logf("original score=nil; fixed score=%.9f minutes=%d last=%s", *r.Score, r.Minutes, r.LastMinute)
}
