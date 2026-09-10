package live

import (
	iopv "intranet-iopv"
	"os"
	"path/filepath"
	"testing"
)

func TestCachedPCFRestoresOnlyMatchingDate(t *testing.T) {
	st, e := OpenStore(filepath.Join(t.TempDir(), "archive.sqlite"))
	if e != nil {
		t.Fatal(e)
	}
	defer st.DB.Close()
	raw, e := os.ReadFile("../../testdata/pcf/159217.xml")
	if e != nil {
		t.Fatal(e)
	}
	for _, date := range []string{"2026-09-08", "2026-09-09"} {
		if _, e = st.DB.Exec("INSERT INTO pcf VALUES(?,?,?,?,?)", "159217.SZ", date, date, raw, "2026-09-08T08:40:00+08:00"); e != nil {
			t.Fatal(e)
		}
	}
	for _, date := range []string{"2026-09-08", "2026-09-09"} {
		s := &Service{store: st, activeDay: date, candidates: []Candidate{{Symbol: "159217.SZ"}}, baskets: map[string]iopv.Basket{}}
		if e = s.loadCachedPCF(); e != nil {
			t.Fatal(e)
		}
		if (len(s.baskets) == 1) != (date == "2026-09-08") {
			t.Fatalf("wrong date admitted: %s", date)
		}
		if s.pcfCompleteDate != "" || !s.pcfLastAttempt.IsZero() {
			t.Fatal("cache skipped scheduled upstream refresh")
		}
	}
}
