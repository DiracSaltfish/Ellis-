package sharesync

import (
	"strings"

	"newnavnav/internal/domain"
)

type ClosePremiumLookup interface {
	LookupClosePremium(symbol string, shareDate string) (*float64, error)
}

type ClosePremiumLookupFunc func(symbol string, shareDate string) (*float64, error)

func (fn ClosePremiumLookupFunc) LookupClosePremium(symbol string, shareDate string) (*float64, error) {
	if fn == nil {
		return nil, nil
	}
	return fn(symbol, shareDate)
}

func ApplyClosePremium(rows []domain.ShareHistoryRecord, lookup ClosePremiumLookup) error {
	if lookup == nil || len(rows) == 0 {
		return nil
	}
	cache := make(map[string]*float64, len(rows))
	for index := range rows {
		if rows[index].ClosePremiumPct != nil {
			continue
		}
		key := strings.ToUpper(strings.TrimSpace(rows[index].Symbol)) + "|" + strings.TrimSpace(rows[index].ShareDate)
		if cached, ok := cache[key]; ok {
			rows[index].ClosePremiumPct = cloneFloatPointer(cached)
			continue
		}
		value, err := lookup.LookupClosePremium(rows[index].Symbol, rows[index].ShareDate)
		if err != nil {
			return err
		}
		cache[key] = cloneFloatPointer(value)
		rows[index].ClosePremiumPct = cloneFloatPointer(value)
	}
	return nil
}

func cloneFloatPointer(value *float64) *float64 {
	if value == nil {
		return nil
	}
	copy := *value
	return &copy
}
