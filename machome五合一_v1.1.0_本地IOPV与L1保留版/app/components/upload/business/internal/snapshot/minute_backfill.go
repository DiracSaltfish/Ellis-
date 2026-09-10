package snapshot

import (
	"fmt"
	"sort"
	"strings"
	"time"

	"newnavnav/internal/domain"
)

type MinuteBar struct {
	Symbol    string  `json:"symbol"`
	TradeDate string  `json:"trade_date,omitempty"`
	Minute    string  `json:"minute,omitempty"`
	Timestamp string  `json:"timestamp,omitempty"`
	LastPrice float64 `json:"last_price,omitempty"`
	Open      float64 `json:"open,omitempty"`
	High      float64 `json:"high,omitempty"`
	Low       float64 `json:"low,omitempty"`
	Close     float64 `json:"close,omitempty"`
	Volume    float64 `json:"volume,omitempty"`
	Amount    float64 `json:"amount,omitempty"`
	Source    string  `json:"source,omitempty"`
}

type MinuteBackfillResult struct {
	OK       bool     `json:"ok"`
	Source   string   `json:"source"`
	Accepted int      `json:"accepted"`
	Skipped  int      `json:"skipped"`
	Symbols  []string `json:"symbols"`
	Warnings []string `json:"warnings,omitempty"`
}

type normalizedMinuteBar struct {
	Symbol    string
	Minute    string
	Timestamp string
	Price     float64
	Source    string
}

func (s *Service) BackfillMinuteBars(source string, bars []MinuteBar) (MinuteBackfillResult, error) {
	source = strings.TrimSpace(source)
	if source == "" {
		source = "qmt_online_1m"
	}
	result := MinuteBackfillResult{
		Source: source,
	}
	if s.minuteHistory == nil {
		result.Warnings = append(result.Warnings, "minute history store is disabled")
		return result, nil
	}
	if len(bars) == 0 {
		result.Warnings = append(result.Warnings, "rows is required")
		return result, nil
	}

	now := s.nowTime()
	today := minuteHistoryDay(now)
	fundSymbols := map[string]bool{}
	for _, symbol := range domain.AllSymbols() {
		fundSymbols[symbol] = true
	}

	normalized := make([]normalizedMinuteBar, 0, len(bars))
	seenSymbols := map[string]bool{}
	for index, bar := range bars {
		symbol := normalizeMinuteBarSymbol(bar.Symbol)
		if symbol == "" {
			result.Skipped++
			result.addWarning(fmt.Sprintf("row %d missing symbol", index+1))
			continue
		}
		if !fundSymbols[symbol] {
			result.Skipped++
			result.addWarning(fmt.Sprintf("%s is not a tracked fund symbol", symbol))
			continue
		}
		minute, timestamp, ok := normalizeMinuteBarTime(bar)
		if !ok {
			result.Skipped++
			result.addWarning(fmt.Sprintf("%s missing valid minute", symbol))
			continue
		}
		if minuteHistoryDayFromMinute(minute) != today {
			result.Skipped++
			result.addWarning(fmt.Sprintf("%s %s is not today", symbol, minute))
			continue
		}
		if !isMinuteHistorySessionMinute(minute) {
			result.Skipped++
			continue
		}
		price := bar.LastPrice
		if price <= 0 {
			price = bar.Close
		}
		if price <= 0 {
			result.Skipped++
			result.addWarning(fmt.Sprintf("%s %s missing positive price", symbol, minute))
			continue
		}
		rowSource := strings.TrimSpace(bar.Source)
		if rowSource == "" {
			rowSource = source
		}
		normalized = append(normalized, normalizedMinuteBar{
			Symbol:    symbol,
			Minute:    minute,
			Timestamp: timestamp,
			Price:     price,
			Source:    rowSource,
		})
		seenSymbols[symbol] = true
	}
	if len(normalized) == 0 {
		result.Source = source
		result.Symbols = sortedMinuteBackfillSymbols(seenSymbols)
		return result, nil
	}

	existingByKey := make(map[string]MinuteHistoryPoint)
	for symbol := range seenSymbols {
		rows, err := s.minuteHistory.Read(symbol, 2, now)
		if err != nil {
			return result, err
		}
		for _, row := range rows {
			existingByKey[minuteBackfillKey(row.Symbol, row.Minute)] = row
		}
	}

	quotes, valuationData := s.minuteBackfillBaseQuotes(now)

	points := make([]MinuteHistoryPoint, 0, len(normalized))
	for _, bar := range normalized {
		if existing, ok := existingByKey[minuteBackfillKey(bar.Symbol, bar.Minute)]; ok && existing.EstimatedNAV > 0 {
			existing.MarketPrice = bar.Price
			existing.PremiumPct = (bar.Price/existing.EstimatedNAV - 1) * 100
			existing.Timestamp = bar.Timestamp
			existing.QuoteSource = bar.Source
			existing.QuoteStatus = "backfilled"
			existing.UploadSource = source
			points = append(points, existing)
			continue
		}

		quote, ok := quotes[bar.Symbol]
		if !ok {
			quote = domain.Quote{Symbol: bar.Symbol}
		}
		quote.Price = bar.Price
		quote.Source = bar.Source
		quote.IsRealtime = true
		quote.RealtimeStatus = "backfilled"
		estimate := s.engine.Estimate(quote, quotes, valuationData, now)
		estimatedNAV := estimate.FairEst
		premiumPct := estimate.FairPremium
		if estimate.RealtimeEst != nil && *estimate.RealtimeEst > 0 {
			estimatedNAV = *estimate.RealtimeEst
			if estimate.RealtimePremium != nil {
				premiumPct = (bar.Price/estimatedNAV - 1) * 100
			}
		}
		if estimatedNAV <= 0 {
			result.Skipped++
			result.addWarning(fmt.Sprintf("%s %s has no valid estimate", bar.Symbol, bar.Minute))
			continue
		}
		points = append(points, MinuteHistoryPoint{
			Symbol:          bar.Symbol,
			Minute:          bar.Minute,
			Timestamp:       bar.Timestamp,
			MarketPrice:     bar.Price,
			EstimatedNAV:    estimatedNAV,
			PremiumPct:      premiumPct,
			OfficialEST:     estimate.OfficialEst,
			FairEST:         estimate.FairEst,
			RealtimeEST:     estimate.RealtimeEst,
			EffectiveRatio:  estimate.EffectiveRatio,
			QuoteSource:     bar.Source,
			QuoteStatus:     "backfilled",
			ModelVersion:    estimate.ModelVersion,
			ReferenceSymbol: estimate.ReferenceSymbol,
			UploadSource:    source,
		})
	}
	if err := s.minuteHistory.Upsert(now, points); err != nil {
		return result, err
	}

	result.OK = len(points) > 0
	result.Accepted = len(points)
	result.Symbols = sortedMinuteBackfillSymbols(seenSymbols)
	return result, nil
}

