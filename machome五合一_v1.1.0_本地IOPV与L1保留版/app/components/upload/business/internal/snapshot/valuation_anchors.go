package snapshot

import (
	"context"
	"fmt"
	"sort"
	"strings"
	"time"

	"newnavnav/internal/domain"
)

type ValuationAnchorPriceRequestItem struct {
	FundSymbol           string  `json:"fund_symbol"`
	AnchorDate           string  `json:"anchor_date"`
	AnchorKey            string  `json:"anchor_key"`
	AnchorLabel          string  `json:"anchor_label,omitempty"`
	ReferenceSymbol      string  `json:"reference_symbol"`
	Market               string  `json:"market"`
	Weight               float64 `json:"weight"`
	TargetAt             string  `json:"target_at"`
	TargetAtUnixMillis   int64   `json:"target_at_unix_millis"`
	TargetTimezone       string  `json:"target_timezone"`
	TargetLocalDate      string  `json:"target_local_date"`
	TargetLocalTime      string  `json:"target_local_time"`
	TargetBeijingTime    string  `json:"target_beijing_time"`
	CapturePolicy        string  `json:"capture_policy"`
	CaptureWindowSeconds int     `json:"capture_window_seconds"`
	Reason               string  `json:"reason,omitempty"`
}

type ValuationAnchorUploadResult struct {
	OK       bool     `json:"ok"`
	Source   string   `json:"source"`
	Accepted int      `json:"accepted"`
	Skipped  int      `json:"skipped"`
	Warnings []string `json:"warnings,omitempty"`
}

type ValuationAnchorRepository interface {
	UpsertValuationAnchorPrices(ctx context.Context, prices []domain.ValuationAnchorPrice) error
}

func (s *Service) MissingValuationAnchorPriceRequests(ctx context.Context, limit int) ([]ValuationAnchorPriceRequestItem, error) {
	if s.repository == nil {
		return nil, nil
	}
	data, err := s.repository.LoadValuationData(ctx)
	if err != nil {
		return nil, err
	}
	domain.ApplyStaticValuationData(&data)
	return missingValuationAnchorPriceRequestsFromData(data, time.Now(), limit), nil
}

func missingValuationAnchorPriceRequestsFromData(data domain.ValuationData, now time.Time, limit int) []ValuationAnchorPriceRequestItem {
	seen := map[string]bool{}
	requests := make([]ValuationAnchorPriceRequestItem, 0)
	add := func(strategy domain.WeightedAnchorStrategy, anchorDate string, reason string) {
		anchorDate = strings.TrimSpace(anchorDate)
		if anchorDate == "" {
			return
		}
		setKey := domain.ValuationAnchorSetKey(strategy.FundSymbol, anchorDate, strategy.ReferenceSymbol)
		existing := data.ValuationAnchors[setKey]
		existingByKey := map[string]bool{}
		for _, point := range existing.Points {
			if point.Price > 0 {
				existingByKey[point.AnchorKey] = true
			}
		}
		for _, rule := range strategy.Points {
			if existingByKey[rule.Key] {
				continue
			}
			targetAt, ok := rule.TargetAt(anchorDate)
			if !ok {
				continue
			}
			key := strategy.FundSymbol + "|" + anchorDate + "|" + rule.Key
			if seen[key] {
				continue
			}
			seen[key] = true
			requests = append(requests, valuationAnchorRequestItem(strategy, rule, anchorDate, targetAt, reason))
		}
	}

	for _, strategy := range domain.WeightedAnchorStrategies() {
		if nav, ok := data.LatestNetValues[strategy.FundSymbol]; ok && nav.Date != "" && nav.NAV > 0 {
			add(strategy, nav.Date, "latest_nav:"+strategy.FundSymbol)
		}
		for _, day := range recentShanghaiWeekdays(now, 5) {
			add(strategy, day, "scheduled_anchor:"+strategy.FundSymbol)
		}
	}
	sort.Slice(requests, func(i, j int) bool {
		if requests[i].TargetAt == requests[j].TargetAt {
			if requests[i].FundSymbol == requests[j].FundSymbol {
				return requests[i].AnchorKey < requests[j].AnchorKey
			}
			return requests[i].FundSymbol < requests[j].FundSymbol
		}
		return requests[i].TargetAt < requests[j].TargetAt
	})
	if limit > 0 && len(requests) > limit {
		requests = requests[:limit]
	}
	return requests
}

