package live

import (
	"encoding/json"
	"net/http/httptest"
	"testing"
)

func TestDailySharesExactDateAndMissing(t *testing.T) {
	st, err := OpenStore(t.TempDir() + "/iopv.sqlite")
	if err != nil {
		t.Fatal(err)
	}
	defer st.DB.Close()
	_, err = st.DB.Exec(`INSERT INTO daily_shares VALUES('159570.SZ','2026-09-10',1000,-12.34,'1navs','2026-09-10T23:00:00+08:00'),('159570.SZ','2026-09-11',1000,0,'1navs','2026-09-11T23:00:00+08:00')`)
	if err != nil {
		t.Fatal(err)
	}
	s := &Service{store: st}
	for _, tc := range []struct {
		date string
		want *float64
	}{{"2026-09-10", ptr(-12.34)}, {"2026-09-11", ptr(0)}, {"2026-09-09", nil}} {
		w := httptest.NewRecorder()
		s.dailySharesHandler(w, httptest.NewRequest("GET", "/api/v1/daily-shares?symbol=159570.SZ&date="+tc.date, nil))
		if w.Code != 200 {
			t.Fatal(w.Code, w.Body.String())
		}
		var v DailyShares
		if err = json.Unmarshal(w.Body.Bytes(), &v); err != nil {
			t.Fatal(err)
		}
		if v.Date != tc.date || (v.Change == nil) != (tc.want == nil) || (tc.want != nil && *v.Change != *tc.want) {
			t.Fatalf("unexpected %+v", v)
		}
	}
	for _, query := range []string{"symbol=invalid&date=2026-09-10", "symbol=159570.SZ&date=2026-02-30"} {
		w := httptest.NewRecorder()
		s.dailySharesHandler(w, httptest.NewRequest("GET", "/?"+query, nil))
		if w.Code != 400 {
			t.Fatal(w.Code)
		}
	}
}
