package live

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"sort"
	"time"
)

// Only historical rows enter feature storage. Never consume T-day net flow as an input.
func (s *Service) syncModelShares(ctx context.Context, symbol, date string) error {
	u := "https://1navs.com/api/v1/funds/" + symbol[7:] + symbol[:6] + "/share-history"
	req, _ := http.NewRequestWithContext(ctx, "GET", u, nil)
	req.Header.Set("User-Agent", "Mozilla/5.0 MachomeIOPV/1.1")
	resp, e := (&http.Client{Timeout: 15 * time.Second}).Do(req)
	if e != nil {
		return fmt.Errorf("share history unavailable")
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return fmt.Errorf("share history HTTP %d", resp.StatusCode)
	}
	var v struct {
		Symbol string `json:"symbol"`
		Rows   []struct {
			Date    string   `json:"share_date"`
			Shares  *float64 `json:"shares_10k"`
			Change  *float64 `json:"share_change_10k"`
			Updated string   `json:"updated_at"`
		} `json:"rows"`
	}
	if e = json.NewDecoder(io.LimitReader(resp.Body, 4<<20)).Decode(&v); e != nil {
		return e
	}
	if v.Symbol != symbol[7:]+symbol[:6] {
		return fmt.Errorf("share history symbol mismatch")
	}
	sort.Slice(v.Rows, func(i, j int) bool { return v.Rows[i].Date > v.Rows[j].Date })
	n := 0
	latest := ""
	for _, r := range v.Rows {
		if n >= 5 {
			break
		}
		if _, e := time.Parse("2006-01-02", r.Date); e != nil || r.Date >= date || !positive(r.Shares) || r.Change == nil {
			continue
		}
		if _, e = s.store.DB.Exec("INSERT INTO daily_shares VALUES(?,?,?,?,?,?) ON CONFLICT(symbol,trade_date) DO UPDATE SET shares_10k=excluded.shares_10k,share_change_10k=excluded.share_change_10k,source=excluded.source,source_updated_at=excluded.source_updated_at", symbol, r.Date, r.Shares, r.Change, u, r.Updated); e != nil {
			return e
		}
		n++
		if r.Date > latest {
			latest = r.Date
		}
	}
	s.mu.RLock()
	b := s.baskets[symbol]
	s.mu.RUnlock()
	if b.Date == date && b.PrevDate != "" && latest != b.PrevDate {
		return fmt.Errorf("share history is not through PCF previous date")
	}
	if n < 5 {
		return fmt.Errorf("fewer than five historical share rows")
	}
	return nil
}
func (s *Service) modelSharesLoop(ctx context.Context) {
	tick := time.NewTicker(time.Minute)
	defer tick.Stop()
	done := map[string]string{}
	for {
		now := time.Now()
		m := sessionMinute(now)
		if m >= 515 && m <= 900 {
			s.mu.RLock()
			cs := append([]Candidate(nil), s.candidates...)
			s.mu.RUnlock()
			for _, c := range cs {
				if c.Symbol[0] != '5' || done[c.Symbol] == Day(now) {
					continue
				}
				e := s.syncModelShares(ctx, c.Symbol, Day(now))
				s.setError("model_shares:"+c.Symbol, e)
				if e == nil {
					done[c.Symbol] = Day(now)
				}
				if ctx.Err() != nil {
					return
				}
			}
		}
		select {
		case <-ctx.Done():
			return
		case <-tick.C:
		}
	}
}
