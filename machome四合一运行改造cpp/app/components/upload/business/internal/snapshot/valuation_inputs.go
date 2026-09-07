package snapshot

import (
	"fmt"
	"sort"
	"strings"
	"time"

	"newnavnav/internal/domain"
	"newnavnav/internal/valuation"
)

func buildValuationInputs(fund string, fundQuote domain.Quote, estimate *domain.EstimateRow, quotes map[string]domain.Quote, data domain.ValuationData, now time.Time) []domain.ValuationInput {
	inputs := []domain.ValuationInput{
		quoteInput("交易标的", fundQuote, fundMarket(fundQuote), "", 0, positionOf(data, fund), 0, "", 0, "", "", "", true, ""),
	}

	fallbackDate := ""
	if holdingDate, ok := data.CurrentHoldingDates[fund]; ok {
		fallbackDate = holdingDate.Date
	}
	baseNAV, hasBaseNAV := valuation.ValuationBaseNAV(data, fund, fallbackDate, now)
	baseDate := ""
	if hasBaseNAV {
		baseDate = baseNAV.Date
	}
	if baseNAV.NAV > 0 {
		inputs = append(inputs, domain.ValuationInput{
			Role:       "净值基准",
			Symbol:     fund,
			Name:       fundQuote.Name,
			Market:     "cn",
			Timezone:   timezoneForMarket("cn"),
			Position:   positionOf(data, fund),
			BaseDate:   baseDate,
			BasePrice:  baseNAV.NAV,
			BaseSource: baseNAV.Source,
			Used:       true,
			Note:       baseNAV.Confidence,
		})
	}
	if estimate == nil {
		return inputs
	}

	switch {
	case strings.HasPrefix(estimate.ModelVersion, "v1.weighted_anchor"):
		if strategy, ok := domain.WeightedAnchorStrategyForFund(fund); ok {
			return append(inputs, weightedAnchorInputs(baseDate, quotes, data, strategy)...)
		}
		return inputs
	case strings.HasPrefix(estimate.ModelVersion, "v1.commodity_basket"):
		if strategy, ok := domain.CommodityBasketStrategyForFund(fund); ok {
			return append(inputs, commodityBasketInputs(baseDate, quotes, data, strategy)...)
		}
		return inputs
	case strings.HasPrefix(estimate.ModelVersion, "v1.commodity_futures"):
		if strategy, ok := domain.SingleCommodityFutureStrategyForFund(fund); ok {
			return append(inputs, singleCommodityFutureInputs(fund, baseDate, quotes, data, strategy)...)
		}
		return append(inputs, holdingInputs(fund, baseDate, quotes, data, true)...)
	case strings.HasPrefix(estimate.ModelVersion, "v1.holdings"):
		return append(inputs, holdingInputs(fund, baseDate, quotes, data, false)...)
	case strings.HasPrefix(estimate.ModelVersion, "v1.fundpair"):
		return append(inputs, fundPairInputs(fund, estimate.ReferenceSymbol, baseDate, quotes, data)...)
	default:
		return inputs
	}
}

func commodityBasketInputs(
	baseDate string,
	quotes map[string]domain.Quote,
	data domain.ValuationData,
	strategy domain.CommodityBasketStrategy,
) []domain.ValuationInput {
	inputs := make([]domain.ValuationInput, 0, len(strategy.Legs))
	for _, leg := range strategy.Legs {
		if leg.Symbol == "" || leg.Weight <= 0 {
			continue
		}
		basePrice, baseSource := dailyPriceInfo(data, leg.Symbol, baseDate)
		quote, hasQuote := quoteForSymbol(quotes, leg.Symbol)
		used := hasQuote && quote.Price > 0 && basePrice > 0 && !isDemoQuote(quote)
		note := fmt.Sprintf("%s 估值腿", leg.Label)
		if len(leg.Components) > 0 {
			note += "；对应 " + strings.Join(leg.Components, " / ")
		}
		if !used {
			note = strings.TrimSpace(note + " " + missingInputReason(hasQuote, quote, basePrice))
		}
		input := quoteInput(
			"估值腿",
			quote,
			marketForSymbol(leg.Symbol),
			baseDate,
			basePrice,
			0,
			leg.Weight*100,
			baseSource,
			0,
			"USD",
			"",
			"",
			used,
			note,
		)
		input.Symbol = leg.Symbol
		if input.Name == "" {
			input.Name = leg.Label
		}
		inputs = append(inputs, input)
	}
	return inputs
}

