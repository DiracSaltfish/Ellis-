package snapshot

import (
	"fmt"
	"math"
	"sort"
	"strings"
	"time"

	"newnavnav/internal/domain"
)

type MinuteHistoryRecomputeResult struct {
	OK          bool     `json:"ok"`
	Day         string   `json:"day"`
	TotalRows   int      `json:"total_rows"`
	UpdatedRows int      `json:"updated_rows"`
	SkippedRows int      `json:"skipped_rows"`
	Symbols     []string `json:"symbols"`
	Warnings    []string `json:"warnings,omitempty"`
}

type NavSettingsRatioStatus struct {
	Symbol                  string   `json:"symbol"`
	Name                    string   `json:"name"`
	ModelVersion            string   `json:"model_version"`
	ReferenceSymbol         string   `json:"reference_symbol,omitempty"`
	EffectiveRatio          float64  `json:"effective_ratio"`
	DefaultEffectiveRatio   float64  `json:"default_effective_ratio"`
	EffectiveRatioSource    string   `json:"effective_ratio_source,omitempty"`
	ManualOverrideRatio     *float64 `json:"manual_override_ratio,omitempty"`
	ManualOverrideSource    string   `json:"manual_override_source,omitempty"`
	ManualOverrideUpdatedAt string   `json:"manual_override_updated_at,omitempty"`
	ManualOverrideUpdatedBy string   `json:"manual_override_updated_by,omitempty"`
}

type NavSettingsStatus struct {
	ServerTime       string                   `json:"server_time"`
	MinuteHistoryDay string                   `json:"minute_history_day"`
	Ratios           []NavSettingsRatioStatus `json:"ratios"`
	Operations       []string                 `json:"operations"`
}

func (s *Service) NavSettingsStatus(now time.Time) NavSettingsStatus {
	s.mu.RLock()
	quotes := make(map[string]domain.Quote, len(s.quotes))
	for symbol, quote := range s.quotes {
		quotes[symbol] = quote
	}
	s.mu.RUnlock()

	status := NavSettingsStatus{
		ServerTime:       now.Format(time.RFC3339Nano),
		MinuteHistoryDay: minuteHistoryDay(now),
		Operations: []string{
			"refresh_snapshot",
			"recompute_today_minute_history",
		},
	}
	for _, symbol := range domain.AllSymbols() {
		config, ok := currentMinuteHistoryRatioConfig(s.valuationData, symbol)
		if !ok || config.Ratio <= 0 {
			continue
		}
		name := symbol
		if quote, ok := quotes[symbol]; ok && strings.TrimSpace(quote.Name) != "" {
			name = quote.Name
		}
		var manualRatio *float64
		manualSource := ""
		manualUpdatedAt := ""
		manualUpdatedBy := ""
		if config.ManualOverride != nil {
			value := round6(config.ManualOverride.Ratio)
			manualRatio = &value
			manualSource = config.ManualOverride.Source
			manualUpdatedBy = config.ManualOverride.UpdatedBy
			if !config.ManualOverride.UpdatedAt.IsZero() {
				manualUpdatedAt = config.ManualOverride.UpdatedAt.Format(time.RFC3339Nano)
			}
		}
		status.Ratios = append(status.Ratios, NavSettingsRatioStatus{
			Symbol:                  symbol,
			Name:                    name,
			ModelVersion:            config.ModelVersion,
			ReferenceSymbol:         config.ReferenceSymbol,
			EffectiveRatio:          round6(config.Ratio),
			DefaultEffectiveRatio:   round6(config.DefaultRatio),
			EffectiveRatioSource:    config.RatioSource,
			ManualOverrideRatio:     manualRatio,
			ManualOverrideSource:    manualSource,
			ManualOverrideUpdatedAt: manualUpdatedAt,
			ManualOverrideUpdatedBy: manualUpdatedBy,
		})
	}
	sort.Slice(status.Ratios, func(i, j int) bool {
		return status.Ratios[i].Symbol < status.Ratios[j].Symbol
	})
	return status
}

