package snapshot

import (
	"context"
	"errors"
	"strings"

	"newnavnav/internal/domain"
)

type manualValuationPositionRepository interface {
	UpsertManualValuationPositionOverride(ctx context.Context, symbol string, ratio float64, source string, updatedBy string) error
}

func (s *Service) UpdateManualValuationPosition(ctx context.Context, symbol string, ratio float64, source string, updatedBy string) error {
	repository, ok := any(s.repository).(manualValuationPositionRepository)
	if !ok || repository == nil {
		return errors.New("manual valuation position repository is unavailable")
	}
	symbol = strings.ToUpper(strings.TrimSpace(symbol))
	if symbol == "" || ratio <= 0 {
		return errors.New("symbol and ratio are required")
	}
	if err := repository.UpsertManualValuationPositionOverride(ctx, symbol, ratio, source, updatedBy); err != nil {
		return err
	}
	s.mu.Lock()
	if s.valuationData.ManualPositionOverrides == nil {
		s.valuationData.ManualPositionOverrides = map[string]domain.ManualValuationPositionOverride{}
	}
	s.valuationData.ManualPositionOverrides[symbol] = domain.ManualValuationPositionOverride{
		Symbol:    symbol,
		Ratio:     ratio,
		Source:    source,
		UpdatedBy: updatedBy,
		UpdatedAt: s.nowTime(),
	}
	s.mu.Unlock()
	return nil
}