func weightedAnchorInputs(
	baseDate string,
	quotes map[string]domain.Quote,
	data domain.ValuationData,
	strategy domain.WeightedAnchorStrategy,
) []domain.ValuationInput {
	quote, hasQuote := quoteForSymbol(quotes, strategy.ReferenceSymbol)
	inputs := make([]domain.ValuationInput, 0, len(strategy.Points)+1)
	anchorSet, anchorWarnings, hasAnchorSet := valuation.ResolveWeightedAnchorSet(data, strategy, baseDate)
	baseSource := "valuation_anchor_set"
	if hasAnchorSet && len(anchorSet.Points) > 0 && strings.TrimSpace(anchorSet.Points[0].Source) != "" {
		baseSource = anchorSet.Points[0].Source
	}
	basePrice := 0.0
	if hasAnchorSet {
		basePrice = anchorSet.WeightedPrice
	}
	used := hasQuote && quote.Price > 0 && basePrice > 0 && !isDemoQuote(quote)
	note := fmt.Sprintf("%s 多时区加权锚点估值", strategy.Label)
	if hasAnchorSet {
		note = fmt.Sprintf(
			"%s 多时区加权锚点估值，覆盖权重 %.2f/%.2f",
			strategy.Label,
			anchorSet.CoverageWeight,
			anchorSet.RequiredWeight,
		)
		if len(anchorWarnings) > 0 {
			note += "；" + strings.Join(anchorWarnings, "; ")
		}
	}
	if !used {
		note = strings.TrimSpace(note + " " + missingInputReason(hasQuote, quote, basePrice))
	}
	input := quoteInput(
		"连续价格尺度",
		quote,
		marketForSymbol(strategy.ReferenceSymbol),
		baseDate,
		basePrice,
		strategy.InvestmentRatio,
		strategy.InvestmentRatio*100,
		baseSource,
		0,
		"USD",
		"",
		"",
		used,
		note,
	)
	input.Symbol = strategy.ReferenceSymbol
	if input.Name == "" {
		input.Name = strategy.Label
	}
	inputs = append(inputs, input)

	if !hasAnchorSet {
		return inputs
	}
	pointByKey := make(map[string]domain.ValuationAnchorPrice, len(anchorSet.Points))
	for _, point := range anchorSet.Points {
		pointByKey[point.AnchorKey] = point
	}
	for _, rule := range strategy.Points {
		point, ok := pointByKey[rule.Key]
		if !ok {
			continue
		}
		pointNote := rule.Label
		if !point.TargetAt.IsZero() {
			pointNote = fmt.Sprintf("%s %s", rule.Label, point.TargetAt.In(time.Local).Format(time.RFC3339))
		}
		var targetAt, observedAt *time.Time
		if !point.TargetAt.IsZero() {
			value := point.TargetAt
			targetAt = &value
		}
		if !point.ObservedAt.IsZero() {
			value := point.ObservedAt
			observedAt = &value
		}
		inputs = append(inputs, domain.ValuationInput{
			Role:          "估值锚点",
			Symbol:        strategy.ReferenceSymbol,
			Name:          rule.Label,
			Market:        marketForSymbol(strategy.ReferenceSymbol),
			Timezone:      point.TargetTimezone,
			WeightRatio:   point.Weight * 100,
			BaseDate:      baseDate,
			BasePrice:     point.Price,
			BaseSource:    point.Source,
			TargetAt:      targetAt,
			ObservedAt:    observedAt,
			CaptureStatus: point.CaptureStatus,
			Used:          point.Price > 0,
			Note:          pointNote,
		})
	}
	return inputs
}

