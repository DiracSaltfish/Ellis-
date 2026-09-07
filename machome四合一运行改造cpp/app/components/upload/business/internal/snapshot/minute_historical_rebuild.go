package snapshot

import (
	"fmt"
	"strings"
	"time"

	"newnavnav/internal/domain"
)

func (s *Service) RebuildHistoricalMinuteBars(source string, fundBars []MinuteBar, referenceBars []MinuteBar) (MinuteBackfillResult, error) {
	source = strings.TrimSpace(source)
	if source == "" {
		source = "historical_minute_rebuild"
	}
	result := MinuteBackfillResult{Source: source}
	if s.minuteHistory == nil {
		result.Warnings = append(result.Warnings, "minute history store is disabled")
		return result, nil
	}
	if len(fundBars) == 0 {
		result.Warnings = append(result.Warnings, "fund rows are required")
		return result, nil
	}

	fundSymbols := map[string]bool{}
	for _, symbol := range domain.AllSymbols() {
		fundSymbols[symbol] = true
	}

	day := ""
	fundsByMinute := make(map[string]map[string]normalizedMinuteBar)
	seenFunds := map[string]bool{}
	for index, bar := range fundBars {
		symbol := normalizeMinuteBarSymbol(bar.Symbol)
		if symbol == "" {
			result.Skipped++
			result.addWarning(fmt.Sprintf("fund row %d missing symbol", index+1))
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
		rowDay := minuteHistoryDayFromMinute(minute)
		if day == "" {
			day = rowDay
		}
		if rowDay == "" || rowDay != day {
			result.Skipped++
			result.addWarning(fmt.Sprintf("%s %s is outside rebuild date %s", symbol, minute, day))
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
		if fundsByMinute[minute] == nil {
			fundsByMinute[minute] = make(map[string]normalizedMinuteBar)
		}
		fundsByMinute[minute][symbol] = normalizedMinuteBar{
			Symbol:    symbol,
			Minute:    minute,
			Timestamp: timestamp,
			Price:     price,
			Source:    rowSource,
		}
		seenFunds[symbol] = true
	}
	if len(fundsByMinute) == 0 || day == "" {
		result.Symbols = sortedMinuteBackfillSymbols(seenFunds)
		return result, nil
	}

	referencesByMinute := make(map[string]map[string]domain.Quote)
	seenReferences := map[string]bool{}
	for index, bar := range referenceBars {
		symbol := normalizeReferenceMinuteSymbol(bar.Symbol)
		if symbol == "" {
			result.Skipped++
			result.addWarning(fmt.Sprintf("reference row %d missing symbol", index+1))
			continue
		}
		minute, timestamp, ok := normalizeMinuteBarTime(bar)
		if !ok {
			result.Skipped++
			result.addWarning(fmt.Sprintf("%s missing valid minute", symbol))
			continue
		}
		if minuteHistoryDayFromMinute(minute) != day {
			result.Skipped++
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
		minuteTime, err := parseMinuteHistoryMinute(timestamp)
		if err != nil {
			minuteTime, _ = parseMinuteHistoryMinute(minute)
		}
		if referencesByMinute[minute] == nil {
			referencesByMinute[minute] = make(map[string]domain.Quote)
		}
		referencesByMinute[minute][symbol] = domain.Quote{
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
			RealtimeStatus: "historical_reference",
			FetchedAt:      minuteTime,
		}
		seenReferences[symbol] = true
	}
	if len(referenceBars) == 0 {
		result.addWarning("reference rows are empty; only funds that do not require minute references can be rebuilt")
	}

	baseNow := s.nowTime()
	baseQuotes, valuationData := s.minuteBackfillBaseQuotes(baseNow)
	requiredByFund := make(map[string][]string, len(seenFunds))
	requiredReferenceSet := make(map[string]bool)
	for fund := range seenFunds {
		required := historicalMinuteRequiredReferences(valuationData, fund)
		requiredByFund[fund] = required
		for _, symbol := range required {
			requiredReferenceSet[symbol] = true
		}
	}
	for symbol := range seenReferences {
		delete(baseQuotes, symbol)
	}
	for symbol := range requiredReferenceSet {
		delete(baseQuotes, symbol)
	}

	points := make([]MinuteHistoryPoint, 0, len(fundBars))
	for minute, fundRows := range fundsByMinute {
		minuteTime, err := parseMinuteHistoryMinute(minute)
		if err != nil {
			result.Skipped += len(fundRows)
			continue
		}
		quotes := make(map[string]domain.Quote, len(baseQuotes)+len(referencesByMinute[minute])+len(fundRows))
		for symbol, quote := range baseQuotes {
			quotes[symbol] = quote
		}
		for symbol, quote := range referencesByMinute[minute] {
			quotes[symbol] = quote
		}
		for _, bar := range fundRows {
			required := requiredByFund[bar.Symbol]
			if missing := missingHistoricalReferences(required, referencesByMinute[minute]); len(missing) > 0 {
				result.Skipped++
				result.addWarning(fmt.Sprintf("%s %s missing reference %s", bar.Symbol, minute, strings.Join(missing, ",")))
				continue
			}

			fundQuote := quotes[bar.Symbol]
			fundQuote.Symbol = bar.Symbol
			if strings.TrimSpace(fundQuote.Name) == "" {
				fundQuote.Name = bar.Symbol
			}
			fundQuote.Price = bar.Price
			fundQuote.Source = bar.Source
			fundQuote.SourceSymbol = bar.Symbol
			fundQuote.QuoteDate = minute[:10]
			fundQuote.QuoteTime = minute[11:] + ":00"
			fundQuote.IsRealtime = true
			fundQuote.RealtimeStatus = "historical_backfilled"
			fundQuote.FetchedAt = parseMinuteTimeOrNow(bar.Timestamp, minuteTime)
			quotes[bar.Symbol] = fundQuote

			estimate := s.engine.Estimate(fundQuote, quotes, valuationData, minuteTime)
			estimatedNAV := estimate.FairEst
			premiumPct := estimate.FairPremium
			if estimate.RealtimeEst != nil && *estimate.RealtimeEst > 0 {
				estimatedNAV = *estimate.RealtimeEst
				if estimate.RealtimePremium != nil {
					premiumPct = *estimate.RealtimePremium
				} else {
					premiumPct = (bar.Price/estimatedNAV - 1) * 100
				}
			}
			if estimatedNAV <= 0 {
				result.Skipped++
				result.addWarning(fmt.Sprintf("%s %s has no valid estimate", bar.Symbol, minute))
				continue
			}
			points = append(points, MinuteHistoryPoint{
				Symbol:          bar.Symbol,
				Minute:          minute,
				Timestamp:       parseMinuteTimeOrNow(bar.Timestamp, minuteTime).Format(time.RFC3339Nano),
				MarketPrice:     bar.Price,
				EstimatedNAV:    estimatedNAV,
				PremiumPct:      premiumPct,
				OfficialEST:     estimate.OfficialEst,
				FairEST:         estimate.FairEst,
				RealtimeEST:     estimate.RealtimeEst,
				EffectiveRatio:  estimate.EffectiveRatio,
				QuoteSource:     publicSourceLabel(bar.Source),
				QuoteStatus:     "historical_backfilled",
				ModelVersion:    estimate.ModelVersion,
				ReferenceSymbol: estimate.ReferenceSymbol,
				UploadSource:    source,
			})
		}
	}

	if err := s.minuteHistory.ReplaceDaySymbols(day, sortedMinuteBackfillSymbols(seenFunds), points); err != nil {
		return result, err
	}

	result.OK = len(points) > 0
	result.Accepted = len(points)
	result.Symbols = sortedMinuteBackfillSymbols(seenFunds)
	return result, nil
}

func historicalMinuteRequiredReferences(data domain.ValuationData, fund string) []string {
	fund = strings.ToUpper(strings.TrimSpace(fund))
	seen := map[string]bool{}
	add := func(symbol string) {
		symbol = normalizeReferenceMinuteSymbol(symbol)
		if symbol != "" {
			seen[symbol] = true
		}
	}
	if strategy, ok := domain.WeightedAnchorStrategyForFund(fund); ok {
		add(strategy.ReferenceSymbol)
		return sortedMinuteBackfillSymbols(seen)
	}
	if strategy, ok := domain.CommodityBasketStrategyForFund(fund); ok {
		for _, leg := range strategy.Legs {
			add(leg.Symbol)
		}
		return sortedMinuteBackfillSymbols(seen)
	}
	if strategy, ok := domain.SingleCommodityFutureStrategyForFund(fund); ok {
		add(strategy.ReferenceSymbol)
		return sortedMinuteBackfillSymbols(seen)
	}
	for _, pair := range data.FundPairs[fund] {
		add(pair.PairSymbol)
	}
	if len(seen) > 0 {
		return sortedMinuteBackfillSymbols(seen)
	}

	currentHoldingDate := strings.TrimSpace(data.CurrentHoldingDates[fund].Date)
	if currentHoldingDate == "" {
		return nil
	}
	for _, holding := range data.Holdings[fund] {
		if holding.HoldingDate != currentHoldingDate || holding.Ratio <= 0 {
			continue
		}
		if reference, ok := domain.CommodityFutureReferenceForHolding(holding.HoldingSymbol); ok {
			add(reference.Symbol)
			continue
		}
		add(holding.HoldingSymbol)
	}
	return sortedMinuteBackfillSymbols(seen)
}

func missingHistoricalReferences(required []string, quotes map[string]domain.Quote) []string {
	if len(required) == 0 {
		return nil
	}
	missing := make([]string, 0)
	for _, symbol := range required {
		quote, ok := quotes[symbol]
		if !ok || quote.Price <= 0 {
			missing = append(missing, symbol)
		}
	}
	return missing
}
