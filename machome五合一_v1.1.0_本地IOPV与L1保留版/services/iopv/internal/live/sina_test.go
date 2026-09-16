package live

import (
	"context"
	iopv "intranet-iopv"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestSinaValidation(t *testing.T) {
	now := time.Date(2026, 9, 10, 10, 56, 40, 0, Zone)
	valid := `var hq_str_rt_hk09866="NIO,NIO,28,29,30,27,28.32,-1,-4,28,29,10000,100,0,0,60,28,2026/09/10,10:56:35";`
	tests := map[string]string{"valid": valid, "delayed": strings.Replace(valid, "rt_hk", "hk", 1), "stale": strings.Replace(valid, "10:56:35", "10:30:00", 1), "yesterday": strings.Replace(valid, "2026/09/10", "2026/09/09", 1), "future": strings.Replace(valid, "10:56:35", "10:57:00", 1), "zero_volume": strings.Replace(valid, ",100,0,0,", ",0,0,0,", 1), "nan": strings.Replace(valid, "28.32", "NaN", 1), "unrequested": strings.Replace(valid, "09866", "00700", 1), "empty": `var hq_str_rt_hk09866="";`}
	for name, raw := range tests {
		t.Run(name, func(t *testing.T) {
			q := parseSina([]byte(raw), map[string]bool{"09866.HK": true}, now)
			if (len(q) == 1) != (name == "valid" || name == "stale") {
				t.Fatalf("unexpected accepted quotes: %v", q)
			}
		})
	}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Referer") == "" || r.URL.Query().Get("list") != "rt_hk09866" {
			t.Error("bad request")
		}
		w.Write([]byte(valid))
	}))
	defer srv.Close()
	q, err := fetchSina(context.Background(), srv.Client(), srv.URL, []string{"09866.HK"}, now)
	if err != nil || q["09866.HK"].Price != 28.32 {
		t.Fatalf("%v %v", q, err)
	}
}
func TestSinaRoutingAndSchedule(t *testing.T) {
	now := time.Date(2026, 9, 10, 10, 56, 40, 0, Zone)
	q := Quote{Symbol: "09866.HK", Price: 28, Observed: now, Received: now}
	f := q
	f.Price = 29
	f.Source = "sina_rt_hk"
	s := &Service{quotes: map[string]Quote{"09866.HK": q}, sinaQuotes: map[string]Quote{"09866.HK": f}}
	got, _ := s.componentQuote("09866.HK", now)
	if got.Price != 28 {
		t.Fatal("Sina overwrote internal")
	}
	delete(s.quotes, "09866.HK")
	got, ok := s.componentQuote("09866.HK", now)
	if !ok || got.Price != 29 {
		t.Fatal("fallback missing")
	}
	if _, ok = s.componentQuote("09866.HK", now.Add(241*time.Second)); ok {
		t.Fatal("accepted stale fallback")
	}
	s.cfg.SinaFallbackSymbols = []string{}
	if _, ok = s.componentQuote("09866.HK", now); ok {
		t.Fatal("disabled fallback used")
	}
	s.cfg.SinaFallbackSymbols = nil
	s.sinaQuotes["00700.HK"] = f
	if _, ok = s.componentQuote("00700.HK", now); ok {
		t.Fatal("unlisted fallback used")
	}
	for _, c := range []struct {
		h, m int
		want bool
	}{{9, 14, false}, {9, 15, true}, {12, 0, false}, {13, 0, true}, {16, 9, true}, {16, 10, false}} {
		if sinaPolling(time.Date(2026, 9, 10, c.h, c.m, 0, 0, Zone)) != c.want {
			t.Fatal(c)
		}
	}
	if sinaPolling(time.Date(2026, 9, 12, 10, 0, 0, 0, Zone)) {
		t.Fatal("weekend polling")
	}
}

func TestSinaStaleLastTradeAndDailyPlan(t *testing.T) {
	now := time.Date(2026, 9, 14, 10, 0, 0, 0, Zone)
	raw := `var hq_str_rt_hk01698="TME,TME,31,31,32,30,31.4,0,0,31,32,100000,1000,0,0,60,28,2026/09/14,09:50:00";`
	quotes, diag := parseSinaDetailed([]byte(raw), map[string]bool{"01698.HK": true, "09961.HK": true}, now)
	q := quotes["01698.HK"]
	if !sinaUsable(q, now) || !quoteFresh(q, now) || diag["01698.HK"].Status != "last_trade_stale" || diag["09961.HK"].Status != "missing_response" {
		t.Fatal(quotes, diag)
	}
	if sinaUsable(q, now.Add(241*time.Second)) {
		t.Fatal("old response accepted")
	}
	s := &Service{cfg: Config{DataDir: t.TempDir()}, activeDay: Day(now), started: now.Add(-time.Minute), feedAt: now, quotes: map[string]Quote{"00700.HK": {Price: 500, Observed: now, Received: now}}, sinaQuotes: quotes, errors: map[string]string{}, plan: iopv.Plan{Subscriptions: []string{"00700.HK", "01698.HK", "00853.HK"}}}
	s.freezeSinaPlan(now)
	if !s.sinaDaily.Frozen || !s.sinaAllowed("00853.HK") || s.sinaAllowed("00700.HK") {
		t.Fatal(s.sinaDaily)
	}
	s.plan.Subscriptions = append(s.plan.Subscriptions, "09999.HK")
	s.freezeSinaPlan(now.Add(time.Minute))
	if s.sinaAllowed("09999.HK") {
		t.Fatal("daily plan changed")
	}
	x := &Service{cfg: s.cfg, activeDay: Day(now)}
	x.loadSinaPlan(now)
	if !x.sinaDaily.Frozen || !x.sinaAllowed("01698.HK") {
		t.Fatal("restart lost plan")
	}
	got, ok := s.componentQuote("01698.HK", now)
	if !ok || got.Observed != q.Observed {
		t.Fatal("stale last trade lost")
	}
}