func singleCommodityFutureInputs(
	fund string,
	baseDate string,
	quotes map[string]domain.Quote,
	data domain.ValuationData,
	strategy domain.SingleCommodityFutureStrategy,
) []domain.ValuationInput {
	quote, hasQuote := quoteForSymbol(quotes, strategy.ReferenceSymbol)
	basePrice, baseSource := dailyPriceInfo(data, strategy.ReferenceSymbol, baseDate)
	fxAdjust := 0.0
	for _, holding := range data.Holdings[fund] {
		if holding.HoldingDate != data.CurrentHoldingDates[fund].Date {
			continue
		}
		if strategy.FXHoldingSymbol != "" && !strings.EqualFold(holding.HoldingSymbol, strategy.FXHoldingSymbol) {
			continue
		}
		if holding.FXAdjust > 0 {
			fxAdjust = holding.FXAdjust
			break
		}
	}
	used := hasQuote && quote.Price > 0 && basePrice > 0 && !isDemoQuote(quote)
	note := fmt.Sprintf("%s 单锚点期货估值", strategy.Label)
	if !used {
		note = strings.TrimSpace(note + " " + missingInputReason(hasQuote, quote, basePrice))
	}
	input := quoteInput(
		"期货基准",
		quote,
		marketForSymbol(strategy.ReferenceSymbol),
		baseDate,
		basePrice,
		strategy.InvestmentRatio,
		strategy.InvestmentRatio*100,
		baseSource,
		fxAdjust,
		"USD",
		data.CurrentHoldingDates[fund].Date,
		"",
		used,
		note,
	)
	input.Symbol = strategy.ReferenceSymbol
	if input.Name == "" {
		input.Name = strategy.Label
	}
	return []domain.ValuationInput{input}
}

func holdingInputs(fund string, baseDate string, quotes map[string]domain.Quote, data domain.ValuationData, includeCommodityReplacement bool) []domain.ValuationInput {
	holdingDate := data.CurrentHoldingDates[fund]
	holdings := append([]domain.Holding(nil), data.Holdings[fund]...)
	sort.SliceStable(holdings, func(i, j int) bool {
		if holdings[i].Ratio == holdings[j].Ratio {
			return holdings[i].HoldingSymbol < holdings[j].HoldingSymbol
		}
		return holdings[i].Ratio > holdings[j].Ratio
	})

	inputs := make([]domain.ValuationInput, 0, len(holdings))
	for _, holding := range holdings {
		if holding.HoldingDate != holdingDate.Date || holding.Ratio <= 0 {
			continue
		}
		inputs = append(inputs, holdingInput("持仓标的", holding, holding.HoldingSymbol, "", baseDate, quotes, data))
		if includeCommodityReplacement {
			if reference, ok := domain.CommodityFutureReferenceForHolding(holding.HoldingSymbol); ok {
				inputs = append(inputs, holdingInput("期货替代", holding, reference.Symbol, holding.HoldingSymbol, baseDate, quotes, data))
			}
		}
	}
	return inputs
}

func holdingInput(role string, holding domain.Holding, symbol string, referenceSymbol string, baseDate string, quotes map[string]domain.Quote, data domain.ValuationData) domain.ValuationInput {
	market := marketForSymbol(symbol)
	basePrice, baseSource := dailyPriceInfo(data, symbol, baseDate)
	quote, hasQuote := quoteForSymbol(quotes, symbol)
	name := holding.HoldingName
	if hasQuote && quote.Name != "" {
		name = quote.Name
	}
	used := hasQuote && quote.Price > 0 && basePrice > 0 && !isDemoQuote(quote)
	note := ""
	if referenceSymbol != "" {
		note = fmt.Sprintf("替代 %s 的实时部分", referenceSymbol)
	}
	if !used {
		note = strings.TrimSpace(note + " " + missingInputReason(hasQuote, quote, basePrice))
	}
	input := quoteInput(role, quote, market, baseDate, basePrice, 0, holding.Ratio, baseSource, holding.FXAdjust, holding.Currency, holding.HoldingDate, referenceSymbol, used, note)
	input.Symbol = symbol
	input.Name = name
	return input
}

