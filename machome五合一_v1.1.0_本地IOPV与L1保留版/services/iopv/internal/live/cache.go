package live

import iopv "intranet-iopv"

// Restore only today's validated baskets without making upstream requests.
// This does not mark today's scheduled refresh complete.
func (s *Service) loadCachedPCF() error {
	bs := []iopv.Basket{}
	for _, c := range s.candidates {
		var raw []byte
		if err := s.store.DB.QueryRow("SELECT raw FROM pcf WHERE symbol=? AND trade_date=? ORDER BY fetched_at DESC LIMIT 1", c.Symbol, s.activeDay).Scan(&raw); err != nil {
			continue
		}
		raw, err := decodeArchive(raw)
		if err != nil {
			return err
		}
		b, err := iopv.ParsePCF(raw, c.Symbol, s.activeDay)
		if err != nil {
			continue
		}
		s.baskets[c.Symbol] = b
		bs = append(bs, b)
	}
	if len(bs) == 0 {
		return nil
	}
	plan, err := iopv.BuildPlan(bs)
	if err != nil {
		return err
	}
	s.plan = plan
	return nil
}
