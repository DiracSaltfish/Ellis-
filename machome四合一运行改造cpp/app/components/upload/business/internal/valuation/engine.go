package valuation

import (
	"fmt"
	"math"
	"strings"
	"time"

	"newnavnav/internal/domain"
)

type Engine struct{}

const weightedAnchorCarryForwardDays = 7

func NewEngine() *Engine {
	return &Engine{}
}

func (e *Engine) Estimate(quote domain.Quote, quotes map[string]domain.Quote, data domain.ValuationData, now time.Time) domain.EstimateRow {
	if row, ok := e.estimateFromWeightedAnchor(quote, quotes, data, now); ok {
		return row
	}
	if row, ok := e.estimateFromCommodityBasket(quote, quotes, data, now); ok {
		return row
	}
	if _, ok := domain.CommodityBasketStrategyForFund(quote.Symbol); ok {
		if row, ok := e.estimateFromLatestNAV(quote, data, now); ok {
			return row
		}
		return estimateFromQuote(quote, now, "v0.quote_passthrough", "组合商品估值基准数据不完整，暂用行情价占位")
	}
	if row, ok := e.estimateFromCommodityFutures(quote, quotes, data, now); ok {
		return row
	}
	if row, ok := e.estimateFromHoldings(quote, quotes, data, now); ok {
		return row
	}
	if row, ok := e.estimateFromFundPair(quote, quotes, data, now); ok {
		return row
	}
	if row, ok := e.estimateFromLatestNAV(quote, data, now); ok {
		return row
	}
	return estimateFromQuote(quote, now, "v0.quote_passthrough", "没有可用 fundpair、净值或持仓基准数据，暂用行情价占位")
}

func (e *Engine) estimateFromWeightedAnchor(quote domain.Quote, quotes map[string]domain.Quote, data domain.ValuationData, now time.Time) (domain.EstimateRow, bool) {
	strategy, ok := domain.WeightedAnchorStrategyForFund(quote.Symbol)
	if !ok {
		return domain.EstimateRow{}, false
	}
	baseNAV, ok := ValuationNetValue(data, quote.Symbol, now)
	if !ok || baseNAV.NAV <= 0 || baseNAV.Date == "" {
		return domain.EstimateRow{}, false
	}
	referenceQuote, ok := quoteForSymbol(quotes, strategy.ReferenceSymbol)
	if !ok || referenceQuote.Price <= 0 || isDemoQuote(referenceQuote) {
		return domain.EstimateRow{}, false
	}
	anchorSet, anchorWarnings, ok := ResolveWeightedAnchorSet(data, strategy, baseNAV.Date)
	if !ok || anchorSet.WeightedPrice <= 0 || anchorSet.CoverageWeight+1e-9 < anchorSet.RequiredWeight {
		return domain.EstimateRow{}, false
	}
	currentFX, baseFX, fxPair, ok := fxRatesForWeightedAnchor(data, strategy, baseNAV.Date, now)
	if !ok || currentFX <= 0 || baseFX <= 0 {
		return domain.EstimateRow{}, false
	}
	investmentRatio, _, _ := domain.EffectiveRatio(data, quote.Symbol)
	if investmentRatio <= 0 {
		investmentRatio = strategy.InvestmentRatio
	}
	if investmentRatio <= 0 {
		investmentRatio = 1
	}
	staticRatio := 1 - investmentRatio
	fxRatio := currentFX / baseFX
	referencePrice := referenceQuote.Price
	fair := baseNAV.NAV * (staticRatio + investmentRatio*(referencePrice/anchorSet.WeightedPrice)*fxRatio)
	var realtime *float64
	if domain.IsManualRealtimeFund(quote.Symbol) {
		realtime = &fair
	}
	note := fmt.Sprintf(
		"净值基准日 %s，使用 %s 多时区加权锚点估值，基准 %.6f，%s %.6f/%.6f，覆盖权重 %.2f/%.2f",
		baseNAV.Date,
		strategy.Label,
		anchorSet.WeightedPrice,
		fxPair,
		baseFX,
		currentFX,
		anchorSet.CoverageWeight,
		anchorSet.RequiredWeight,
	)
	if staticRatio > 1e-9 {
		note = fmt.Sprintf(
			"净值基准日 %s，使用 %s 多时区加权锚点估值，基准 %.6f，%s %.6f/%.6f，%s有效仓位 %.2f%%，静态资产 %.2f%%，覆盖权重 %.2f/%.2f",
			baseNAV.Date,
			strategy.Label,
			anchorSet.WeightedPrice,
			fxPair,
			baseFX,
			currentFX,
			strategy.Label,
			investmentRatio*100,
			staticRatio*100,
			anchorSet.CoverageWeight,
			anchorSet.RequiredWeight,
		)
	}
	row := buildRow(quote, baseNAV.NAV, fair, realtime, now, "v1.weighted_anchor."+strings.ToLower(strategy.Label), note)
	row.EffectiveRatio = round6(investmentRatio)
	row.ReferenceSymbol = strategy.ReferenceSymbol
	if len(anchorWarnings) > 0 {
		row.Note += "；" + strings.Join(anchorWarnings, "; ")
	}
	return row, true
}

