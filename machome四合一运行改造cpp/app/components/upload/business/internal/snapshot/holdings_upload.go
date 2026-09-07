package snapshot

import (
	"context"
	"errors"
	"strings"
	"time"

	"newnavnav/internal/domain"
)

func (s *Service) UpsertUploadedHoldingSnapshot(ctx context.Context, source string, fundSymbol string, holdingDate string, position float64, holdings []domain.Holding) (int, error) {
	repository, ok := s.repository.(holdingSnapshotRepository)
	if !ok || repository == nil {
		return 0, errors.New("holding snapshot upload is not supported by repository")
	}

	fundSymbol = strings.ToUpper(strings.TrimSpace(fundSymbol))
	holdingDate = strings.TrimSpace(holdingDate)
	source = strings.TrimSpace(source)
	if source == "" {
		source = "upload_holdings"
	}
	if fundSymbol == "" {
		return 0, errors.New("fund_symbol is required")
	}
	if _, err := time.Parse("2006-01-02", holdingDate); err != nil {
		return 0, errors.New("holding_date must be YYYY-MM-DD")
	}

	valid := make([]domain.Holding, 0, len(holdings))
	for _, holding := range holdings {
		symbol := strings.ToUpper(strings.TrimSpace(holding.HoldingSymbol))
		if symbol == "" || holding.Ratio <= 0 {
			continue
		}
		holding.FundSymbol = fundSymbol
		holding.HoldingDate = holdingDate
		holding.HoldingSymbol = symbol
		holding.HoldingName = strings.TrimSpace(holding.HoldingName)
		holding.Currency = strings.ToUpper(strings.TrimSpace(holding.Currency))
		holding.Source = source
		valid = append(valid, holding)
	}
	if len(valid) == 0 {
		return 0, errors.New("holdings is required")
	}

	accepted, err := repository.UpsertHoldingSnapshot(ctx, fundSymbol, holdingDate, position, source, valid)
	if err != nil {
		return 0, err
	}
	s.RecordEvent("info", "holdings", "holding snapshot uploaded", map[string]any{
		"source":       source,
		"fund_symbol":  fundSymbol,
		"holding_date": holdingDate,
		"count":        accepted,
	})
	if err := s.Refresh(ctx); err != nil {
		return accepted, err
	}
	return accepted, nil
}
