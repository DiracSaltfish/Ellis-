package live

import (
	"encoding/json"
	"math"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestCompactHistory(t *testing.T) {
	s, e := OpenStore(filepath.Join(t.TempDir(), "db"))
	if e != nil {
		t.Fatal(e)
	}
	defer s.DB.Close()
	p := Point{Symbol: "513090.SH", Date: "2026-09-09", Minute: "15:48", At: time.Date(2026, 9, 9, 15, 48, 1, 0, Zone), ETF: ptr(.980), Mid: ptr(.98519677), MidPremium: ptr(-.527), Reasons: []string{"not_live_signal"}, Mode: "historical_reconstruction"}
	b, _ := json.Marshal(p)
	if _, e = s.DB.Exec("INSERT INTO minutes VALUES(?,?,?,?)", p.Symbol, p.Date, p.Minute, b); e != nil {
		t.Fatal(e)
	}
	raw, e := os.ReadFile("../../testdata/pcf/159217.xml")
	if e != nil {
		t.Fatal(e)
	}
	if _, e = s.DB.Exec("INSERT INTO pcf VALUES(?,?,?,?,?)", "159217.SZ", "2026-09-08", "h", raw, "t"); e != nil {
		t.Fatal(e)
	}
	for i := 0; i < 2; i++ {
		if _, e = s.CompactLegacy(); e != nil {
			t.Fatal(e)
		}
	}
	out, e := s.History(p.Symbol, p.Date)
	if e != nil || len(out) != 1 {
		t.Fatal(e, len(out))
	}
	q := out[0]
	if *q.ETF != .98 || *q.Mid != .9852 || q.Buy != nil || q.BuyPremium != nil || q.Book != nil || q.Reasons[0] != "not_live_signal" {
		t.Fatal(q)
	}
	if math.Abs(*q.MidPremium-(.98/.9852-1)*100) > 1e-10 {
		t.Fatal(q.MidPremium)
	}
	if *p.Mid != .98519677 {
		t.Fatal("live input mutated")
	}
	older := p
	older.At = older.At.Add(-time.Second)
	older.Mid = ptr(9)
	if e = s.Write([]Point{older}); e != nil {
		t.Fatal(e)
	}
	out, _ = s.History(p.Symbol, p.Date)
	if *out[0].Mid != .9852 {
		t.Fatal("older overwrite")
	}
	newer := p
	newer.At = newer.At.Add(time.Second)
	newer.Mid = ptr(1.23456)
	newer.MidPremium = nil
	if e = s.Write([]Point{newer}); e != nil {
		t.Fatal(e)
	}
	out, _ = s.History(p.Symbol, p.Date)
	if *out[0].Mid != 1.2346 || out[0].MidPremium != nil {
		t.Fatal("newer/null")
	}
	var stored []byte
	if e = s.DB.QueryRow("SELECT raw FROM pcf").Scan(&stored); e != nil {
		t.Fatal(e)
	}
	decoded, e := decodeArchive(stored)
	if e != nil || string(decoded) != string(raw) {
		t.Fatal("pcf roundtrip", e)
	}
	if _, e = decodeArchive([]byte("IOPZ1invalid")); e == nil {
		t.Fatal("corruption accepted")
	}
	dates, e := s.Dates(p.Symbol)
	if e != nil || len(dates) != 1 {
		t.Fatal(dates, e)
	}
}
func TestFXBlocksLossless(t *testing.T) {
	s, e := OpenStore(filepath.Join(t.TempDir(), "fxdb"))
	if e != nil {
		t.Fatal(e)
	}
	defer s.DB.Close()
	for _, at := range []string{"2026-09-09T10:00:00+08:00", "2026-09-09T10:00:15+08:00", "2026-09-09T11:00:00+08:00"} {
		raw := []byte(`{"at":"` + at + `","invalid":true}`)
		if _, e = s.DB.Exec("INSERT INTO fx VALUES(?,?,?)", at, "2026-09-09", raw); e != nil {
			t.Fatal(e)
		}
	}
	if _, e = s.CompactLegacy(); e != nil {
		t.Fatal(e)
	}
	var count int
	if e = s.DB.QueryRow("SELECT sum(point_count) FROM fx_blocks").Scan(&count); e != nil || count != 3 {
		t.Fatal(count, e)
	}
	if e = s.WriteFX("2026-09-09T10:00:15+08:00", "2026-09-09", []byte("duplicate")); e != nil {
		t.Fatal(e)
	}
	if e = s.WriteFX("2026-09-09T10:00:30+08:00", "2026-09-09", []byte("new")); e != nil {
		t.Fatal(e)
	}
	var raw []byte
	s.DB.QueryRow("SELECT payload FROM fx_blocks WHERE bucket=?", "2026-09-09T10").Scan(&raw)
	raw, e = decodeArchive(raw)
	if e != nil {
		t.Fatal(e)
	}
	var m map[string][]byte
	if e = json.Unmarshal(raw, &m); e != nil {
		t.Fatal(e)
	}
	if len(m) != 3 || string(m["2026-09-09T10:00:15+08:00"]) == "duplicate" {
		t.Fatal(m)
	}
}
