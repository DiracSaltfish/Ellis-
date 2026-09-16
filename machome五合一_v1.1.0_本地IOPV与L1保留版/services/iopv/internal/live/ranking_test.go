package live

import (
	"encoding/json"
	"net/http/httptest"
	"path/filepath"
	"testing"
	"time"
)

func rp(min string) Point {
	t, _ := time.ParseInLocation("2006-01-02 15:04", "2026-09-11 "+min, Zone)
	return Point{Symbol: "520600.SH", Date: Day(t), Minute: min, At: t, ETF: ptr(1.01), Buy: ptr(1), Mid: ptr(1.02), BuyFX: ptr(.85), MidFX: ptr(.87), FXActionable: true, HKDAssets: ptr(1000000), Unit: 1000000}
}
func TestRankingMinuteBoundaries(t *testing.T) {
	for c, n := range map[string]int{"09:30": 0, "10:00": 30, "11:30": 120, "13:00": 120, "14:00": 180, "14:30": 210, "14:45": 225, "15:00": 240, "16:00": 240} {
		if rankExpected(c) != n {
			t.Fatalf("%s %d", c, rankExpected(c))
		}
	}
	for _, m := range []string{"11:30", "12:00", "15:00", "16:00"} {
		if rankMinute(m) {
			t.Fatal(m)
		}
	}
}
func TestRankingAllMinutesAndQCParity(t *testing.T) {
	ps := []Point{}
	base := rp("09:30")
	for i := 0; i < 30; i++ {
		p := base
		p.At = p.At.Add(time.Duration(i) * time.Minute)
		p.Minute = p.At.Format("15:04")
		ps = append(ps, p)
	}
	ps = append(ps, ps[0], rp("15:30"), rp("10:00"))
	a := rankRow(Candidate{Symbol: "520600.SH", QC: "PASS"}, ps, "2026-09-11", "10:00", 30)
	b := rankRow(Candidate{Symbol: "520600.SH", QC: "WATCHLIST_PENDING_QC"}, ps, "2026-09-11", "10:00", 30)
	if !a.Pass || !b.Pass || a.Minutes != 30 || *a.Score != 100 || *b.Score != *a.Score {
		t.Fatalf("%+v %+v", a, b)
	}
	ps[0].Reasons = []string{"STALE"}
	ps = ps[:30]
	ps[1].ETF = nil
	a = rankRow(Candidate{}, ps, "2026-09-11", "10:00", 30)
	if a.Pass || a.Minutes != 28 {
		t.Fatalf("%+v", a)
	}
}
func TestRankingFixedFXAndNoFuture(t *testing.T) {
	a, b := rp("09:30"), rp("09:31")
	b.BuyFX = ptr(.86)
	b.Buy = ptr(1.01)
	b.Mid = ptr(1.02)
	r := rankRow(Candidate{}, []Point{a, b, rp("09:32")}, a.Date, "09:32", 2)
	if r.MeanBP != 0 || r.Minutes != 2 {
		t.Fatalf("%+v", r)
	}
	a.HKDAssets = nil
	r = rankRow(Candidate{}, []Point{a, b}, a.Date, "09:32", 2)
	if r.Minutes != 1 {
		t.Fatal(r.Minutes)
	}
}
func TestRankingSHFilterAndSnapshots(t *testing.T) {
	st, e := OpenStore(filepath.Join(t.TempDir(), "test.sqlite"))
	if e != nil {
		t.Fatal(e)
	}
	defer st.DB.Close()
	s := &Service{store: st, candidates: []Candidate{{Symbol: "159570.SZ"}, {Symbol: "520600.SH"}}}
	v := s.computeRanking(rp("14:30").At)
	if len(v.Rows) != 1 || v.Rows[0].Symbol != "520600.SH" {
		t.Fatal(v)
	}
	raw, _ := json.Marshal(v)
	for i := 0; i < 2; i++ {
		if _, e = st.DB.Exec("INSERT OR IGNORE INTO ranking_snapshots VALUES(?,?,?)", v.Date, "14:30", string(raw)); e != nil {
			t.Fatal(e)
		}
	}
	var n int
	st.DB.QueryRow("SELECT COUNT(*) FROM ranking_snapshots").Scan(&n)
	if n != 1 {
		t.Fatal(n)
	}
	w := httptest.NewRecorder()
	s.rankingHandler(w, httptest.NewRequest("GET", "/api/v1/ranking?date=2026-09-11&slot=14:00", nil))
	if w.Code != 404 {
		t.Fatal(w.Code)
	}
	w = httptest.NewRecorder()
	s.rankingHandler(w, httptest.NewRequest("GET", "/api/v1/ranking?date=2026-09-11&slot=14:30", nil))
	if w.Code != 200 {
		t.Fatal(w.Code)
	}
}