func fundPairInputs(fund string, referenceSymbol string, baseDate string, quotes map[string]domain.Quote, data domain.ValuationData) []domain.ValuationInput {
	pairs := data.FundPairs[fund]
	if referenceSymbol != "" {
		for _, pair := range pairs {
			if strings.EqualFold(pair.PairSymbol, referenceSymbol) {
				return []domain.ValuationInput{fundPairInput(pair, baseDate, quotes, data, fund)}
			}
		}
	}
	for _, pair := range pairs {
		if quote, ok := quoteForSymbol(quotes, pair.PairSymbol); ok && quote.Price > 0 && !isDemoQuote(quote) {
			return []domain.ValuationInput{fundPairInput(pair, baseDate, quotes, data, fund)}
		}
	}
	return nil
}

func fundPairInput(pair domain.FundPair, baseDate string, quotes map[string]domain.Quote, data domain.ValuationData, fund string) domain.ValuationInput {
	symbol := pair.PairSymbol
	market := marketForSymbol(symbol)
	basePrice, baseSource := dailyPriceInfo(data, symbol, baseDate)
	if basePrice <= 0 {
		if nav, ok := netValueInfo(data, symbol, baseDate); ok {
			basePrice = nav.NAV
			baseSource = nav.Source
		}
	}
	quote, hasQuote := quoteForSymbol(quotes, symbol)
	used := hasQuote && quote.Price > 0 && !isDemoQuote(quote)
	note := pair.PairType
	if !used {
		note = strings.TrimSpace(note + " " + missingInputReason(hasQuote, quote, basePrice))
	}
	input := quoteInput("配对标的", quote, market, baseDate, basePrice, positionOf(data, fund), 0, baseSource, 0, "", "", "", used, note)
	input.Symbol = symbol
	if input.Name == "" {
		input.Name = pair.PairSymbol
	}
	return input
}

func quoteInput(role string, quote domain.Quote, market string, baseDate string, basePrice float64, position float64, weightRatio float64, baseSource string, fxAdjust float64, currency string, holdingDate string, referenceSymbol string, used bool, note string) domain.ValuationInput {
	if market == "" {
		market = fundMarket(quote)
	}
	official := quote.Price
	source := strings.ToLower(strings.TrimSpace(quote.Source))
	session := strings.ToLower(strings.TrimSpace(quote.QuoteSession))
	if quote.Source == "sina_us_after_hours" && quote.PrevClose > 0 {
		official = quote.PrevClose
	} else if (strings.HasPrefix(source, "ibkr_us_") ||
		strings.HasPrefix(source, "upload:home-mac-ib") ||
		strings.Contains(session, "us_overnight") ||
		strings.Contains(session, "us_extended")) && quote.PrevClose > 0 {
		official = quote.PrevClose
	}
	var fetchedAt *time.Time
	if !quote.FetchedAt.IsZero() && quote.Source != "missing" {
		fetchedAt = &quote.FetchedAt
	}
	return domain.ValuationInput{
		Role:            role,
		Symbol:          quote.Symbol,
		Name:            quote.Name,
		Market:          market,
		Timezone:        timezoneForMarket(market),
		WeightRatio:     weightRatio,
		Position:        position,
		HoldingDate:     holdingDate,
		BaseDate:        baseDate,
		BasePrice:       basePrice,
		BaseSource:      publicSourceLabel(baseSource),
		CurrentPrice:    quote.Price,
		OfficialPrice:   official,
		ChangePct:       quote.ChangePct,
		QuoteDate:       quote.QuoteDate,
		QuoteTime:       quote.QuoteTime,
		FetchedAt:       fetchedAt,
		Source:          publicSourceLabel(quote.Source),
		SourceSymbol:    quote.SourceSymbol,
		QuoteSession:    quote.QuoteSession,
		RealtimeStatus:  quote.RealtimeStatus,
		StaleReason:     quote.StaleReason,
		FXAdjust:        fxAdjust,
		Currency:        currency,
		ReferenceSymbol: referenceSymbol,
		Used:            used,
		Note:            strings.TrimSpace(note),
	}
}

