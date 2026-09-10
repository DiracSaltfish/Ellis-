package live

import (
	iopv "intranet-iopv"
	"testing"
	"time"
)

func at(s string) time.Time {
	t, e := time.Parse(time.RFC3339, s)
	if e != nil {
		panic(e)
	}
	return t
}
func TestPCFWallClockSchedule(t *testing.T) {
	cases := []struct {
		now, last, complete string
		due                 bool
		next                string
	}{
		{"2026-09-10T08:39:59+08:00", "", "", false, "2026-09-10T08:40:00+08:00"},
		{"2026-09-10T08:40:00+08:00", "", "", true, "2026-09-10T08:40:00+08:00"},
		{"2026-09-10T08:44:59+08:00", "2026-09-10T08:40:00+08:00", "", false, "2026-09-10T08:45:00+08:00"},
		{"2026-09-10T08:45:00+08:00", "2026-09-10T08:40:00+08:00", "", true, "2026-09-10T08:45:00+08:00"},
		{"2026-09-10T10:00:00+08:00", "", "", true, "2026-09-10T10:00:00+08:00"},
		{"2026-09-10T18:00:00+08:00", "", "", false, "2026-09-11T08:40:00+08:00"},
		{"2026-09-10T18:01:00+08:00", "2026-09-10T18:00:00+08:00", "", false, "2026-09-11T08:40:00+08:00"},
		{"2026-09-11T10:00:00+08:00", "2026-09-11T08:40:00+08:00", "2026-09-11", false, "2026-09-14T08:40:00+08:00"},
		{"2026-09-12T10:00:00+08:00", "", "", false, "2026-09-14T08:40:00+08:00"},
		{"2026-09-14T00:40:00Z", "", "", true, "2026-09-14T08:40:00+08:00"},
	}
	for _, c := range cases {
		var last time.Time
		if c.last != "" {
			last = at(c.last)
		}
		now := at(c.now)
		if pcfDue(now, last, c.complete) != c.due || !nextPCFAttempt(now, last, c.complete).Equal(at(c.next)) {
			t.Errorf("%+v => %v", c, nextPCFAttempt(now, last, c.complete))
		}
	}
}
func TestRolloverClearsLiveAndPreservesArchive(t *testing.T) {
	st, e := OpenStore(t.TempDir() + "/iopv.sqlite")
	if e != nil {
		t.Fatal(e)
	}
	defer st.DB.Close()
	p := Point{Symbol: "513090.SH", Date: "2026-09-09", Minute: "16:08", At: at("2026-09-09T16:08:00+08:00")}
	s := &Service{store: st, activeDay: p.Date, baskets: map[string]iopv.Basket{"513090.SH": {Date: p.Date}}, plan: iopv.Plan{Subscriptions: []string{"00700.HK"}}, quotes: map[string]Quote{"00700.HK": {}}, latest: map[string]Point{p.Symbol: p}, fxRaw: []byte(`{}`), pending: map[string]map[string]Point{p.Date + " " + p.Minute: {p.Symbol: p}}, errors: map[string]string{"storage": "disk warning", "pcf:old": "old"}, restart: make(chan struct{}, 1), pcfCompleteDate: p.Date}
	now := at("2026-09-10T00:00:00+08:00")
	if !s.rollDay(now) || s.rollDay(now) {
		t.Fatal("rollover must happen exactly once")
	}
	if len(s.baskets)+len(s.plan.Subscriptions)+len(s.quotes)+len(s.latest)+len(s.fxRaw) != 0 || s.pcfCompleteDate != "" {
		t.Fatal("live state leaked")
	}
	if len(s.restart) != 1 || s.errors["storage"] != "disk warning" {
		t.Fatal("restart/error state")
	}
	s.flush(now, false)
	rows, e := st.History(p.Symbol, p.Date)
	if e != nil || len(rows) != 1 {
		t.Fatal("prior-day minute lost", e, rows)
	}
	rows, e = st.History(p.Symbol, "2026-09-10")
	if e != nil || len(rows) != 0 {
		t.Fatal("prior-day minute misdated")
	}
}
