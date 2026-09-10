package live

import (
	"encoding/json"
	iopv "intranet-iopv"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestDailySuspensionImpact(t *testing.T) {
	st, e := OpenStore(filepath.Join(t.TempDir(), "test.sqlite"))
	if e != nil {
		t.Fatal(e)
	}
	defer st.DB.Close()
	now := time.Date(2026, 9, 9, 10, 30, 0, 0, Zone)
	s := &Service{store: st, candidates: []Candidate{{Symbol: "513090.SH", Name: "test"}}, baskets: map[string]iopv.Basket{"513090.SH": {Date: Day(now), Components: []iopv.Component{{Symbol: "00853.HK"}}}}, quotes: map[string]Quote{}}
	a, e := s.SuspensionImpacts(now)
	if e != nil || len(a) != 1 || a[0].Status != "pending" || len(a[0].Funds) != 1 {
		t.Fatalf("missing must be pending: %+v %v", a, e)
	}
	v := Suspension{Date: Day(now), Symbol: "00853.HK", Status: "suspended", Note: "test evidence", ConfirmedAt: now}
	if e = st.SetSuspension(v); e != nil {
		t.Fatal(e)
	}
	a, e = s.SuspensionImpacts(now)
	if e != nil || a[0].Status != "suspended" {
		t.Fatal(a, e)
	}
	next := now.AddDate(0, 0, 1)
	states, e := st.Suspensions(Day(next))
	if e != nil || len(states) != 0 {
		t.Fatal("yesterday must not carry", states, e)
	}
	b := s.baskets["513090.SH"]
	b.Date = Day(next)
	s.baskets["513090.SH"] = b
	a, e = s.SuspensionImpacts(next)
	if e != nil || len(a) != 1 || a[0].Status != "pending" {
		t.Fatal("new day confirmation", a, e)
	}
	b.Date = Day(now)
	s.baskets["513090.SH"] = b
	v.Status = "trading"
	if e = st.SetSuspension(v); e != nil {
		t.Fatal(e)
	}
	a, e = s.SuspensionImpacts(now)
	if e != nil || a[0].Status != "trading" {
		t.Fatal(a, e)
	}
	s.quotes["00853.HK"] = Quote{Price: 10, Observed: now}
	a, e = s.SuspensionImpacts(now)
	if e != nil || len(a) != 0 {
		t.Fatal("resumed quote should clear impact", a, e)
	}
	v.Status = "unknown"
	if st.SetSuspension(v) == nil {
		t.Fatal("invalid status accepted")
	}
}

func TestFXUpstreamFailureRecovery(t *testing.T) {
	raw, e := os.ReadFile("../../testdata/fx_20260909.json")
	if e != nil {
		t.Fatal(e)
	}
	var snapshot map[string]any
	if e = json.Unmarshal(raw, &snapshot); e != nil {
		t.Fatal(e)
	}
	now := time.Date(2026, 9, 9, 11, 13, 36, 0, Zone)
	snapshot["generated_at"] = now.Add(-55 * time.Second).Format(time.RFC3339)
	quote := snapshot["fx"].(map[string]any)
	quote["observed_at"] = now.Add(-219 * time.Second).Format(time.RFC3339)
	quote["healthy"] = false
	snapshot["status"] = "cfets_unavailable"
	raw, _ = json.Marshal(snapshot)
	if _, e = SessionFX(raw, Day(now), "shanghai", "buy_hk", now); e == nil {
		t.Fatal("unhealthy FX accepted")
	}
	quote["healthy"] = true
	quote["observed_at"] = now.Add(-39 * time.Second).Format(time.RFC3339)
	snapshot["status"] = "reference_only"
	raw, _ = json.Marshal(snapshot)
	for _, channel := range []string{"shanghai", "shenzhen"} {
		f, e := SessionFX(raw, Day(now), channel, "buy_hk", now)
		if e != nil || f.Settlement <= 0 {
			t.Fatal("recovery failed", channel, f, e)
		}
	}
}