func valuationAnchorRequestItem(
	strategy domain.WeightedAnchorStrategy,
	rule domain.WeightedAnchorPointRule,
	anchorDate string,
	targetAt time.Time,
	reason string,
) ValuationAnchorPriceRequestItem {
	targetLoc := targetAt.Location()
	beijing := targetAt.In(shanghaiLocation())
	window := rule.CaptureWindowSeconds
	if window <= 0 {
		window = 180
	}
	return ValuationAnchorPriceRequestItem{
		FundSymbol:           strategy.FundSymbol,
		AnchorDate:           anchorDate,
		AnchorKey:            rule.Key,
		AnchorLabel:          rule.Label,
		ReferenceSymbol:      strategy.ReferenceSymbol,
		Market:               basePriceMarket(strategy.ReferenceSymbol),
		Weight:               rule.Weight,
		TargetAt:             targetAt.UTC().Format(time.RFC3339),
		TargetAtUnixMillis:   targetAt.UTC().UnixMilli(),
		TargetTimezone:       targetLoc.String(),
		TargetLocalDate:      targetAt.In(targetLoc).Format("2006-01-02"),
		TargetLocalTime:      targetAt.In(targetLoc).Format("15:04:05"),
		TargetBeijingTime:    beijing.Format(time.RFC3339),
		CapturePolicy:        "exact_or_current_within_window",
		CaptureWindowSeconds: window,
		Reason:               reason,
	}
}

func recentShanghaiWeekdays(now time.Time, days int) []string {
	if days <= 0 {
		return nil
	}
	out := make([]string, 0, days)
	local := now.In(shanghaiLocation())
	cursor := time.Date(local.Year(), local.Month(), local.Day(), 0, 0, 0, 0, shanghaiLocation())
	for len(out) < days {
		if cursor.Weekday() != time.Saturday && cursor.Weekday() != time.Sunday {
			out = append(out, cursor.Format("2006-01-02"))
		}
		cursor = cursor.AddDate(0, 0, -1)
	}
	return out
}

func (s *Service) UpsertValuationAnchorPrices(ctx context.Context, source string, prices []domain.ValuationAnchorPrice) (ValuationAnchorUploadResult, error) {
	result := ValuationAnchorUploadResult{Source: strings.TrimSpace(source)}
	if result.Source == "" {
		result.Source = "valuation_anchor_upload"
	}
	repo, ok := s.repository.(ValuationAnchorRepository)
	if s.repository == nil || !ok {
		result.Warnings = append(result.Warnings, "valuation anchor repository is unavailable")
		return result, nil
	}
	valid := make([]domain.ValuationAnchorPrice, 0, len(prices))
	for index, price := range prices {
		normalized, warnings, ok := normalizeValuationAnchorPrice(price, result.Source)
		if !ok {
			result.Skipped++
			for _, warning := range warnings {
				result.addWarning(fmt.Sprintf("row %d %s", index+1, warning))
			}
			continue
		}
		valid = append(valid, normalized)
	}
	if len(valid) == 0 {
		return result, nil
	}
	if err := repo.UpsertValuationAnchorPrices(ctx, valid); err != nil {
		return result, err
	}
	result.OK = true
	result.Accepted = len(valid)
	s.RecordEvent("info", "valuation_anchor", "valuation anchor prices uploaded", map[string]any{
		"source": result.Source,
		"count":  len(valid),
	})
	return result, nil
}

func normalizeValuationAnchorPrice(price domain.ValuationAnchorPrice, source string) (domain.ValuationAnchorPrice, []string, bool) {
	var warnings []string
	price.FundSymbol = normalizeUploadSymbol(price.FundSymbol)
	price.ReferenceSymbol = normalizeUploadSymbol(price.ReferenceSymbol)
	price.AnchorDate = strings.TrimSpace(price.AnchorDate)
	price.AnchorKey = strings.TrimSpace(price.AnchorKey)
	if price.FundSymbol == "" {
		warnings = append(warnings, "missing fund_symbol")
	}
	if price.ReferenceSymbol == "" {
		warnings = append(warnings, "missing reference_symbol")
	}
	if price.AnchorDate == "" {
		warnings = append(warnings, "missing anchor_date")
	}
	if price.AnchorKey == "" {
		warnings = append(warnings, "missing anchor_key")
	}
	if price.Price <= 0 {
		warnings = append(warnings, "price must be positive")
	}
	if price.TargetAt.IsZero() {
		warnings = append(warnings, "target_at is required")
	}
	if len(warnings) > 0 {
		return domain.ValuationAnchorPrice{}, warnings, false
	}
	if price.Weight <= 0 {
		if strategy, ok := domain.WeightedAnchorStrategyForFund(price.FundSymbol); ok {
			for _, rule := range strategy.Points {
				if rule.Key == price.AnchorKey {
					price.Weight = rule.Weight
					break
				}
			}
		}
	}
	if price.Weight <= 0 {
		warnings = append(warnings, "weight must be positive")
		return domain.ValuationAnchorPrice{}, warnings, false
	}
	if price.Source == "" {
		price.Source = "ws_valuation_anchor:" + source
	}
	if price.CaptureStatus == "" {
		price.CaptureStatus = "captured"
	}
	if price.TargetTimezone == "" {
		price.TargetTimezone = "UTC"
	}
	return price, nil, true
}

func (r *ValuationAnchorUploadResult) addWarning(warning string) {
	if warning == "" || len(r.Warnings) >= 50 {
		return
	}
	r.Warnings = append(r.Warnings, warning)
}
