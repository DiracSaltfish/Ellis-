package live

import (
	iopv "intranet-iopv"
	"strings"
	"time"
)

func sessionMinute(now time.Time) int {
	n := now.In(Zone)
	if n.Weekday() == time.Saturday || n.Weekday() == time.Sunday {
		return -1
	}
	return n.Hour()*60 + n.Minute()
}
func quoteWindow(now time.Time) bool       { m := sessionMinute(now); return m >= 555 && m < 970 }
func fxPolling(now time.Time) bool         { m := sessionMinute(now); return m >= 520 && m < 970 }
func calculationWindow(now time.Time) bool { return sessionMinute(now) >= 0 && iopv.ChartMinute(now) }
func subscriptionSymbols(plan []string, now time.Time) []string {
	out := []string{}
	m := sessionMinute(now)
	for _, s := range plan {
		end := 901
		if strings.HasSuffix(s, ".HK") {
			end = 970
		}
		if m >= 555 && m < end {
			out = append(out, s)
		}
	}
	return out
}

func (s *Service) scheduleNow() time.Time {
	if s.nowForSchedule != nil {
		return s.nowForSchedule()
	}
	return time.Now()
}