func (s *Service) RecomputeTodayMinuteHistory(now time.Time) (MinuteHistoryRecomputeResult, error) {
	result := MinuteHistoryRecomputeResult{
		Day: minuteHistoryDay(now),
	}
	if s.minuteHistory == nil {
		result.Warnings = append(result.Warnings, "minute history store is disabled")
		return result, nil
	}

	rows, err := s.minuteHistory.ReadDay(result.Day)
	if err != nil {
		return result, err
	}
	result.TotalRows = len(rows)
	if len(rows) == 0 {
		result.Warnings = append(result.Warnings, "today minute history is empty")
		return result, nil
	}

	s.mu.RLock()
	valuationData := s.valuationData
	s.mu.RUnlock()

	updatedSymbols := map[string]bool{}
	for index, row := range rows {
		config, ok := currentMinuteHistoryRatioConfig(valuationData, row.Symbol)
		if !ok || config.Ratio <= 0 {
			result.SkippedRows++
			continue
		}
		baseNAV, ok := currentMinuteHistoryBaseNAV(valuationData, row.Symbol)
		if !ok || baseNAV <= 0 {
			result.SkippedRows++
			result.addWarning(fmt.Sprintf("%s missing latest base nav", row.Symbol))
			continue
		}
		oldRatio := row.EffectiveRatio
		if oldRatio <= 0 {
			oldRatio, ok = legacyMinuteHistoryEffectiveRatio(row.Symbol)
			if !ok {
				result.SkippedRows++
				result.addWarning(fmt.Sprintf("%s missing historical effective ratio fallback", row.Symbol))
				continue
			}
		}

		updated := false
		if value, ok := recomputeEstimateFromRatio(row.OfficialEST, baseNAV, oldRatio, config.Ratio); ok {
			row.OfficialEST = value
			updated = true
		}
		if value, ok := recomputeEstimateFromRatio(row.FairEST, baseNAV, oldRatio, config.Ratio); ok {
			row.FairEST = value
			updated = true
		}
		if row.RealtimeEST != nil {
			if value, ok := recomputeEstimateFromRatio(*row.RealtimeEST, baseNAV, oldRatio, config.Ratio); ok {
				row.RealtimeEST = &value
				updated = true
			}
		}
		if value, ok := recomputeEstimateFromRatio(row.EstimatedNAV, baseNAV, oldRatio, config.Ratio); ok {
			row.EstimatedNAV = value
			row.PremiumPct = minuteHistoryPremium(row.MarketPrice, value)
			updated = true
		}
		if !updated {
			result.SkippedRows++
			continue
		}

		row.EffectiveRatio = round6(config.Ratio)
		if strings.HasPrefix(row.ModelVersion, "v1.fundpair") && strings.HasPrefix(config.ModelVersion, "v1.fundpair") {
			// Preserve the existing fundpair factor source label when only the ratio changed.
		} else {
			row.ModelVersion = config.ModelVersion
		}
		if config.ReferenceSymbol != "" {
			row.ReferenceSymbol = config.ReferenceSymbol
		}
		row.UploadSource = "navsettings_recompute"
		rows[index] = row
		result.UpdatedRows++
		updatedSymbols[row.Symbol] = true
	}

	if result.UpdatedRows == 0 {
		result.Symbols = sortedRecomputedSymbols(updatedSymbols)
		return result, nil
	}
	if err := s.minuteHistory.ReplaceDay(result.Day, rows); err != nil {
		return result, err
	}
	result.OK = true
	result.Symbols = sortedRecomputedSymbols(updatedSymbols)
	return result, nil
}

type minuteHistoryRatioConfig struct {
	Ratio           float64
	DefaultRatio    float64
	RatioSource     string
	ModelVersion    string
	ReferenceSymbol string
	ManualOverride  *domain.ManualValuationPositionOverride
}

func currentMinuteHistoryRatioConfig(data domain.ValuationData, symbol string) (minuteHistoryRatioConfig, bool) {
	symbol = strings.ToUpper(strings.TrimSpace(symbol))
	ratio, source, override := domain.EffectiveRatio(data, symbol)
	defaultRatio, _ := domain.DefaultEffectiveRatio(data, symbol)
	if strategy, exists := domain.WeightedAnchorStrategyForFund(symbol); exists && strategy.InvestmentRatio > 0 {
		return minuteHistoryRatioConfig{
			Ratio:           ratio,
			DefaultRatio:    defaultRatio,
			RatioSource:     source,
			ModelVersion:    "v1.weighted_anchor." + strings.ToLower(strategy.Label),
			ReferenceSymbol: strategy.ReferenceSymbol,
			ManualOverride:  override,
		}, true
	}
	if strategy, exists := domain.CommodityBasketStrategyForFund(symbol); exists && strategy.InvestmentRatio() > 0 {
		return minuteHistoryRatioConfig{
			Ratio:          ratio,
			DefaultRatio:   defaultRatio,
			RatioSource:    source,
			ModelVersion:   "v1.commodity_basket.cn",
			ManualOverride: override,
		}, true
	}
	if strategy, exists := domain.SingleCommodityFutureStrategyForFund(symbol); exists && strategy.InvestmentRatio > 0 {
		return minuteHistoryRatioConfig{
			Ratio:           ratio,
			DefaultRatio:    defaultRatio,
			RatioSource:     source,
			ModelVersion:    "v1.commodity_futures.single.cn",
			ReferenceSymbol: strategy.ReferenceSymbol,
			ManualOverride:  override,
		}, true
	}
	if referenceSymbol, ok := currentHoldingsCommodityFutureReferenceSymbol(data, symbol); ok {
		return minuteHistoryRatioConfig{
			Ratio:           ratio,
			DefaultRatio:    defaultRatio,
			RatioSource:     source,
			ModelVersion:    "v1.commodity_futures.cn",
			ReferenceSymbol: referenceSymbol,
			ManualOverride:  override,
		}, true
	}
	if hasCurrentPositiveHoldings(data, symbol) {
		return minuteHistoryRatioConfig{
			Ratio:          ratio,
			DefaultRatio:   defaultRatio,
			RatioSource:    source,
			ModelVersion:   "v1.holdings.cn",
			ManualOverride: override,
		}, true
	}
	if pairs := data.FundPairs[symbol]; len(pairs) > 0 {
		for _, pair := range pairs {
			if strings.TrimSpace(pair.PairSymbol) == "" {
				continue
			}
			return minuteHistoryRatioConfig{
				Ratio:           ratio,
				DefaultRatio:    defaultRatio,
				RatioSource:     source,
				ModelVersion:    "v1.fundpair",
				ReferenceSymbol: pair.PairSymbol,
				ManualOverride:  override,
			}, true
		}
	}
	return minuteHistoryRatioConfig{}, false
}