func (e *Engine) estimateFromCommodityBasket(quote domain.Quote, quotes map[string]domain.Quote, data domain.ValuationData, now time.Time) (domain.EstimateRow, bool) {
	strategy, ok := domain.CommodityBasketStrategyForFund(quote.Symbol)
	if !ok {
		return domain.EstimateRow{}, false
	}
	baseNAV, ok := ValuationNetValue(data, quote.Symbol, now)
	if !ok || baseNAV.NAV <= 0 || baseNAV.Date == "" {
		return domain.EstimateRow{}, false
	}

	defaultInvestmentRatio := strategy.InvestmentRatio()
	investmentRatio, _, _ := domain.EffectiveRatio(data, quote.Symbol)
	if investmentRatio <= 0 {
		investmentRatio = defaultInvestmentRatio
	}
	if investmentRatio <= 0 {
		return domain.EstimateRow{}, false
	}
	staticRatio := strategy.StaticRatio()
	if staticRatio = 1 - investmentRatio; staticRatio < 0 {
		staticRatio = 0
	}
	weightScale := 1.0
	if defaultInvestmentRatio > 0 {
		weightScale = investmentRatio / defaultInvestmentRatio
	}
	official := baseNAV.NAV * staticRatio
	fair := baseNAV.NAV * staticRatio
	usedLegs := 0
	usedWeight := 0.0
	labels := make([]string, 0, len(strategy.Legs))
	fxNotes := make([]string, 0, len(strategy.Legs))

	for _, leg := range strategy.Legs {
		if leg.Weight <= 0 || leg.Symbol == "" {
			continue
		}
		currentQuote, hasQuote := quoteForSymbol(quotes, leg.Symbol)
		basePrice, hasBase := dailyPriceAt(data, leg.Symbol, baseNAV.Date)
		currentFX, baseFX, fxPair, hasFX := fxRatesForPair(data, leg.FXPair, baseNAV.Date, now)
		if !hasQuote || currentQuote.Price <= 0 || !hasBase || basePrice <= 0 || isDemoQuote(currentQuote) || !hasFX || currentFX <= 0 || baseFX <= 0 {
			return domain.EstimateRow{}, false
		}
		fxRatio := currentFX / baseFX
		effectiveWeight := leg.Weight * weightScale
		official += baseNAV.NAV * effectiveWeight * (officialQuotePrice(currentQuote) / basePrice) * fxRatio
		fair += baseNAV.NAV * effectiveWeight * (currentQuote.Price / basePrice) * fxRatio
		usedLegs++
		usedWeight += effectiveWeight
		labels = append(labels, leg.Label)
		if fxPair != "" {
			fxNotes = append(fxNotes, fmt.Sprintf("%s %s %.6f/%.6f", leg.Label, fxPair, baseFX, currentFX))
		}
	}
	if usedLegs == 0 || usedWeight+1e-9 < investmentRatio {
		return domain.EstimateRow{}, false
	}

	var realtime *float64
	if domain.IsManualRealtimeFund(quote.Symbol) {
		realtime = &fair
	}
	note := fmt.Sprintf(
		"净值基准日 %s，使用 %s 组合估值，商品有效仓位 %.2f%%，静态资产 %.2f%%，使用 %d/%d 条估值腿",
		baseNAV.Date,
		strings.Join(labels, "/"),
		investmentRatio*100,
		staticRatio*100,
		usedLegs,
		len(strategy.Legs),
	)
	if len(fxNotes) > 0 {
		note += "；汇率 " + strings.Join(fxNotes, " / ")
	}
	row := buildRow(quote, official, fair, realtime, now, "v1.commodity_basket.cn", note)
	row.EffectiveRatio = round6(investmentRatio)
	return row, true
}

