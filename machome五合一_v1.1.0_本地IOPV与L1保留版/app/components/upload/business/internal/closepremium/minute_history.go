package closepremium

import (
	"strings"

	"newnavnav/internal/sharesync"
	"newnavnav/internal/snapshot"
)

func ShareHistoryLookup(store *snapshot.MinuteHistoryStore) sharesync.ClosePremiumLookup {
	return sharesync.ClosePremiumLookupFunc(func(symbol string, shareDate string) (*float64, error) {
		if store == nil {
			return nil, nil
		}
		day := strings.ReplaceAll(strings.TrimSpace(shareDate), "-", "")
		if day == "" {
			return nil, nil
		}
		rows, err := store.ReadDate(symbol, day)
		if err != nil {
			return nil, err
		}
		if len(rows) == 0 {
			return nil, nil
		}
		value := rows[len(rows)-1].PremiumPct
		return &value, nil
	})
}