func currentHoldingsCommodityFutureReferenceSymbol(data domain.ValuationData, symbol string) (string, bool) {
	holdingDate, ok := data.CurrentHoldingDates[symbol]
	if !ok || strings.TrimSpace(holdingDate.Date) == "" {
		return "", false
	}
	references := map[string]bool{}
	for _, holding := range data.Holdings[symbol] {
		if holding.HoldingDate != holdingDate.Date || holding.Ratio <= 0 {
			continue
		}
		reference, ok := domain.CommodityFutureReferenceForHolding(holding.HoldingSymbol)
		if !ok {
			continue
		}
		references[reference.Symbol] = true
	}
	if len(references) == 0 {
		return "", false
	}
	if len(references) == 1 {
		for referenceSymbol := range references {
			return referenceSymbol, true
		}
	}
	return "", true
}

func hasCurrentPositiveHoldings(data domain.ValuationData, symbol string) bool {
	holdingDate, ok := data.CurrentHoldingDates[symbol]
	if !ok || strings.TrimSpace(holdingDate.Date) == "" {
		return false
	}
	for _, holding := range data.Holdings[symbol] {
		if holding.HoldingDate == holdingDate.Date && holding.Ratio > 0 {
			return true
		}
	}
	return false
}

func currentMinuteHistoryBaseNAV(data domain.ValuationData, symbol string) (float64, bool) {
	nav, ok := data.LatestNetValues[strings.ToUpper(strings.TrimSpace(symbol))]
	if !ok || nav.NAV <= 0 {
		return 0, false
	}
	return nav.NAV, true
}

func legacyMinuteHistoryEffectiveRatio(symbol string) (float64, bool) {
	ratio, ok := map[string]float64{
		"SH501300": 1.0,
		"SH501018": 1.0,
		"SZ160719": 0.9495,
		"SZ160723": 0.83,
		"SZ161129": 0.9488,
		"SZ161815": 0.8465003081095107,
		"SZ164701": 0.9215,
		"SH513100": 1.0,
		"SH513110": 1.0,
		"SH513300": 1.0,
		"SH513390": 1.0,
		"SH513870": 1.0,
		"SZ159501": 1.0,
		"SZ159513": 1.0,
		"SZ159632": 1.0,
		"SZ159659": 1.0,
		"SZ159660": 1.0,
		"SZ159696": 1.0,
		"SZ159941": 1.0,
		"SZ161130": 1.0,
	}[strings.ToUpper(strings.TrimSpace(symbol))]
	return ratio, ok
}

func recomputeEstimateFromRatio(oldValue float64, baseNAV float64, oldRatio float64, newRatio float64) (float64, bool) {
	if oldValue <= 0 || baseNAV <= 0 {
		return 0, false
	}
	if oldRatio <= 0 || oldRatio > 1 || newRatio < 0 || newRatio > 1 {
		return 0, false
	}
	multiplier := (oldValue/baseNAV - (1 - oldRatio)) / oldRatio
	if math.IsNaN(multiplier) || math.IsInf(multiplier, 0) {
		return 0, false
	}
	value := baseNAV * ((1 - newRatio) + newRatio*multiplier)
	if value <= 0 || math.IsNaN(value) || math.IsInf(value, 0) {
		return 0, false
	}
	return round6(value), true
}

func minuteHistoryPremium(price float64, estimate float64) float64 {
	if estimate <= 0 {
		return 0
	}
	return round6((price/estimate - 1) * 100)
}

func sortedRecomputedSymbols(symbols map[string]bool) []string {
	out := make([]string, 0, len(symbols))
	for symbol := range symbols {
		out = append(out, symbol)
	}
	sort.Strings(out)
	return out
}

func (r *MinuteHistoryRecomputeResult) addWarning(warning string) {
	if warning == "" || len(r.Warnings) >= 50 {
		return
	}
	r.Warnings = append(r.Warnings, warning)
}