func (e *Engine) estimateFromCommodityFutures(quote domain.Quote, quotes map[string]domain.Quote, data domain.ValuationData, now time.Time) (domain.EstimateRow, bool) {
	holdingDate, ok := data.CurrentHoldingDates[quote.Symbol]
	if !ok || holdingDate.Date == "" {
		return domain.EstimateRow{}, false
	}
	holdings := data.Holdings[quote.Symbol]
	if len(holdings) == 0 {
		return domain.EstimateRow{}, false
	}
	baseNAV, ok := ValuationBaseNAV(data, quote.Symbol, holdingDate.Date, now)
	if !ok || baseNAV.NAV <= 0 {
		return domain.EstimateRow{}, false
	}
	baseDate := baseNAV.Date
	if strategy, ok := domain.SingleCommodityFutureStrategyForFund(quote.Symbol); ok {
		return e.estimateFromSingleCommodityFuture(quote, quotes, data, now, holdingDate.Date, holdings, baseNAV, baseDate, strategy)
	}

	position := positionOf(data, quote.Symbol)
	officialWeightedRatio := 0.0
	fairWeightedRatio := 0.0
	totalRatio := 0.0
	used := 0
	replacementUsed := 0
	references := []string{}
	seenReferences := map[string]bool{}

	for _, holding := range holdings {
		if holding.HoldingDate != holdingDate.Date || holding.Ratio <= 0 {
			continue
		}
		if reference, ok := domain.CommodityFutureReferenceForHolding(holding.HoldingSymbol); ok {
			originalQuote, hasOriginal := quotes[holding.HoldingSymbol]
			originalBase, hasOriginalBase := dailyPriceAt(data, holding.HoldingSymbol, baseDate)
			referenceQuote, hasReference := quoteForSymbol(quotes, reference.Symbol)
			referenceBase, hasReferenceBase := dailyPriceAt(data, reference.Symbol, baseDate)
			if !hasOriginal || !hasOriginalBase || !hasReference ||
				!hasReferenceBase ||
				originalQuote.Price <= 0 || originalBase <= 0 ||
				referenceQuote.Price <= 0 || referenceBase <= 0 ||
				isDemoQuote(originalQuote) || isDemoQuote(referenceQuote) {
				continue
			}

			officialRelative := officialQuotePrice(originalQuote) / originalBase
			fairRelative := officialRelative * (referenceQuote.Price / referenceBase)
			if holding.FXAdjust > 0 {
				fairRelative /= holding.FXAdjust
			}
			officialWeightedRatio += holding.Ratio * officialRelative
			fairWeightedRatio += holding.Ratio * fairRelative
			totalRatio += holding.Ratio
			used++
			replacementUsed++
			if !seenReferences[reference.Label] {
				seenReferences[reference.Label] = true
				references = append(references, reference.Label)
			}
			continue
		}

		currentQuote, hasCurrent := quotes[holding.HoldingSymbol]
		basePrice, hasBase := dailyPriceAt(data, holding.HoldingSymbol, baseDate)
		if !hasCurrent || currentQuote.Price <= 0 || !hasBase || basePrice <= 0 || isDemoQuote(currentQuote) {
			continue
		}
		fairRelative := currentQuote.Price / basePrice
		officialRelative := officialQuotePrice(currentQuote) / basePrice
		if holding.FXAdjust > 0 {
			fairRelative /= holding.FXAdjust
		}
		officialWeightedRatio += holding.Ratio * officialRelative
		fairWeightedRatio += holding.Ratio * fairRelative
		totalRatio += holding.Ratio
		used++
	}
	if replacementUsed == 0 || used == 0 || totalRatio <= 0 {
		return domain.EstimateRow{}, false
	}

	officialChange := officialWeightedRatio/totalRatio - 1
	fairChange := fairWeightedRatio/totalRatio - 1
	official := baseNAV.NAV * (1 + officialChange*position)
	fair := baseNAV.NAV * (1 + fairChange*position)
	var realtime *float64
	if domain.IsManualRealtimeFund(quote.Symbol) {
		realtime = &fair
	}
	note := fmt.Sprintf("持仓基准日 %s，净值基准日 %s，使用商品期货 %s 替代 GLD/SLV/USO 中的可实时部分，期货基准取净值日对应美股收盘时点价格；使用 %d/%d 个持仓标的",
		holdingDate.Date,
		baseDate,
		strings.Join(references, "/"),
		used,
		len(holdings),
	)
	row := buildRow(quote, official, fair, realtime, now, "v1.commodity_futures.cn", note)
	row.EffectiveRatio = round6(position)
	if len(references) == 1 {
		if referenceSymbol, ok := singleCommodityFutureReferenceSymbol(references[0]); ok {
			row.ReferenceSymbol = referenceSymbol
		}
	}
	return row, true
}

func (e *Engine) estimateFromSingleCommodityFuture(
	quote domain.Quote,
	quotes map[string]domain.Quote,
	data domain.ValuationData,
	now time.Time,
	holdingDate string,
	holdings []domain.Holding,
	baseNAV domain.NetValue,
	baseDate string,
	strategy domain.SingleCommodityFutureStrategy,
) (domain.EstimateRow, bool) {
	referenceQuote, ok := quoteForSymbol(quotes, strategy.ReferenceSymbol)
	if !ok || referenceQuote.Price <= 0 || isDemoQuote(referenceQuote) {
		return domain.EstimateRow{}, false
	}
	referenceBase, ok := dailyPriceAt(data, strategy.ReferenceSymbol, baseDate)
	if !ok || referenceBase <= 0 {
		return domain.EstimateRow{}, false
	}
	fxDivisor := 1.0
	fxSource := ""
	for _, holding := range holdings {
		if holding.HoldingDate != holdingDate {
			continue
		}
		if strategy.FXHoldingSymbol != "" && !strings.EqualFold(holding.HoldingSymbol, strategy.FXHoldingSymbol) {
			continue
		}
		if holding.FXAdjust > 0 {
			fxDivisor = holding.FXAdjust
			fxSource = holding.HoldingSymbol
			break
		}
	}
	fxRatio := 1.0
	if fxDivisor > 0 {
		fxRatio = 1 / fxDivisor
	}
	investmentRatio, _, _ := domain.EffectiveRatio(data, quote.Symbol)
	if investmentRatio <= 0 {
		investmentRatio = strategy.InvestmentRatio
	}
	staticRatio := 1 - investmentRatio
	fair := baseNAV.NAV * (staticRatio + investmentRatio*(referenceQuote.Price/referenceBase)) * fxRatio
	var realtime *float64
	if domain.IsManualRealtimeFund(quote.Symbol) {
		realtime = &fair
	}
	note := fmt.Sprintf(
		"净值基准日 %s，使用 %s 单锚点期货估值，仓位 %.2f，静态 %.4f，期货基准取净值日对应美股收盘时点价格",
		baseDate,
		strategy.Label,
		investmentRatio,
		staticRatio,
	)
	if fxSource != "" {
		note += fmt.Sprintf("；FX 使用 %s 页面 adjust", fxSource)
	}
	row := buildRow(quote, baseNAV.NAV, fair, realtime, now, "v1.commodity_futures.single.cn", note)
	row.EffectiveRatio = round6(investmentRatio)
	row.ReferenceSymbol = strategy.ReferenceSymbol
	return row, true
}

