package hkconnectfx

import (
	"encoding/json"
	"os"
	"path/filepath"
	"time"
)

// This cache retains the original observation/receipt timestamps across restarts.
// It is never marked live after restore; only a successful current poll can do so.
func loadLastHealthyFX(dataDir string) map[string]FXQuote {
	result := make(map[string]FXQuote)
	raw, err := os.ReadFile(filepath.Join(dataDir, "cfets-last-healthy.json"))
	if err != nil {
		return result
	}
	var stored map[string]FXQuote
	if json.Unmarshal(raw, &stored) != nil {
		return result
	}
	for pair, q := range stored {
		if _, ok := initialCFETSSpotQuotes()[pair]; !ok {
			continue
		}
		if q.Pair != pair || q.Source != "CFETS_CHINAMONEY" || q.Bid == nil || q.Ask == nil ||
			!validRate(*q.Bid) || !validRate(*q.Ask) || *q.Ask < *q.Bid ||
			q.ObservedAt == nil || q.ReceivedAt == nil || q.ObservedAt.IsZero() || q.ReceivedAt.IsZero() {
			continue
		}
		result[pair] = cloneFX(q)
	}
	return result
}

func restoredFXQuotes(dataDir string) map[string]FXQuote {
	result := initialCFETSSpotQuotes()
	for pair, q := range loadLastHealthyFX(dataDir) {
		q.Healthy, q.Error = false, "restored prior CFETS observation; awaiting current poll"
		result[pair] = q
	}
	return result
}

func freshFXQuotes(values map[string]FXQuote, now time.Time) map[string]FXQuote {
	result := cloneFXQuotes(values)
	for pair, q := range result {
		if q.Healthy && (q.ObservedAt == nil || now.Sub(*q.ObservedAt) > cfetsQuoteFreshness ||
			q.ObservedAt.Sub(now) > 30*time.Second || q.ObservedAt.In(shanghai).Format("2006-01-02") != now.In(shanghai).Format("2006-01-02")) {
			q.Healthy, q.Error = false, "stale CFETS source observation"
			result[pair] = q
		}
	}
	return result
}

func (s *Service) persistLastHealthyFX(quotes map[string]FXQuote) error {
	if len(quotes) == 0 {
		return nil
	}
	raw, err := json.Marshal(quotes)
	if err != nil {
		return err
	}
	if err = os.MkdirAll(s.dataDir, 0755); err != nil {
		return err
	}
	f, err := os.CreateTemp(s.dataDir, ".cfets-last-healthy-*")
	if err != nil {
		return err
	}
	defer os.Remove(f.Name())
	if _, err = f.Write(raw); err != nil {
		f.Close()
		return err
	}
	if err = f.Sync(); err != nil {
		f.Close()
		return err
	}
	if err = f.Close(); err != nil {
		return err
	}
	return os.Rename(f.Name(), filepath.Join(s.dataDir, "cfets-last-healthy.json"))
}
