package ratiofit

import (
	"math"
	"strings"
	"time"

	"newnavnav/internal/domain"
	"newnavnav/internal/snapshot"
)

type ClosePremiumPoint struct {
	Minute     string
	PremiumPct float64
}

type ClosePremiumLookup interface {
	LookupClosePremium(symbol string, targetDate string) (*ClosePremiumPoint, error)
}

type MinuteHistoryClosePremiumLookup struct {
	Store *snapshot.MinuteHistoryStore
}

func (l MinuteHistoryClosePremiumLookup) LookupClosePremium(symbol string, targetDate string) (*ClosePremiumPoint, error) {
	if l.Store == nil {
		return nil, nil
	}
	day := strings.ReplaceAll(strings.TrimSpace(targetDate), "-", "")
	if day == "" {
		return nil, nil
	}
	rows, err := l.Store.ReadDate(symbol, day)
	if err != nil {
		return nil, err
	}
	if len(rows) == 0 {
		return nil, nil
	}
	last := rows[len(rows)-1]
	if !isFinite(last.PremiumPct) {
		return nil, nil
	}
	return &ClosePremiumPoint{
		Minute:     closePremiumMinuteLabel(last.Minute),
		PremiumPct: round6(last.PremiumPct),
	}, nil
}

func ApplyClosePremium(rows []domain.EffectiveRatioFitHistoryRow, lookup ClosePremiumLookup) error {
	if lookup == nil {
		return nil
	}
	for index := range rows {
		point, err := lookup.LookupClosePremium(rows[index].Symbol, rows[index].TargetDate)
		if err != nil {
			return err
		}
		rows[index].ClosingRealtimeMinute = ""
		rows[index].ClosingRealtimePremiumPct = nil
		if point == nil {
			continue
		}
		rows[index].ClosingRealtimeMinute = strings.TrimSpace(point.Minute)
		rows[index].ClosingRealtimePremiumPct = ptrFloat(round6(point.PremiumPct))
	}
	return nil
}

func closePremiumMinuteLabel(value string) string {
	value = strings.TrimSpace(value)
	if value == "" {
		return ""
	}
	loc := shanghaiLocation()
	for _, pattern := range []string{
		"2006-01-02 15:04",
		"2006-01-02 15:04:05",
		"2006-01-02T15:04",
		"2006-01-02T15:04:05",
		"200601021504",
		"20060102150405",
	} {
		if parsed, err := time.ParseInLocation(pattern, value, loc); err == nil {
			return parsed.In(loc).Format("15:04")
		}
	}
	if parsed, err := time.Parse(time.RFC3339Nano, value); err == nil {
		return parsed.In(loc).Format("15:04")
	}
	if len(value) >= 5 {
		tail := value[len(value)-5:]
		if len(tail) == 5 && tail[2] == ':' {
			return tail
		}
	}
	return value
}

func isFinite(value float64) bool {
	return !math.IsNaN(value) && !math.IsInf(value, 0)
}

func shanghaiLocation() *time.Location {
	loc, err := time.LoadLocation("Asia/Shanghai")
	if err != nil {
		return time.FixedZone("Asia/Shanghai", 8*60*60)
	}
	return loc
}