func (s *Service) BackfillReferenceMinuteBars(source string, bars []MinuteBar) (MinuteBackfillResult, error) {
	source = strings.TrimSpace(source)
	if source == "" {
		source = "reference_1m"
	}
	result := MinuteBackfillResult{Source: source}
	if s.minuteHistory == nil {
		result.Warnings = append(result.Warnings, "minute history store is disabled")
		return result, nil
	}
	if len(bars) == 0 {
		result.Warnings = append(result.Warnings, "rows is required")
		return result, nil
	}

	now := s.nowTime()
	today := minuteHistoryDay(now)
	byMinute := make(map[string]map[string]domain.Quote)
	seenSymbols := map[string]bool{}
	for index, bar := range bars {
		symbol := normalizeReferenceMinuteSymbol(bar.Symbol)
		if symbol == "" {
			result.Skipped++
			result.addWarning(fmt.Sprintf("row %d missing symbol", index+1))
			continue
		}
		minute, timestamp, ok := normalizeMinuteBarTime(bar)
		if !ok {
			result.Skipped++
			result.addWarning(fmt.Sprintf("%s missing valid minute", symbol))
			continue
		}
		if minuteHistoryDayFromMinute(minute) != today {
			result.Skipped++
			result.addWarning(fmt.Sprintf("%s %s is not today", symbol, minute))
			continue
		}
		if !isMinuteHistorySessionMinute(minute) {
			result.Skipped++
			continue
		}
		price := bar.LastPrice
		if price <= 0 {
			price = bar.Close
		}
		if price <= 0 {
			result.Skipped++
			result.addWarning(fmt.Sprintf("%s %s missing positive price", symbol, minute))
			continue
		}
		rowSource := strings.TrimSpace(bar.Source)
		if rowSource == "" {
			rowSource = source
		}
		quote := domain.Quote{
			Symbol:         symbol,
			Name:           symbol,
			Price:          price,
			PrevClose:      price,
			Open:           bar.Open,
			High:           bar.High,
			Low:            bar.Low,
			Volume:         bar.Volume,
			Amount:         bar.Amount,
			QuoteDate:      minute[:10],
			QuoteTime:      minute[11:] + ":00",
			Source:         rowSource,
			SourceSymbol:   symbol,
			QuoteSession:   referenceQuoteSession(symbol),
			IsRealtime:     true,
			RealtimeStatus: "reference_backfilled",
			FetchedAt:      parseMinuteTimeOrNow(timestamp, now),
		}
		if byMinute[minute] == nil {
			byMinute[minute] = make(map[string]domain.Quote)
		}
		byMinute[minute][symbol] = quote
		seenSymbols[symbol] = true
	}
	if len(byMinute) == 0 {
		result.Symbols = sortedMinuteBackfillSymbols(seenSymbols)
		return result, nil
	}

	baseQuotes, valuationData := s.minuteBackfillBaseQuotes(now)
	fundSymbols := impactedReferenceBackfillFunds(valuationData, seenSymbols)
	if len(fundSymbols) == 0 {
		result.Symbols = sortedMinuteBackfillSymbols(seenSymbols)
		result.Warnings = append(result.Warnings, "no tracked funds depend on uploaded reference symbols")
		return result, nil
	}

	existingByKey := make(map[string]MinuteHistoryPoint)
	for _, symbol := range fundSymbols {
		rows, err := s.minuteHistory.Read(symbol, 2, now)
		if err != nil {
			return result, err
		}
		for _, row := range rows {
			existingByKey[minuteBackfillKey(row.Symbol, row.Minute)] = row
		}
	}

	minutes := make([]string, 0, len(byMinute))
	for minute := range byMinute {
		minutes = append(minutes, minute)
	}
	sort.Strings(minutes)

	points := make([]MinuteHistoryPoint, 0, len(minutes)*len(fundSymbols))
	for _, minute := range minutes {
		minuteTime, err := parseMinuteHistoryMinute(minute)
		if err != nil {
			result.Skipped += len(fundSymbols)
			continue
		}
		quotes := make(map[string]domain.Quote, len(baseQuotes)+len(byMinute[minute]))
		for symbol, quote := range baseQuotes {
			quotes[symbol] = quote
		}
		for symbol, quote := range byMinute[minute] {
			quotes[symbol] = quote
		}
		for _, fundSymbol := range fundSymbols {
			fundQuote := quotes[fundSymbol]
			if strings.TrimSpace(fundQuote.Symbol) == "" {
				fundQuote = domain.Quote{Symbol: fundSymbol, Name: fundSymbol, Source: "reference_minute_backfill"}
			}
			estimate := s.engine.Estimate(fundQuote, quotes, valuationData, minuteTime)
			estimatedNAV := estimate.FairEst
			if estimate.RealtimeEst != nil && *estimate.RealtimeEst > 0 {
				estimatedNAV = *estimate.RealtimeEst
			}
			if estimatedNAV <= 0 {
				result.Skipped++
				continue
			}
			existing := existingByKey[minuteBackfillKey(fundSymbol, minute)]
			marketPrice := existing.MarketPrice
			premiumPct := 0.0
			if marketPrice > 0 {
				premiumPct = (marketPrice/estimatedNAV - 1) * 100
			}
			points = append(points, MinuteHistoryPoint{
				Symbol:          fundSymbol,
				Minute:          minute,
				Timestamp:       minuteTime.In(shanghaiLocation()).Format(time.RFC3339Nano),
				MarketPrice:     marketPrice,
				EstimatedNAV:    estimatedNAV,
				PremiumPct:      premiumPct,
				OfficialEST:     estimate.OfficialEst,
				FairEST:         estimate.FairEst,
				RealtimeEST:     estimate.RealtimeEst,
				EffectiveRatio:  estimate.EffectiveRatio,
				QuoteSource:     source,
				QuoteStatus:     "reference_backfilled",
				ModelVersion:    estimate.ModelVersion,
				ReferenceSymbol: estimate.ReferenceSymbol,
				UploadSource:    source,
			})
		}
	}
	if err := s.minuteHistory.Upsert(now, points); err != nil {
		return result, err
	}

	result.OK = len(points) > 0
	result.Accepted = len(points)
	result.Symbols = sortedMinuteBackfillSymbols(seenSymbols)
	return result, nil
}

