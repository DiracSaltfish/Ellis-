package live

import (
	iopv "intranet-iopv"
	"time"
)

// Fixed Beijing wall-clock schedule, independent of process startup time.
func nextPCFAttempt(now, last time.Time, complete string) time.Time {
	local := now.In(Zone)
	target := time.Date(local.Year(), local.Month(), local.Day(), 8, 40, 0, 0, Zone)
	weekday := local.Weekday() != time.Saturday && local.Weekday() != time.Sunday
	if weekday && complete != Day(now) {
		if local.Before(target) {
			return target
		}
		end := time.Date(local.Year(), local.Month(), local.Day(), 16, 8, 0, 0, Zone)
		// Catch up only within the trading-day acquisition window.
		if Day(last) != Day(now) && !local.After(end) {
			return now
		}
		retry := last.Add(5 * time.Minute)
		if !local.After(end) && !retry.After(end) {
			return retry
		}
	}
	for {
		target = target.AddDate(0, 0, 1)
		if target.Weekday() != time.Saturday && target.Weekday() != time.Sunday {
			return target
		}
	}
}
func pcfDue(now, last time.Time, complete string) bool {
	return !now.Before(nextPCFAttempt(now, last, complete))
}

// Keep pending/archive rows in their original date partitions; clear only live state.
func (s *Service) rollDay(now time.Time) bool {
	date := Day(now)
	s.mu.Lock()
	if s.activeDay == date {
		s.mu.Unlock()
		return false
	}
	old := s.activeDay
	s.activeDay = date
	s.baskets = map[string]iopv.Basket{}
	s.plan = iopv.Plan{}
	s.latest = map[string]Point{}
	s.quotes = map[string]Quote{}
	s.sinaQuotes = nil
	s.sinaLastAttempt = time.Time{}
	s.fxRaw = nil
	s.feedAt = time.Time{}
	s.feedState = "waiting_pcf"
	s.pcfLastAttempt = time.Time{}
	s.pcfCompleteDate = ""
	// Keep storage errors visible; day-scoped acquisition errors do not carry forward.
	for k := range s.errors {
		if k != "storage" && k != "fx_storage" {
			delete(s.errors, k)
		}
	}
	s.mu.Unlock()
	select {
	case s.restart <- struct{}{}:
	default:
	}
	if s.store != nil {
		s.store.Event("day_rollover", old+" -> "+date)
	}
	return true
}