func (e *Engine) estimateFromHoldings(quote domain.Quote, quotes map[string]domain.Quote, data domain.ValuationData, now time.Time) (domain.EstimateRow, bool) {
	holdingDate, ok := data.CurrentHoldingDates[quote.Symbol]
	if !ok || holdingDate.Date == "" {
		return domain.EstimateRow{}, false
	}
	holdings := data.Holdings[quote.Symbol]
	if len(holdings) == 0 {
		return domain.EstimateRow{}, false
	}
	baseNAV, ok := ValuationBaseNAV(data, quote.Symbol, holdingDate.Date, now)
	if !ok || baseNAV.NAV <= 0 {
		return domain.EstimateRow{}, false
	}
	baseDate := baseNAV.Date

	position := positionOf(data, quote.Symbol)
	officialWeightedRatio := 0.0
	fairWeightedRatio := 0.0
	totalRatio := 0.0
	used := 0
	fxAdjusted := 0
	skippedDemo := 0
	currentHoldingCount := 0
	for _, holding := range holdings {
		if holding.HoldingDate != holdingDate.Date || holding.Ratio <= 0 {
			continue
		}
		currentHoldingCount++
		currentQuote, hasCurrent := quotes[holding.HoldingSymbol]
		basePrice, hasBase := dailyPriceAt(data, holding.HoldingSymbol, baseDate)
		if !hasCurrent || currentQuote.Price <= 0 || !hasBase || basePrice <= 0 {
			continue
		}
		if isDemoQuote(currentQuote) {
			skippedDemo++
			continue
		}
		fairRelative := currentQuote.Price / basePrice
		officialRelative := officialQuotePrice(currentQuote) / basePrice
		if holding.FXAdjust > 0 {
			fairRelative /= holding.FXAdjust
			fxAdjusted++
		}
		officialWeightedRatio += holding.Ratio * officialRelative
		fairWeightedRatio += holding.Ratio * fairRelative
		totalRatio += holding.Ratio
		used++
	}
	if used == 0 || totalRatio <= 0 {
		return domain.EstimateRow{}, false
	}

	officialChange := officialWeightedRatio/totalRatio - 1
	fairChange := fairWeightedRatio/totalRatio - 1
	official := baseNAV.NAV * (1 + officialChange*position)
	fair := baseNAV.NAV * (1 + fairChange*position)
	fxNote := "未使用 FX 调整"
	if fxAdjusted > 0 {
		fxNote = fmt.Sprintf("使用 %d 个标的的页面 FX 调整", fxAdjusted)
	}
	note := fmt.Sprintf(
		"持仓基准日 %s，净值基准日 %s，使用 %d/%d 个持仓标的；%s；有效仓位 %.2f%%，静态资产 %.2f%%",
		holdingDate.Date,
		baseDate,
		used,
		currentHoldingCount,
		fxNote,
		position*100,
		(1-position)*100,
	)
	if skippedDemo > 0 {
		note += fmt.Sprintf("；跳过 %d 个 demo 占位行情", skippedDemo)
	}
	var realtime *float64
	if domain.IsManualRealtimeFund(quote.Symbol) {
		realtime = &fair
	}
	row := buildRow(quote, official, fair, realtime, now, "v1.holdings.cn", note)
	row.EffectiveRatio = round6(position)
	return row, true
}

func (e *Engine) estimateFromFundPair(quote domain.Quote, quotes map[string]domain.Quote, data domain.ValuationData, now time.Time) (domain.EstimateRow, bool) {
	pairs := data.FundPairs[quote.Symbol]
	if len(pairs) == 0 {
		return domain.EstimateRow{}, false
	}
	for _, pair := range pairs {
		pairQuote, ok := quoteForSymbol(quotes, pair.PairSymbol)
		if !ok || pairQuote.Price <= 0 || isDemoQuote(pairQuote) {
			continue
		}
		factor, baseValue, factorSource, ok := e.fundPairFactor(quote, pair, pairQuote, data, now)
		if !ok || factor <= 0 {
			continue
		}
		raw := pairQuote.Price / factor
		fair := adjustPosition(positionOf(data, quote.Symbol), raw, baseValue)
		official := officialFromFundPair(data, quote.Symbol, pair.PairSymbol, factor, baseValue, now)
		if official <= 0 {
			official = valuationNAVOr(data, quote.Symbol, now, fair)
		}
		note := fmt.Sprintf("配对标的 %s，factor=%s，仓位=%.4f", pair.PairSymbol, trimFloat(factor), positionOf(data, quote.Symbol))
		var realtime *float64
		if domain.IsManualRealtimeFund(quote.Symbol) {
			realtime = &fair
		}
		row := buildRow(quote, official, fair, realtime, now, "v1.fundpair."+factorSource, note)
		row.ReferenceSymbol = pair.PairSymbol
		return row, true
	}
	return domain.EstimateRow{}, false
}