func dailyPriceInfo(data domain.ValuationData, symbol string, date string) (float64, string) {
	byDate := data.DailyPricesByDate[symbol]
	if byDate == nil {
		return 0, ""
	}
	price, ok := byDate[date]
	if !ok {
		return 0, ""
	}
	if price.AdjClose > 0 {
		return price.AdjClose, price.Source
	}
	return price.Close, price.Source
}

func netValueInfo(data domain.ValuationData, symbol string, date string) (domain.NetValue, bool) {
	byDate := data.NetValuesByDate[symbol]
	if byDate == nil {
		return domain.NetValue{}, false
	}
	nav, ok := byDate[date]
	return nav, ok && nav.NAV > 0
}

func positionOf(data domain.ValuationData, symbol string) float64 {
	if strategy, ok := domain.WeightedAnchorStrategyForFund(symbol); ok && strategy.InvestmentRatio > 0 {
		return strategy.InvestmentRatio
	}
	if strategy, ok := domain.CommodityBasketStrategyForFund(symbol); ok && strategy.InvestmentRatio() > 0 {
		return strategy.InvestmentRatio()
	}
	if strategy, ok := domain.SingleCommodityFutureStrategyForFund(symbol); ok && strategy.InvestmentRatio > 0 {
		return strategy.InvestmentRatio
	}
	if value, ok := data.Positions[symbol]; ok && value > 0 {
		return value
	}
	return 1
}

func isDemoQuote(quote domain.Quote) bool {
	return quote.Source == "demo" || quote.Error == "demo fallback"
}

func missingInputReason(hasQuote bool, quote domain.Quote, basePrice float64) string {
	switch {
	case !hasQuote || quote.Symbol == "":
		return "缺少当前行情"
	case quote.Price <= 0:
		return "当前价格无效"
	case isDemoQuote(quote):
		return "demo 行情未使用"
	case basePrice <= 0:
		return "缺少基准价"
	default:
		return ""
	}
}

func fundMarket(quote domain.Quote) string {
	if quote.Symbol != "" {
		if market := marketForSymbol(quote.Symbol); market != "" {
			return market
		}
	}
	source := strings.ToLower(quote.Source)
	session := strings.ToLower(quote.QuoteSession)
	switch {
	case strings.Contains(source, "hk"):
		return "hk"
	case strings.Contains(source, "hf"), strings.Contains(session, "future"):
		return "us_commodity_futures"
	case strings.Contains(source, "us"):
		return "us"
	case strings.Contains(source, "znb"):
		return "jp"
	default:
		return "cn"
	}
}

func marketForSymbol(symbol string) string {
	symbol = strings.TrimSpace(symbol)
	if market := basePriceMarket(symbol); market != "" {
		return market
	}
	upper := strings.ToUpper(symbol)
	switch {
	case strings.HasSuffix(upper, "-EU"):
		return "eu"
	case strings.HasSuffix(upper, "-JP"):
		return "jp"
	case strings.HasSuffix(upper, "-HK"):
		return "hk"
	default:
		return ""
	}
}

func timezoneForMarket(market string) string {
	switch market {
	case "cn":
		return "Asia/Shanghai"
	case "hk":
		return "Asia/Hong_Kong"
	case "jp":
		return "Asia/Tokyo"
	case "eu":
		return "Europe/Berlin"
	case "us", "us_commodity_futures":
		return "America/New_York"
	default:
		return ""
	}
}
