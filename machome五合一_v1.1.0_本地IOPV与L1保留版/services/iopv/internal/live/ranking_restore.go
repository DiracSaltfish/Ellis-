package live

import (
	"encoding/json"
	"fmt"
	iopv "intranet-iopv"
	"time"
)

// Restore display only, from the last shared trading session. No extra daily
// snapshots are written, and neither post-cutoff FX nor lunch quotes are used.
func rankingRestoreCutoff(now time.Time) (time.Time, bool) {
	n := now.In(Zone)
	if n.Weekday() == time.Saturday || n.Weekday() == time.Sunday {
		return time.Time{}, false
	}
	h, m := 0, 0
	minute := n.Hour()*60 + n.Minute()
	if minute >= 690 && minute < 780 {
		h, m = 11, 30
	} else if minute >= 900 {
		h, m = 15, 0
	} else {
		return time.Time{}, false
	}
	t := time.Date(n.Year(), n.Month(), n.Day(), h, m, 5, 0, Zone)
	return t, !t.After(now)
}
func (s *Store) rankingFXAt(cut time.Time) ([]byte, error) {
	var latest time.Time
	var result []byte
	consider := func(at string, raw []byte) {
		t, e := time.Parse(time.RFC3339Nano, at)
		if e == nil && !t.After(cut) && t.After(latest) && Day(t) == Day(cut) {
			latest = t
			result = append([]byte(nil), raw...)
		}
	}
	rows, e := s.DB.Query("SELECT payload FROM fx_blocks WHERE trade_date=?", Day(cut))
	if e != nil {
		return nil, e
	}
	for rows.Next() {
		var raw []byte
		if e = rows.Scan(&raw); e != nil {
			break
		}
		raw, e = decodeArchive(raw)
		if e != nil {
			break
		}
		var block map[string][]byte
		if e = json.Unmarshal(raw, &block); e != nil {
			break
		}
		for at, v := range block {
			consider(at, v)
		}
	}
	re := rows.Err()
	rows.Close()
	if e != nil {
		return nil, e
	}
	if re != nil {
		return nil, re
	}
	rows, e = s.DB.Query("SELECT at,payload FROM fx WHERE trade_date=?", Day(cut))
	if e != nil {
		return nil, e
	}
	for rows.Next() {
		var at string
		var raw []byte
		if e = rows.Scan(&at, &raw); e != nil {
			break
		}
		raw, e = decodeArchive(raw)
		if e != nil {
			break
		}
		consider(at, raw)
	}
	re = rows.Err()
	rows.Close()
	if e != nil {
		return nil, e
	}
	if re != nil {
		return nil, re
	}
	if len(result) == 0 {
		return nil, fmt.Errorf("no archived FX before ranking cutoff")
	}
	return result, nil
}
func (s *Service) restoreSessionRanking(now time.Time) error {
	cut, ok := rankingRestoreCutoff(now)
	if !ok {
		return nil
	}
	s.ranking.RLock()
	present := s.ranking.value.Date == Day(now) && len(s.ranking.value.Rows) > 0
	s.ranking.RUnlock()
	if present {
		return nil
	}
	raw, e := s.store.rankingFXAt(cut)
	if e != nil {
		return e
	}
	s.mu.RLock()
	cs := append([]Candidate(nil), s.candidates...)
	paused := s.paused
	s.mu.RUnlock()
	// Use the exact PCF referenced by each fund's latest recorded pre-cutoff point.
	bs := map[string]iopv.Basket{}
	for _, c := range cs {
		ps, err := s.store.History(c.Symbol, Day(cut))
		if err != nil {
			return err
		}
		var last Point
		for _, p := range ps {
			if rankMinute(p.Minute) && p.Minute < cut.Format("15:04") && p.At.Before(cut) && p.At.After(last.At) {
				last = p
			}
		}
		if last.Hash == "" {
			continue
		}
		var pcf []byte
		if err = s.store.DB.QueryRow("SELECT raw FROM pcf WHERE symbol=? AND trade_date=? AND hash=?", c.Symbol, Day(cut), last.Hash).Scan(&pcf); err != nil {
			continue
		}
		pcf, err = decodeArchive(pcf)
		if err != nil {
			continue
		}
		b, err := iopv.ParsePCF(pcf, c.Symbol, Day(cut))
		if err == nil && b.Hash == last.Hash {
			bs[c.Symbol] = b
		}
	}
	view := &Service{store: s.store, candidates: cs, baskets: bs, fxRaw: raw, paused: paused}
	v := view.computeRanking(cut)
	v.Status += "；共同交易时段截止重算（午休/收盘恢复展示，非当前实时行情）"
	has := false
	for _, r := range v.Rows {
		has = has || r.Minutes > 0
	}
	if !has {
		return fmt.Errorf("no pre-cutoff minutes")
	}
	s.ranking.Lock()
	defer s.ranking.Unlock()
	// A concurrent live calculation always takes precedence.
	if s.ranking.value.Date != Day(now) || len(s.ranking.value.Rows) == 0 {
		s.ranking.value = v
	}
	return nil
}