func quoteForSymbol(quotes map[string]domain.Quote, symbol string) (domain.Quote, bool) {
	if quote, ok := quotes[symbol]; ok {
		return quote, true
	}
	lower := strings.ToLower(symbol)
	if strings.HasPrefix(lower, "znb_") && len(symbol) > 4 {
		suffix := strings.ToUpper(symbol[4:])
		if quote, ok := quotes["znb_"+suffix]; ok {
			return quote, true
		}
		if quote, ok := quotes["ZNB_"+suffix]; ok {
			return quote, true
		}
	}
	return domain.Quote{}, false
}

func singleCommodityFutureReferenceSymbol(label string) (string, bool) {
	switch label {
	case "MGC":
		return "HF_GC", true
	case "SI":
		return "HF_SI", true
	case "CL":
		return "HF_CL", true
	default:
		return "", false
	}
}

func isDemoQuote(quote domain.Quote) bool {
	return quote.Source == "demo" || quote.Error == "demo fallback"
}

func officialQuotePrice(quote domain.Quote) float64 {
	if quote.Source == "sina_us_after_hours" && quote.PrevClose > 0 {
		return quote.PrevClose
	}
	source := strings.ToLower(strings.TrimSpace(quote.Source))
	session := strings.ToLower(strings.TrimSpace(quote.QuoteSession))
	if (strings.HasPrefix(source, "ibkr_us_") ||
		strings.HasPrefix(source, "upload:home-mac-ib") ||
		strings.Contains(session, "us_overnight") ||
		strings.Contains(session, "us_extended")) && quote.PrevClose > 0 {
		return quote.PrevClose
	}
	return quote.Price
}

func (e *Engine) fundPairFactor(quote domain.Quote, pair domain.FundPair, pairQuote domain.Quote, data domain.ValuationData, now time.Time) (factor float64, baseValue float64, source string, ok bool) {
	if cal, exists := data.LatestCalibrations[quote.Symbol]; exists && cal.Factor > 0 && calibrationAppliesToPair(cal, pair) && calibrationDateApplies(cal, now) {
		if calibrationMismatchesPairScale(cal, pairQuote) {
			return 0, 0, "", false
		}
		base := cal.BaseValue
		if base <= 0 {
			if nav, hasNAV := netValueAt(data, quote.Symbol, cal.Date); hasNAV {
				base = nav.NAV
			}
		}
		if base <= 0 {
			base = valuationNAVOr(data, quote.Symbol, now, quote.Price)
		}
		return cal.Factor, base, calibrationSourceName(cal), true
	}

	fundNAV, hasFundNAV := ValuationNetValue(data, quote.Symbol, now)
	if hasFundNAV {
		if pairNAV, hasPairNAV := netValueAt(data, pair.PairSymbol, fundNAV.Date); hasPairNAV && pairNAV.NAV > 0 && fundNAV.NAV > 0 {
			return pairNAV.NAV / fundNAV.NAV, fundNAV.NAV, "nav_factor", true
		}
		if pairDaily, hasPairDaily := dailyPriceAt(data, pair.PairSymbol, fundNAV.Date); hasPairDaily && pairDaily > 0 && fundNAV.NAV > 0 {
			return pairDaily / fundNAV.NAV, fundNAV.NAV, "daily_factor", true
		}
	}

	if quote.PrevClose > 0 && pairQuote.PrevClose > 0 {
		return pairQuote.PrevClose / quote.PrevClose, quote.PrevClose, "prev_close_bootstrap", true
	}
	if quote.Price > 0 && pairQuote.Price > 0 {
		return pairQuote.Price / quote.Price, quote.Price, "bootstrap", true
	}
	return 0, 0, "", false
}

func isStaticFundPair(pair domain.FundPair) bool {
	return strings.Contains(pair.PairType, "_static")
}

func calibrationAppliesToPair(cal domain.Calibration, pair domain.FundPair) bool {
	if pairSymbol, ok := autoDailyCalibrationPair(cal.Source); ok {
		return strings.EqualFold(pairSymbol, pair.PairSymbol)
	}
	return !isStaticFundPair(pair)
}

func calibrationDateApplies(cal domain.Calibration, now time.Time) bool {
	if strings.TrimSpace(cal.Date) == "" {
		return true
	}
	return dateOnOrBefore(cal.Date, MaxValuationBaseDate(now))
}

func calibrationSourceName(cal domain.Calibration) string {
	if _, ok := autoDailyCalibrationPair(cal.Source); ok {
		return "auto_daily"
	}
	return "calibrated"
}

