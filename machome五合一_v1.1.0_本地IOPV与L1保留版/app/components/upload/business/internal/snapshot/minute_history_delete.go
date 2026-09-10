package snapshot

import (
	"errors"
	"os"
	"path/filepath"
)

var (
	ErrMinuteHistoryStoreDisabled = errors.New("minute history store is disabled")
	ErrInvalidMinuteHistoryDay    = errors.New("date must be YYYY-MM-DD or YYYYMMDD")
)

type MinuteHistoryDeleteDayResult struct {
	OK          bool     `json:"ok"`
	Day         string   `json:"day"`
	Deleted     bool     `json:"deleted"`
	SymbolCount int      `json:"symbol_count"`
	RowCount    int      `json:"row_count"`
	Warnings    []string `json:"warnings,omitempty"`
}

func (s *Service) DeleteMinuteHistoryDay(day string) (MinuteHistoryDeleteDayResult, error) {
	if s == nil || s.minuteHistory == nil {
		return MinuteHistoryDeleteDayResult{}, ErrMinuteHistoryStoreDisabled
	}
	normalized := normalizeMinuteHistoryDayKey(day)
	if normalized == "" {
		return MinuteHistoryDeleteDayResult{}, ErrInvalidMinuteHistoryDay
	}
	return s.minuteHistory.DeleteDay(normalized)
}

func (s *MinuteHistoryStore) DeleteDay(day string) (MinuteHistoryDeleteDayResult, error) {
	result := MinuteHistoryDeleteDayResult{Day: normalizeMinuteHistoryDayKey(day)}
	if s == nil || s.root == "" {
		return result, ErrMinuteHistoryStoreDisabled
	}
	if result.Day == "" {
		return result, ErrInvalidMinuteHistoryDay
	}

	s.mu.Lock()
	defer s.mu.Unlock()
	rows, err := s.readDayLocked(result.Day)
	if err != nil {
		return result, err
	}
	result.RowCount = len(rows)
	symbols := make(map[string]struct{}, len(rows))
	for _, row := range rows {
		if row.Symbol != "" {
			symbols[row.Symbol] = struct{}{}
		}
	}
	result.SymbolCount = len(symbols)
	dayPath := filepath.Join(s.root, result.Day)
	if _, err := os.Stat(dayPath); os.IsNotExist(err) {
		result.OK = true
		result.Warnings = append(result.Warnings, "minute history day does not exist")
		return result, nil
	} else if err != nil {
		return result, err
	}
	if err := os.RemoveAll(dayPath); err != nil {
		return result, err
	}
	result.OK = true
	result.Deleted = true
	return result, nil
}
