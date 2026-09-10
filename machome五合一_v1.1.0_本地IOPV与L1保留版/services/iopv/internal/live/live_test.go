package live

import (
	"encoding/json"
	iopv "intranet-iopv"
	"math"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func frame(tag string, delta int, fields map[string]any) []byte {
	b, _ := json.Marshal(map[string]any{"tag": tag, "delta": delta, "data": fields})
	return b
}
func full(tag string) map[string]any {
	n := 21
	if tag == "16" {
		n = 23
	}
	d := map[string]any{}
	for i := 1; i <= n; i++ {
		d[fmtInt(i)] = 0
	}
	d["1"] = 102
	d["2"] = "159217"
	d["4"] = int64(20260909103000000)
	d["5"] = "T"
	d["10"] = 1850000
	if tag == "16" {
		d["2"] = "00700"
		d["3"] = int64(20260909103000000)
		d["4"] = "T"
		d["11"] = 436200000
	}
	d["12"] = "1850000|1849000|1848000|1847000|1846000"
	d["14"] = "1851000|1852000|1853000|1854000|1855000"
	d["13"] = "10000|20000|30000|40000|50000"
	d["15"] = d["13"]
	return d
}
func fmtInt(i int) string {
	const digits = "0123456789"
	if i < 10 {
		return string(digits[i])
	}
	return string([]byte{digits[i/10], digits[i%10]})
}
func TestDecoder(t *testing.T) {
	now, _ := time.Parse(time.RFC3339, "2026-09-09T10:30:01+08:00")
	for _, tag := range []string{"14", "16"} {
		t.Run(tag, func(t *testing.T) {
			d := NewDecoder()
			f := full(tag)
			q, e := d.Decode(frame(tag, 0, f), now, 1)
			if e != nil {
				t.Fatal(e)
			}
			want := 1.85
			if tag == "16" {
				want = 436.2
			}
			if q.Price != want || q.Book.Bids[0].Volume != 100 {
				t.Fatal(q)
			}
			pk, tk := "10", "4"
			if tag == "16" {
				pk, tk = "11", "3"
			}
			delta := map[string]any{"1": 102, "2": f["2"], tk: int64(20260909103003000), pk: 2000000}
			q, e = d.Decode(frame(tag, 1, delta), now.Add(3*time.Second), 1)
			if e != nil || q.Price != 2 || q.Book.Bids[0].Price != 1.85 {
				t.Fatal(q, e)
			}
			delta[tk] = int64(20260909103002000)
			if _, e = d.Decode(frame(tag, 1, delta), now, 1); e == nil {
				t.Fatal("out-of-order accepted")
			}
			if _, e = NewDecoder().Decode(frame(tag, 1, delta), now, 2); e == nil {
				t.Fatal("delta accepted without session full")
			}
		})
	}
}
func TestStoreDurableIdempotent(t *testing.T) {
	path := filepath.Join(t.TempDir(), "test.db")
	s, e := OpenStore(path)
	if e != nil {
		t.Fatal(e)
	}
	now := time.Now()
	p := Point{Symbol: "513090.SH", Date: "2026-09-09", Minute: "10:30", At: now, Mid: ptr(1.85), Buy: ptr(1.83), RunID: "one"}
	if e = s.Write([]Point{p, p}); e != nil {
		t.Fatal(e)
	}
	p.At = now.Add(-time.Second)
	p.Mid = ptr(9)
	s.Write([]Point{p})
	r, e := s.History(p.Symbol, p.Date)
	if e != nil || len(r) != 1 || *r[0].Mid != 1.85 {
		t.Fatal(r, e)
	}
	s.DB.Close()
	s, e = OpenStore(path)
	if e != nil {
		t.Fatal(e)
	}
	defer s.DB.Close()
	r, e = s.History(p.Symbol, p.Date)
	if e != nil || len(r) != 1 || r[0].RunID != "one" {
		t.Fatal(r, e)
	}
	if e = s.Check(); e != nil {
		t.Fatal(e)
	}
}
func testService(t *testing.T) *Service {
	t.Helper()
	dir := t.TempDir()
	u := filepath.Join(dir, "universe.json")
	os.WriteFile(u, []byte(`{"candidates":[{"symbol":"513090.SH","name":"Test"},{"symbol":"159217.SZ","name":"SZ"}]}`), 0600)
	s, e := New(Config{DataDir: dir, Universe: u})
	if e != nil {
		t.Fatal(e)
	}
	t.Cleanup(func() { s.store.DB.Close() })
	return s
}
func TestChannelAndIndependentFX(t *testing.T) {
	if Channel("159217.SZ") != "shenzhen" || Channel("513090.SH") != "shanghai" {
		t.Fatal("mapping")
	}
	s := testService(t)
	now, _ := time.Parse(time.RFC3339, "2026-09-09T10:30:00+08:00")
	s.baskets["513090.SH"] = iopv.Basket{Symbol: "513090.SH", Date: Day(now), Unit: 100, Cash: 1, Components: []iopv.Component{{Symbol: "00700.HK", Quantity: 10}}}
	s.plan.Groups[0] = []string{"513090.SH"}
	s.quotes["00700.HK"] = Quote{Price: 20, Observed: now, Received: now}
	s.quotes["513090.SH"] = Quote{Price: 1.8, Observed: now, Received: now}
	s.feedAt = now
	s.fxRaw = []byte(`{"schema_version":"hk-connect-fx.v7","trade_date":"2026-09-09","central_parity":{"trade_date":"2026-09-09","pair":"HKD/CNY","rate":0.86}}`)
	s.calculateGroup(now, 0)
	p := s.latest["513090.SH"]
	if p.Mid == nil || *p.Mid != 1.73 || p.Buy != nil || p.Eligible {
		t.Fatal(p)
	}
	s.flush(now.Add(time.Minute), false)
	r, e := s.store.History(p.Symbol, p.Date)
	if e != nil || len(r) != 1 || r[0].Mid == nil {
		t.Fatal(r, e)
	}
	s.quotes = map[string]Quote{}
	s.calculateGroup(now, 0)
	if s.latest[p.Symbol].Mid != nil {
		t.Fatal("partial basket produced NAV")
	}
}
func TestHTTPAndManagement(t *testing.T) {
	s := testService(t)
	h := s.Handler(http.NotFoundHandler())
	for _, path := range []string{"/api/v1/health", "/api/v1/snapshots", "/api/v1/minutes?symbol=513090.SH&date=2026-09-09", "/api/v1/dates?symbol=513090.SH", "/api/v1/export.csv?symbol=513090.SH&date=2026-09-09"} {
		r := httptest.NewRequest("GET", path, nil)
		w := httptest.NewRecorder()
		h.ServeHTTP(w, r)
		if w.Code != 200 {
			t.Fatal(path, w.Code)
		}
	}
	for _, tc := range []struct {
		remote, origin, header string
		want                   int
	}{{"192.168.1.5:3456", "", "1", 403}, {"127.0.0.1:1234", "", "", 403}, {"127.0.0.1:1234", "http://evil.test", "1", 403}, {"127.0.0.1:1234", "", "1", 200}} {
		r := httptest.NewRequest("POST", "/api/v1/manage/pause", nil)
		r.RemoteAddr = tc.remote
		r.Header.Set("Origin", tc.origin)
		r.Header.Set("X-IOPV-Manage", tc.header)
		w := httptest.NewRecorder()
		h.ServeHTTP(w, r)
		if w.Code != tc.want {
			t.Fatal(tc, w.Code)
		}
	}
	if !s.paused {
		t.Fatal("pause did not apply")
	}
	r := httptest.NewRequest("GET", "/api/v1/minutes?date=garbage", nil)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, r)
	if w.Code != 400 {
		t.Fatal("bad date")
	}
}
func TestSnapshotExpiry(t *testing.T) {
	s := testService(t)
	s.latest["513090.SH"] = Point{Symbol: "513090.SH", Date: Day(time.Now()), At: time.Now().Add(-20 * time.Second), ETF: ptr(1), Mid: ptr(1), MidPremium: ptr(0), Eligible: true}
	r := s.Snapshots()
	for _, p := range r {
		if p.Symbol == "513090.SH" && (p.Eligible || p.ETF != nil || p.MidPremium != nil || !strings.Contains(strings.Join(p.Reasons, ","), "NAV_STALE")) {
			t.Fatal(p)
		}
	}
}
func TestClosedFXWindow(t *testing.T) {
	now, _ := time.Parse(time.RFC3339, "2026-09-09T16:08:00+08:00")
	raw := []byte(`{"schema_version":"hk-connect-fx.v7","trade_date":"2026-09-09","generated_at":"2026-09-09T16:08:00+08:00","status":"closed","central_parity":{"trade_date":"2026-09-09","pair":"HKD/CNY","rate":0.86418},"fx":{"pair":"HKD/CNY","observed_at":"2026-09-09T15:59:57+08:00","healthy":true},"estimate":{"predicted_buy_settlement":0.855,"predicted_sell_settlement":0.856},"model":{"version":"test"}}`)
	f, e := SessionFX(raw, Day(now), "shanghai", "buy_hk", now)
	if e != nil || f.Settlement != .856 || f.Actionable || f.Status != "closed_reference" {
		t.Fatal(f, e)
	}
	if _, e = SessionFX(raw, Day(now), "shanghai", "buy_hk", now.Add(time.Minute)); e == nil {
		t.Fatal("beyond close window accepted")
	}
	raw = []byte(strings.Replace(string(raw), "15:59:57", "14:59:57", 1))
	if _, e = SessionFX(raw, Day(now), "shanghai", "buy_hk", now); e == nil {
		t.Fatal("old FX accepted")
	}
}
func TestRepriceSingleBackend(t *testing.T) {
	now := time.Now()
	now = time.Date(now.Year(), now.Month(), now.Day(), 10, 30, 0, 0, Zone)
	p := Point{Date: Day(now), Mid: ptr(2), Buy: ptr(1.9), Sell: ptr(1.91)}
	q := Quote{Symbol: "513090.SH", Price: 2.1, Observed: now, Received: now}
	q.Book.ObservedAt = now
	for i := 0; i < 5; i++ {
		q.Book.Bids[i] = iopv.Level{Price: 2.09, Volume: 100}
		q.Book.Asks[i] = iopv.Level{Price: 2.1, Volume: 200}
	}
	reprice(&p, q, now, true)
	if p.ETF == nil || len(p.BookValues) != 10 || p.BookValues[0].Mid == nil {
		t.Fatal(p)
	}
	want := (2.09/2 - 1) * 100
	if math.Abs(*p.BookValues[0].Mid-want) > 1e-12 {
		t.Fatal("book calculation")
	}
	reprice(&p, q, now, false)
	if p.ETF != nil || p.MidPremium != nil || p.BookValues[0].Mid != nil {
		t.Fatal("expired NAV repriced")
	}
}
func TestClosingMinutePreserved(t *testing.T) {
	at := time.Date(2026, 9, 9, 15, 0, 0, 0, Zone)
	p := Point{Date: Day(at), Mid: ptr(2), Buy: ptr(1.9), Eligible: true}
	q := Quote{Symbol: "513090.SH", Price: 2.1, Observed: at, Received: at}
	reprice(&p, q, at.Add(55*time.Second), true)
	if p.ETF == nil || p.MidPremium == nil || p.Eligible {
		t.Fatal("closing minute lost or made actionable", p)
	}
	reprice(&p, q, at.Add(time.Minute), true)
	if p.ETF != nil || p.MidPremium != nil {
		t.Fatal("postclose premium generated")
	}
}