func autoDailyCalibrationPair(source string) (string, bool) {
	source = strings.TrimSpace(source)
	const prefix = "auto_daily:"
	if !strings.HasPrefix(source, prefix) {
		return "", false
	}
	pair := strings.TrimSpace(strings.TrimPrefix(source, prefix))
	return pair, pair != ""
}

func calibrationMismatchesPairScale(cal domain.Calibration, pairQuote domain.Quote) bool {
	if cal.Factor <= 0 || pairQuote.Price <= 0 {
		return true
	}
	if cal.Factor >= 100 && pairQuote.Price < 100 {
		return true
	}
	if cal.Factor < 100 && pairQuote.Price >= 1000 {
		return true
	}
	return false
}

func (e *Engine) estimateFromLatestNAV(quote domain.Quote, data domain.ValuationData, now time.Time) (domain.EstimateRow, bool) {
	nav, ok := ValuationNetValue(data, quote.Symbol, now)
	if !ok || nav.NAV <= 0 {
		return domain.EstimateRow{}, false
	}
	return buildRow(quote, nav.NAV, nav.NAV, nil, now, "v1.netvalue.latest", "只存在估值基准日官方净值，未找到可用配对或持仓估值数据"), true
}

func officialFromFundPair(data domain.ValuationData, fundSymbol string, pairSymbol string, factor float64, fallbackBase float64, now time.Time) float64 {
	fundNAV, hasFundNAV := ValuationNetValue(data, fundSymbol, now)
	if !hasFundNAV || fundNAV.NAV <= 0 {
		return fallbackBase
	}
	position := positionOf(data, fundSymbol)
	if pairNAV, ok := netValueAt(data, pairSymbol, fundNAV.Date); ok && pairNAV.NAV > 0 {
		return adjustPosition(position, pairNAV.NAV/factor, fundNAV.NAV)
	}
	if pairDaily, ok := dailyPriceAt(data, pairSymbol, fundNAV.Date); ok && pairDaily > 0 {
		return adjustPosition(position, pairDaily/factor, fundNAV.NAV)
	}
	return fundNAV.NAV
}

func estimateFromQuote(quote domain.Quote, now time.Time, modelVersion string, note string) domain.EstimateRow {
	return buildRow(quote, quote.Price, quote.Price, nil, now, modelVersion, note)
}

func buildRow(quote domain.Quote, official float64, fair float64, realtime *float64, now time.Time, modelVersion string, note string) domain.EstimateRow {
	row := domain.EstimateRow{
		Symbol:          quote.Symbol,
		Name:            quote.Name,
		MarketPrice:     quote.Price,
		OfficialEst:     round6(official),
		FairEst:         round6(fair),
		OfficialPremium: premium(quote.Price, official),
		FairPremium:     premium(quote.Price, fair),
		EstimateDate:    now.Format("2006-01-02"),
		ModelVersion:    modelVersion,
		Note:            note,
	}
	if realtime != nil {
		realtimeEst := round6(*realtime)
		realtimePremium := premium(quote.Price, realtimeEst)
		row.RealtimeEst = &realtimeEst
		row.RealtimePremium = &realtimePremium
	}
	return row
}

func ResolveWeightedAnchorSet(data domain.ValuationData, strategy domain.WeightedAnchorStrategy, anchorDate string) (domain.ValuationAnchorPriceSet, []string, bool) {
	if exact, ok := data.ValuationAnchors[domain.ValuationAnchorSetKey(strategy.FundSymbol, anchorDate, strategy.ReferenceSymbol)]; ok {
		// Backward compatibility: older snapshots only persisted the aggregate weighted price.
		if len(exact.Points) == 0 && exact.WeightedPrice > 0 && exact.CoverageWeight+1e-9 >= exact.RequiredWeight && exact.RequiredWeight > 0 {
			return exact, nil, true
		}
	}
	set := domain.ValuationAnchorPriceSet{
		FundSymbol:      strategy.FundSymbol,
		AnchorDate:      anchorDate,
		ReferenceSymbol: strategy.ReferenceSymbol,
	}
	warnings := make([]string, 0)
	weighted := 0.0
	coverage := 0.0
	for _, point := range strategy.Points {
		set.RequiredWeight += point.Weight
		resolved, actualDate, ok := resolveWeightedAnchorPoint(data, strategy.FundSymbol, strategy.ReferenceSymbol, point.Key, anchorDate, weightedAnchorCarryForwardDays)
		if !ok || resolved.Price <= 0 || point.Weight <= 0 {
			return domain.ValuationAnchorPriceSet{}, nil, false
		}
		weighted += resolved.Price * point.Weight
		coverage += point.Weight
		set.Points = append(set.Points, resolved)
		if actualDate != anchorDate {
			warnings = append(warnings, fmt.Sprintf("%s %s uses %s for %s", strategy.ReferenceSymbol, point.Key, actualDate, anchorDate))
		}
	}
	if coverage <= 0 {
		return domain.ValuationAnchorPriceSet{}, nil, false
	}
	set.WeightedPrice = weighted / coverage
	set.CoverageWeight = coverage
	return set, warnings, true
}