func normalizeMinuteBarSymbol(symbol string) string {
	symbol = strings.TrimSpace(symbol)
	if symbol == "" {
		return ""
	}
	upper := strings.ToUpper(symbol)
	if parts := strings.Split(upper, "."); len(parts) == 2 && len(parts[0]) == 6 {
		switch parts[1] {
		case "SH", "XSHG", "SSE":
			return "SH" + parts[0]
		case "SZ", "XSHE", "SZSE":
			return "SZ" + parts[0]
		}
	}
	if len(upper) >= 8 {
		prefix := upper[:2]
		if prefix == "SH" || prefix == "SZ" || prefix == "BJ" {
			return prefix + upper[2:]
		}
	}
	if len(upper) == 6 && allDigits(upper) {
		if strings.HasPrefix(upper, "5") {
			return "SH" + upper
		}
		return "SZ" + upper
	}
	return normalizeUploadSymbol(symbol)
}

func (s *Service) minuteBackfillBaseQuotes(now time.Time) (map[string]domain.Quote, domain.ValuationData) {
	s.mu.RLock()
	defer s.mu.RUnlock()

	quotes := make(map[string]domain.Quote, len(s.quotes)+len(s.uploadedQuotes))
	for symbol, quote := range s.quotes {
		quotes[symbol] = quote
	}
	if s.enableUploadQuotes {
		for symbol, quote := range s.uploadedQuotes {
			quotes[symbol] = quoteWithUploadFreshness(quote, now)
		}
	}
	return quotes, s.valuationData
}

