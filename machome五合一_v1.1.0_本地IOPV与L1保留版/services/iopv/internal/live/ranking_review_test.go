package live

import (
	"encoding/json"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestVerifiedLastTradeIsNotFeedStaleness(t *testing.T) {
	p := rp("10:00")
	p.Stale = []string{"01681.HK"}
	p.Reasons = []string{"STALE_COMPONENT_MARKS"}
	p.CNYAssets = ptr(10)
	p.ComponentIssues = []ComponentIssue{{Symbol: "01681.HK", Source: "sina_rt_hk", Price: 13.31, Observed: p.At.Add(-30 * time.Minute), Received: p.At.Add(-5 * time.Second)}}
	if !modelPointUsable(p, p.Date, "10:01") {
		t.Fatal("fresh no-trade verification rejected")
	}
	p.ComponentIssues[0].Received = p.At.Add(-241 * time.Second)
	if modelPointUsable(p, p.Date, "10:01") {
		t.Fatal("stale response admitted")
	}
	p.ComponentIssues[0].Received = p.At
	p.ComponentIssues[0].Source = "internal_l1"
	if !modelPointUsable(p, p.Date, "10:01") {
		t.Fatal("fresh internal no-trade quote rejected")
	}
	p.ComponentIssues[0].Source = "sina_rt_hk"
	p.Reasons = append(p.Reasons, "QUOTE_MISSING")
	if modelPointUsable(p, p.Date, "10:01") {
		t.Fatal("missing price admitted")
	}
	p.Reasons = []string{"STALE_COMPONENT_MARKS"}
	p.Stale = append(p.Stale, "00700.HK")
	if modelPointUsable(p, p.Date, "10:01") {
		t.Fatal("partially verified basket admitted")
	}
}
func TestSinaColdQuoteProbeIsBounded(t *testing.T) {
	s := &Service{quotes: map[string]Quote{}, cfg: Config{SinaFallbackSymbols: []string{"01698.HK"}}}
	now := time.Now()
	for i := 0; i < 100; i++ {
		s.plan.Subscriptions = append(s.plan.Subscriptions, strings.Repeat("0", 3)+string(rune('1'+i/10))+string(rune('0'+i%10))+".HK")
	}
	if len(s.sinaProbeSymbols(now)) != 32 {
		t.Fatal("probe not bounded")
	}
	s.cfg.SinaFallbackSymbols = []string{}
	if len(s.sinaProbeSymbols(now)) != 0 {
		t.Fatal("disabled supplement probed")
	}
}
func TestRankingReviewRecordedFixture(t *testing.T) {
	root := os.Getenv("RANKING_REVIEW_FIXTURE")
	if root == "" {
		t.Skip("production fixture opt-in")
	}
	raw, e := os.ReadFile(filepath.Join(root, "original.json"))
	if e != nil {
		t.Fatal(e)
	}
	var original Ranking
	if e = json.Unmarshal(raw, &original); e != nil {
		t.Fatal(e)
	}
	got, e := ReviewRanking(filepath.Join(root, "test.sqlite"), root, original)
	if e != nil {
		t.Fatal(e)
	}
	before, after := 0, 0
	by := map[string]RankingRow{}
	for _, r := range original.Rows {
		by[r.Symbol] = r
		if r.Score != nil {
			before++
		}
	}
	for _, r := range got.Rows {
		if r.Score != nil {
			after++
		}
		old := by[r.Symbol]
		if old.Score != nil && (*old.Score != *r.Score || old.Pass != r.Pass) {
			t.Fatal("original score changed", r.Symbol)
		}
		if old.Score == nil && r.Score != nil && (r.Pass || r.Coverage < .8) {
			t.Fatal("reference gate broken", r)
		}
	}
	if after <= before {
		t.Fatal("no coverage improvement", before, after)
	}
	t.Logf("scored %d -> %d", before, after)
	output, _ := json.MarshalIndent(got, "", "  ")
	if e = os.WriteFile(filepath.Join(root, "review.json"), output, 0600); e != nil {
		t.Fatal(e)
	}
}

func TestRankingRestoresSavedSnapshotAfterRestart(t *testing.T) {
	st, e := OpenStore(filepath.Join(t.TempDir(), "ranking.sqlite"))
	if e != nil {
		t.Fatal(e)
	}
	defer st.DB.Close()
	saved := Ranking{Date: "2026-09-15", Cutoff: "15:00", Rows: []RankingRow{{Symbol: "513090.SH", Score: ptr(.8)}}}
	raw, _ := json.Marshal(saved)
	if _, e = st.DB.Exec("INSERT INTO ranking_snapshots VALUES(?,?,?)", saved.Date, saved.Cutoff, string(raw)); e != nil {
		t.Fatal(e)
	}
	s := &Service{store: st}
	w := httptest.NewRecorder()
	s.rankingHandler(w, httptest.NewRequest("GET", "/api/v1/ranking?date=2026-09-15", nil))
	var got Ranking
	if e = json.Unmarshal(w.Body.Bytes(), &got); e != nil || len(got.Rows) != 1 || got.Cutoff != "15:00" || !strings.Contains(got.Status, "非新实时计算") {
		t.Fatal(got, e)
	}
}