func resolveWeightedAnchorPoint(data domain.ValuationData, fundSymbol string, referenceSymbol string, anchorKey string, anchorDate string, maxDays int) (domain.ValuationAnchorPrice, string, bool) {
	if point, ok := exactWeightedAnchorPoint(data, fundSymbol, referenceSymbol, anchorKey, anchorDate); ok {
		return point, anchorDate, true
	}
	day, err := time.Parse("2006-01-02", strings.TrimSpace(anchorDate))
	if err != nil {
		return domain.ValuationAnchorPrice{}, "", false
	}
	for offset := 1; offset <= maxDays; offset++ {
		candidate := day.AddDate(0, 0, -offset).Format("2006-01-02")
		if point, ok := exactWeightedAnchorPoint(data, fundSymbol, referenceSymbol, anchorKey, candidate); ok {
			return point, candidate, true
		}
	}
	return domain.ValuationAnchorPrice{}, "", false
}

func exactWeightedAnchorPoint(data domain.ValuationData, fundSymbol string, referenceSymbol string, anchorKey string, anchorDate string) (domain.ValuationAnchorPrice, bool) {
	setKey := domain.ValuationAnchorSetKey(fundSymbol, anchorDate, referenceSymbol)
	set, ok := data.ValuationAnchors[setKey]
	if !ok || len(set.Points) == 0 {
		return domain.ValuationAnchorPrice{}, false
	}
	best := domain.ValuationAnchorPrice{}
	found := false
	for _, point := range set.Points {
		if point.AnchorKey != anchorKey || point.Price <= 0 {
			continue
		}
		if !found || weightedAnchorPointIsNewer(point, best) {
			best = point
			found = true
		}
	}
	return best, found
}

func weightedAnchorPointIsNewer(candidate domain.ValuationAnchorPrice, current domain.ValuationAnchorPrice) bool {
	if !candidate.ObservedAt.IsZero() {
		if current.ObservedAt.IsZero() {
			return true
		}
		if candidate.ObservedAt.After(current.ObservedAt) {
			return true
		}
		if candidate.ObservedAt.Before(current.ObservedAt) {
			return false
		}
	}
	if !candidate.TargetAt.IsZero() {
		if current.TargetAt.IsZero() {
			return true
		}
		if candidate.TargetAt.After(current.TargetAt) {
			return true
		}
		if candidate.TargetAt.Before(current.TargetAt) {
			return false
		}
	}
	return false
}

func premium(price float64, estimate float64) float64 {
	if estimate <= 0 {
		return 0
	}
	return round6((price/estimate - 1) * 100)
}

func positionOf(data domain.ValuationData, symbol string) float64 {
	ratio, _, _ := domain.EffectiveRatio(data, symbol)
	if ratio > 0 {
		return ratio
	}
	return 1
}

func adjustPosition(position float64, value float64, baseValue float64) float64 {
	if position <= 0 {
		position = 1
	}
	if baseValue <= 0 {
		baseValue = value
	}
	return position*value + (1-position)*baseValue
}

func latestNetValue(data domain.ValuationData, symbol string) (domain.NetValue, bool) {
	nav, ok := data.LatestNetValues[symbol]
	return nav, ok
}

func latestNAVOr(data domain.ValuationData, symbol string, fallback float64) float64 {
	if nav, ok := latestNetValue(data, symbol); ok && nav.NAV > 0 {
		return nav.NAV
	}
	return fallback
}

func valuationNAVOr(data domain.ValuationData, symbol string, now time.Time, fallback float64) float64 {
	if nav, ok := ValuationNetValue(data, symbol, now); ok && nav.NAV > 0 {
		return nav.NAV
	}
	return fallback
}

func ValuationBaseNAV(data domain.ValuationData, symbol string, fallbackDate string, now time.Time) (domain.NetValue, bool) {
	if nav, ok := ValuationNetValue(data, symbol, now); ok && nav.NAV > 0 && nav.Date != "" {
		return nav, true
	}
	if fallbackDate == "" || !dateOnOrBefore(fallbackDate, MaxValuationBaseDate(now)) {
		return domain.NetValue{}, false
	}
	return netValueAt(data, symbol, fallbackDate)
}

func ValuationNetValue(data domain.ValuationData, symbol string, now time.Time) (domain.NetValue, bool) {
	maxDate := MaxValuationBaseDate(now)
	if nav, ok := netValueOnOrBefore(data, symbol, maxDate); ok {
		return nav, true
	}
	if nav, ok := latestNetValue(data, symbol); ok && nav.NAV > 0 && nav.Date != "" && dateOnOrBefore(nav.Date, maxDate) {
		return nav, true
	}
	return domain.NetValue{}, false
}

func netValueOnOrBefore(data domain.ValuationData, symbol string, maxDate string) (domain.NetValue, bool) {
	byDate := data.NetValuesByDate[symbol]
	if byDate == nil {
		return domain.NetValue{}, false
	}
	target, err := time.Parse("2006-01-02", strings.TrimSpace(maxDate))
	if err != nil {
		return netValueAt(data, symbol, maxDate)
	}
	var bestDay time.Time
	var best domain.NetValue
	for dateText, nav := range byDate {
		if nav.NAV <= 0 {
			continue
		}
		candidateDate := strings.TrimSpace(nav.Date)
		if candidateDate == "" {
			candidateDate = strings.TrimSpace(dateText)
		}
		day, err := time.Parse("2006-01-02", candidateDate)
		if err != nil || day.After(target) {
			continue
		}
		if best.NAV == 0 || day.After(bestDay) {
			bestDay = day
			best = nav
			if best.Date == "" {
				best.Date = candidateDate
			}
		}
	}
	return best, best.NAV > 0
}

