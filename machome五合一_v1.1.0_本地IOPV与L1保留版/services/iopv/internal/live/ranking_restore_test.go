package live

import (
	"path/filepath"
	"testing"
	"time"
)

func TestRankingRestoreSessionBoundary(t *testing.T) {
	for _, c := range []struct{ at, want string }{{"2026-09-16T11:29:59+08:00", ""}, {"2026-09-16T11:30:02+08:00", ""}, {"2026-09-16T12:30:00+08:00", "11:30:05"}, {"2026-09-16T13:00:00+08:00", ""}, {"2026-09-16T16:00:00+08:00", "15:00:05"}, {"2026-09-19T12:00:00+08:00", ""}} {
		n, _ := time.Parse(time.RFC3339, c.at)
		got, ok := rankingRestoreCutoff(n)
		if ok != (c.want != "") || ok && got.Format("15:04:05") != c.want {
			t.Fatal(c, got, ok)
		}
	}
}
func TestRestoreFXNeverReadsFuture(t *testing.T) {
	s, e := OpenStore(filepath.Join(t.TempDir(), "test.sqlite"))
	if e != nil {
		t.Fatal(e)
	}
	defer s.DB.Close()
	cut, _ := time.Parse(time.RFC3339, "2026-09-16T11:30:05+08:00")
	for _, c := range []struct{ at, v string }{{"2026-09-16T11:29:52+08:00", `{"value":1}`}, {"2026-09-16T11:30:06+08:00", `{"value":2}`}, {"2026-09-16T12:00:00+08:00", `{"value":3}`}} {
		if e = s.WriteFX(c.at, Day(cut), []byte(c.v)); e != nil {
			t.Fatal(e)
		}
	}
	raw, e := s.rankingFXAt(cut)
	if e != nil || string(raw) != `{"value":1}` {
		t.Fatal(string(raw), e)
	}
}