func normalizeReferenceMinuteSymbol(symbol string) string {
	return normalizeUploadSymbol(symbol)
}

func normalizeMinuteBarTime(bar MinuteBar) (string, string, bool) {
	for _, value := range []string{bar.Minute, bar.Timestamp} {
		parsed, err := parseMinuteHistoryMinute(value)
		if err != nil {
			continue
		}
		minute := minuteHistoryMinute(parsed)
		return minute, parsed.In(shanghaiLocation()).Format(time.RFC3339Nano), true
	}
	return "", "", false
}

func minuteBackfillKey(symbol, minute string) string {
	return strings.ToUpper(strings.TrimSpace(symbol)) + "|" + strings.TrimSpace(minute)
}

func sortedMinuteBackfillSymbols(symbols map[string]bool) []string {
	out := make([]string, 0, len(symbols))
	for symbol := range symbols {
		out = append(out, symbol)
	}
	sort.Strings(out)
	return out
}

func impactedReferenceBackfillFunds(data domain.ValuationData, seenSymbols map[string]bool) []string {
	if len(seenSymbols) == 0 {
		return nil
	}
	seenFunds := make(map[string]bool)
	add := func(fund string) {
		fund = strings.ToUpper(strings.TrimSpace(fund))
		if fund == "" || seenFunds[fund] {
			return
		}
		seenFunds[fund] = true
	}

	for _, strategy := range domain.WeightedAnchorStrategies() {
		if seenSymbols[strategy.ReferenceSymbol] {
			add(strategy.FundSymbol)
		}
	}
	for _, strategy := range domain.CommodityBasketStrategies() {
		for _, leg := range strategy.Legs {
			if seenSymbols[leg.Symbol] {
				add(strategy.FundSymbol)
				break
			}
		}
	}
	for fund, pairs := range data.FundPairs {
		for _, pair := range pairs {
			if seenSymbols[pair.PairSymbol] {
				add(fund)
				break
			}
		}
	}
	for fund, holdings := range data.Holdings {
		if strategy, ok := domain.SingleCommodityFutureStrategyForFund(fund); ok {
			if seenSymbols[strategy.ReferenceSymbol] {
				add(fund)
			}
			continue
		}
		if _, ok := domain.WeightedAnchorStrategyForFund(fund); ok {
			continue
		}
		if _, ok := domain.CommodityBasketStrategyForFund(fund); ok {
			continue
		}
		currentHoldingDate := strings.TrimSpace(data.CurrentHoldingDates[fund].Date)
		if currentHoldingDate == "" {
			continue
		}
		for _, holding := range holdings {
			if holding.HoldingDate != currentHoldingDate || holding.Ratio <= 0 || strings.TrimSpace(holding.HoldingSymbol) == "" {
				continue
			}
			if seenSymbols[holding.HoldingSymbol] {
				add(fund)
				break
			}
			if reference, ok := domain.CommodityFutureReferenceForHolding(holding.HoldingSymbol); ok && seenSymbols[reference.Symbol] {
				add(fund)
				break
			}
		}
	}
	impacted := make([]string, 0, len(seenFunds))
	for fund := range seenFunds {
		impacted = append(impacted, fund)
	}
	sort.Strings(impacted)
	return impacted
}

func referenceQuoteSession(symbol string) string {
	lower := strings.ToLower(strings.TrimSpace(symbol))
	if lower == "" {
		return "reference_minute"
	}
	if strings.HasPrefix(lower, "hf_") || strings.HasPrefix(lower, "nf_") {
		return "global_future"
	}
	if len(symbol) == 5 && allDigits(symbol) {
		return "hk_regular"
	}
	if strings.HasPrefix(lower, "znb_") {
		return "global_index"
	}
	if strings.HasPrefix(lower, "fx_") {
		return "fx_weekday"
	}
	return "reference_minute"
}

func parseMinuteTimeOrNow(value string, fallback time.Time) time.Time {
	parsed, err := parseMinuteHistoryMinute(value)
	if err != nil {
		return fallback
	}
	return parsed
}

func (r *MinuteBackfillResult) addWarning(warning string) {
	if warning == "" || len(r.Warnings) >= 50 {
		return
	}
	r.Warnings = append(r.Warnings, warning)
}

func allDigits(value string) bool {
	for _, char := range value {
		if char < '0' || char > '9' {
			return false
		}
	}
	return value != ""
}