func MaxValuationBaseDate(now time.Time) string {
	tradeDate := valuationTradeDate(now)
	cursor := tradeDate
	for remaining := 2; remaining > 0; {
		cursor = cursor.AddDate(0, 0, -1)
		if isShanghaiWeekday(cursor) {
			remaining--
		}
	}
	return cursor.Format("2006-01-02")
}

func valuationTradeDate(now time.Time) time.Time {
	loc, err := time.LoadLocation("Asia/Shanghai")
	if err != nil {
		loc = now.Location()
	}
	local := now.In(loc)
	day := time.Date(local.Year(), local.Month(), local.Day(), 0, 0, 0, 0, loc)
	for !isShanghaiWeekday(day) {
		day = day.AddDate(0, 0, 1)
	}
	return day
}

func isShanghaiWeekday(day time.Time) bool {
	return day.Weekday() >= time.Monday && day.Weekday() <= time.Friday
}

func dateOnOrBefore(dateText string, maxDate string) bool {
	dateText = strings.TrimSpace(dateText)
	maxDate = strings.TrimSpace(maxDate)
	if dateText == "" || maxDate == "" {
		return false
	}
	day, err := time.Parse("2006-01-02", dateText)
	if err != nil {
		return dateText <= maxDate
	}
	target, err := time.Parse("2006-01-02", maxDate)
	if err != nil {
		return dateText <= maxDate
	}
	return !day.After(target)
}

func netValueAt(data domain.ValuationData, symbol string, date string) (domain.NetValue, bool) {
	byDate := data.NetValuesByDate[symbol]
	if byDate == nil {
		return domain.NetValue{}, false
	}
	nav, ok := byDate[date]
	return nav, ok
}

func dailyPriceAt(data domain.ValuationData, symbol string, date string) (float64, bool) {
	byDate := data.DailyPricesByDate[symbol]
	if byDate == nil {
		return 0, false
	}
	price, ok := byDate[date]
	if !ok {
		return 0, false
	}
	if price.AdjClose > 0 {
		return price.AdjClose, true
	}
	return price.Close, price.Close > 0
}

func fxRatesForWeightedAnchor(data domain.ValuationData, strategy domain.WeightedAnchorStrategy, baseDate string, now time.Time) (float64, float64, string, bool) {
	return fxRatesForPair(data, strategy.FXPair, baseDate, now)
}

func fxRatesForPair(data domain.ValuationData, pair string, baseDate string, now time.Time) (float64, float64, string, bool) {
	pair = strings.ToUpper(strings.TrimSpace(pair))
	if pair == "" {
		return 1, 1, "", true
	}
	currentDate := chinaTradeDate(now)
	baseFX, ok := centralParityRateAt(data, pair, baseDate)
	if !ok || baseFX <= 0 {
		return 0, 0, pair, false
	}
	currentFX, ok := centralParityRateOnOrBefore(data, pair, currentDate)
	if !ok || currentFX <= 0 {
		return 0, 0, pair, false
	}
	return currentFX, baseFX, pair, true
}

func centralParityRateAt(data domain.ValuationData, pair string, rateDate string) (float64, bool) {
	byDate := data.FXCentralParity[strings.ToUpper(strings.TrimSpace(pair))]
	if byDate == nil {
		return 0, false
	}
	row, ok := byDate[strings.TrimSpace(rateDate)]
	if !ok {
		return 0, false
	}
	return row.Rate, row.Rate > 0
}

func centralParityRateOnOrBefore(data domain.ValuationData, pair string, rateDate string) (float64, bool) {
	byDate := data.FXCentralParity[strings.ToUpper(strings.TrimSpace(pair))]
	if byDate == nil {
		return 0, false
	}
	target, err := time.Parse("2006-01-02", strings.TrimSpace(rateDate))
	if err != nil {
		return centralParityRateAt(data, pair, rateDate)
	}
	var bestDate time.Time
	bestRate := 0.0
	for dateText, row := range byDate {
		if row.Rate <= 0 {
			continue
		}
		day, err := time.Parse("2006-01-02", strings.TrimSpace(dateText))
		if err != nil || day.After(target) {
			continue
		}
		if bestRate == 0 || day.After(bestDate) {
			bestDate = day
			bestRate = row.Rate
		}
	}
	return bestRate, bestRate > 0
}

func chinaTradeDate(now time.Time) string {
	loc, err := time.LoadLocation("Asia/Shanghai")
	if err != nil {
		return now.Format("2006-01-02")
	}
	return now.In(loc).Format("2006-01-02")
}

func round6(value float64) float64 {
	return math.Round(value*1_000_000) / 1_000_000
}

func trimFloat(value float64) string {
	return fmt.Sprintf("%.8g", value)
}
